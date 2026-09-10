"""butler-copytrade duty — mirrors LEADER's trades on the Virtuals rails, one
trade per leader event, never twice. Spot swaps, tokenized stocks and perps.
Every knob below is a word the owner actually said; nothing here has a money
default of its own. See SKILL.md.

Every leg builds its own `acp trade` command and files it with
`bevo.trade(command=…, idempotency_key=…)`, sizing off
`bevo.read("/user-assets", {"fresh": 1})`. Never drop that `fresh` flag: without
it the server may answer from cache with a PRE-trade balance for up to ten
minutes, and a burst of leader events sizes each leg off the one before it.
"""
import json
import math
import os

import bevo

# Every knob is read with .get(): `bevo-automation create` already refuses a
# duty missing LEADER or SIZING, so a KeyError here could only ever fire in an
# offline replay — and a duty that dies on import is a crash loop, not an error
# message. An unusable config skips every event and says so in the log instead.
LEADER = os.environ.get("LEADER") or ""
# fixed = SIZE_USD per copy | cash_share = SHARE of the owner's cash, read at
# trade time | leader_share = SHARE of what the leader just spent.
SIZING = os.environ.get("SIZING") or "fixed"
SIZE_USD = float(os.environ.get("SIZE_USD") or 0)
SHARE = float(os.environ.get("SHARE") or 0)
MAX_USD = float(os.environ.get("MAX_USD") or 0)
# [] means "wherever the leader traded" — trade.asset already carries the chain.
# Spot only: a perp has no chain and a stock's chain is its cash leg's, so a
# chain filter that touched either would silently drop every one of them.
CHAIN_IDS = [int(c) for c in json.loads(os.environ.get("CHAIN_IDS") or "[]")]
MIRROR_SELLS = str(os.environ.get("MIRROR_SELLS") or "false").lower() == "true"
MIN_LEADER_USD = float(os.environ.get("MIN_LEADER_USD") or 0)
MIRROR_PERPS = str(os.environ.get("MIRROR_PERPS") or "false").lower() == "true"
MIRROR_STOCKS = str(os.environ.get("MIRROR_STOCKS") or "false").lower() == "true"
# 0 = take the leader's own leverage on each trade.
PERP_LEVERAGE = float(os.environ.get("PERP_LEVERAGE") or 0)
PERP_MAX_LEVERAGE = float(os.environ.get("PERP_MAX_LEVERAGE") or 0)

# The venue minimums AGENTS.md names. A copy under one of them is not filed at
# all: the leader's size is theirs, the minimum is ours.
SPOT_MIN_USD = 2.0
PERP_MIN_USD = 15.0
STOCK_MIN_USD = 15.0


def _num(raw, default=0.0):
    """A value read off a JSON row: a missing or unparsable one is the
    caller's declared default, never a crash mid-batch."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def fmt(n):
    """A CLI number: never scientific notation, which the trade grammar cannot
    read back (0.00000012 must not become 1.2e-07)."""
    return f"{n:.8f}".rstrip("0").rstrip(".") or "0"


def same_address(a, b):
    """The SDK's `_same_address` rule, ported: an EVM address compares
    case-insensitively, anything else EXACTLY. A Solana mint is base58, where
    case carries information — lower-casing both sides would let one mint
    match a different one, and size a sell against a holding we do not have."""
    a = str(a or "")
    b = str(b or "")
    if not a or not b:
        return False
    if a.startswith("0x") and b.startswith("0x"):
        return a.lower() == b.lower()
    return a == b


def bad_amount(qty):
    """True for a computed quantity that must never reach `acp trade`: 0,
    negative, non-finite, or one so small it formats to "0" at 8 dp — the
    server would answer a parse error instead of a reason."""
    try:
        if not math.isfinite(qty) or qty <= 0:
            return True
    except TypeError:
        return True
    return fmt(qty) == "0"


def size_for(trade):
    """The USD to put into this copy, or 0 to skip it.

    `cash_share` reads the LIVE wallet on every event — the owner said "a
    share of my wallet", and a figure worked out when the duty was written is
    a different promise by the second trade. `leader_share` is a share of what
    the leader spent, which the event carries. `MAX_USD` caps all three, and
    only when the owner named a ceiling. On a perp this is the POSITION size
    (notional), never the margin — `usd_value` on the leader's row is notional
    too, so `leader_share` compares like with like."""
    if SIZING == "cash_share":
        balance = bevo.balance()
        if not balance.available or not balance.cash_usd:
            return 0.0
        size = balance.cash_usd * SHARE
    elif SIZING == "leader_share":
        size = (trade.usd_value or 0.0) * SHARE
    else:
        size = SIZE_USD
    return min(size, MAX_USD) if MAX_USD else size


def leverage_for(trade):
    """The leverage to open at: the owner's fixed figure when they named one,
    otherwise the leader's own on that trade. `PERP_MAX_LEVERAGE` caps it only
    when they named a ceiling. A leader's 40x is 40x of THEIR conviction on
    THEIR balance — an owner who did not say a number gets it verbatim, which
    is why the skill asks.

    The venue takes 1–50x and nothing else. A leader's 100x is a copy worth
    filing at the ceiling rather than dropping, so it clamps — but never
    silently, or the owner reads "60x" in the leader's feed and 50x in their
    own fills with nothing joining them."""
    lev = PERP_LEVERAGE or (trade.leverage or 1)
    if PERP_MAX_LEVERAGE:
        lev = min(lev, PERP_MAX_LEVERAGE)
    asked = int(lev)
    clamped = max(1, min(50, asked))
    if clamped != asked:
        bevo.log(
            f"copytrade event={trade.id}: leverage {asked}x is outside the venue's "
            f"1–50x range — clamped to {clamped}x"
        )
    return clamped


def too_small(trade, size, floor, kind):
    if size >= floor:
        return False
    bevo.log(f"copytrade skip event={trade.id}: ${size:.2f} is under the ${floor:.2f} {kind} minimum")
    return True


def status_of(result):
    """TradeResult exposes .status; a raw dict from a replay stub answers the
    same way — read whichever this is."""
    status = getattr(result, "status", None)
    if status is None and hasattr(result, "get"):
        status = result.get("status")
    return status


def read_assets(trade):
    """One `/user-assets` read for this leg — the holding, its price and (for
    a perp) the open position all come off the SAME snapshot, never a second
    fetch per lookup.

    `fresh=1` is load-bearing, and it is the whole reason the removed money
    verbs read `/butler-read/user-assets?fresh=1`: without it the server serves
    its stale-while-revalidate portfolio cache, which can be up to ten minutes
    old. A leader's burst puts several legs through this duty back to back, and
    a cached read sizes leg N off the balance from BEFORE leg N-1 settled —
    selling a quantity already sold, or spending cash already spent.

    None means unreadable: the caller must skip, not guess."""
    try:
        body = bevo.read("/user-assets", {"fresh": 1})
    except bevo.BevoError as exc:
        bevo.log(f"copytrade event={trade.id}: /user-assets unreadable ({exc}) — skipping this leg")
        return None
    # Guarded ONCE here, so every reader below (spot_holding, perp_position,
    # stock_holding) can call .get() on it: a non-dict body — an error envelope,
    # a list, a null — is not a wallet with nothing in it, and must never read
    # as "holds none of this" and let a sell size itself off a guess.
    if not isinstance(body, dict):
        bevo.log(f"copytrade event={trade.id}: /user-assets answered no object — skipping this leg")
        return None
    return body


def spot_holding(assets, ref, chain_id):
    """The owner's holding row for `ref` (an address, matched by the SDK's own
    EVM-vs-Solana rule — see `same_address`).

    A sell settles on ONE chain: with `chain_id` set (the leader's own), only
    that chain's row counts; with it unset, the largest row across chains is
    the one whose OWN chain the sell carries — never a total summed across
    chains, which would size a quantity no one chain actually holds."""
    spot = assets.get("spot") or {}
    if not spot.get("available"):
        return None
    rows = [row for row in spot.get("tokens") or [] if same_address(row.get("tokenAddress"), ref)]
    if chain_id is not None:
        rows = [row for row in rows if str(row.get("chainId") or "") == str(chain_id)]
    if not rows:
        return None
    return max(rows, key=lambda row: _num(row.get("balance")))


def search_price(query, address=None, symbol=None):
    """A `/token-search` price for `query`, pinned to a row that is really the
    asset asked for: by `address` when we have one, by `symbol` on a ticker
    retry. None when the catalog cannot price it — never the first row that
    happens to wear the ticker."""
    try:
        body = bevo.read("/token-search", {"q": query})
    except bevo.BevoError as exc:
        bevo.log(f"copytrade: /token-search unreadable for {query} ({exc})")
        return None
    for hit in (body if isinstance(body, dict) else {}).get("tokens") or []:
        if address is not None and not same_address(hit.get("address"), address):
            continue
        if symbol is not None and str(hit.get("symbol") or "").strip().upper() != symbol:
            continue
        price = _num(hit.get("priceUsd"))
        if price > 0:
            return price
    return None


def spot_price(ref, row, symbol=None):
    """The live price for `ref`, in order: the holding's own `usdPrice`,
    then a `/token-search` quote matched on the
    ADDRESS, then the same quote asked for the SYMBOL.

    Each step FALLS THROUGH on a falsy price rather than returning None — a row
    that carries `usdPrice: 0` (or null, on a chain whose price feed is down)
    is a missing price, not a price of zero, and returning there would refuse a
    sell the catalog can price perfectly well one call later. The symbol retry
    is the old `_symbol_fallback`: an address the catalog does not index — a
    fresh listing, a chain it does not cover — still prices by its ticker.

    None means genuinely unknown: the caller must not trade, never guess a
    quantity from it."""
    if row is not None:
        held_price = _num(row.get("usdPrice"))
        if held_price > 0:
            return held_price
    price = search_price(ref, address=ref)
    if price:
        return price
    ticker = str(symbol or "").strip().upper()
    if ticker and ticker != str(ref or "").strip().upper():
        return search_price(ticker, symbol=ticker)
    return None


def perp_position(assets, coin):
    """The owner's open position in `coin` (`size > 0`), or None.

    A HIP-3 coin is namespaced (`xyz:AAPL`): match the FULL coin first, or a
    close on a HIP-3 market would find nothing. The bare `:`-suffix fallback
    is only for a plain coin like `BTC` — stripping the dex segment off BOTH
    sides instead would let `xyz:AAPL` close `abc:AAPL`, a different market."""
    perps = assets.get("perps") or {}
    if not perps.get("available"):
        return None
    for row in perps.get("positions") or []:
        if not _num(row.get("size")) > 0:
            continue
        rowcoin = str(row.get("coin") or "").upper()
        if rowcoin == coin or rowcoin.split(":")[-1] == coin:
            return row
    return None


def stock_holding(assets, ticker):
    """The owner's tokenized-stock position in `ticker`, or None.

    A stock is NOT in `spot.tokens` — it is its own `spot.stocks` array, and
    the two disagree on purpose (`tokens` is the raw on-chain balance,
    `shares` is what the venue sells). Sizing a stock sell off a look-alike
    token row would sell the wrong quantity, so this reads the stock row or
    refuses."""
    spot = assets.get("spot") or {}
    if not spot.get("available"):
        return None
    for row in spot.get("stocks") or []:
        if str(row.get("ticker") or "").strip().upper() == ticker:
            return row
    return None


def stock_price(row, ticker):
    """What one share of `ticker` is worth, in order: the holding's own
    `usdPerShare`, then the SAME
    row's `usdValueUsd / shares` — a row that carries a value and a share count
    prices itself and needs no round trip — then a live quote.

    `usdPerShare` is nullable on every venue, so refusing on it alone stops the
    sells of a position the owner plainly holds and whose value the same row
    states. None means genuinely unpriceable: only then does the caller
    refuse."""
    per_share = _num(row.get("usdPerShare"))
    if per_share > 0:
        return per_share
    shares = _num(row.get("shares"))
    value = _num(row.get("usdValueUsd"))
    if shares > 0 and value > 0:
        return value / shares
    return search_price(ticker, symbol=ticker)


def copy_perp(trade, key):
    """A perp leg. A CLOSE is risk coming off and is never gated by a size
    knob, MIN_LEADER_USD or CHAIN_IDS: if the leader is out — by their own
    hand, a stop, or a liquidation the exchange forced — the copy gets out
    too. No open position in our own book is a no-op, not an error: the leg
    is simply skipped and the batch moves on."""
    coin = trade.asset.ref  # symbol only — a perp has no address

    if trade.is_close:
        assets = read_assets(trade)
        if assets is None:
            return
        pos = perp_position(assets, coin)
        if pos is None:
            bevo.log(f"copytrade event={trade.id}: no open position on {coin} — close is a no-op")
            return
        opposite = "short" if str(pos.get("side")) == "long" else "long"
        size = _num(pos.get("size"))
        command = f"acp trade --side {opposite} --token {coin} --size {fmt(size)} --reduce-only"
        result = bevo.trade(command=command, idempotency_key=key)
        bevo.log(
            f"copytrade event={trade.id} close {coin} ({trade.hl_event or 'by hand'}) "
            f"status={status_of(result)} key={key}"
        )
        return

    if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
        return
    size = size_for(trade)
    if size <= 0 or too_small(trade, size, PERP_MIN_USD, "perp"):
        return

    lev = leverage_for(trade)
    side = "short" if trade.is_short else "long"
    command = f"acp trade --side {side} --token {coin} --amount-usdc {fmt(size)} --leverage {lev}"
    result = bevo.trade(command=command, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} {side} {coin} ${size:.2f} at {lev}x status={status_of(result)} key={key}")


def is_stock_leg(trade):
    """Ask the catalog whether this ticker is a tokenized stock.

    A read failure is not an answer. `bevo.is_stock` goes out to token-search,
    and an exception escaping here would abort the whole batch — stranding a
    perp CLOSE queued behind this event, which is the one thing that must
    never be skipped. Log it and treat the leg as unresolvable instead."""
    try:
        return bevo.is_stock(trade.asset.symbol)
    except bevo.BevoError as err:
        bevo.log(f"copytrade skip event={trade.id}: stock lookup failed ({err})")
        return False


def copy_stock(trade, key):
    """A tokenized stock. It rides in on a SWAP row that names a ticker and no
    address, so it needs the stock command shape (`--token <TICKER>`, no
    `--side`) — never the swap shape a spot token takes. A sell's share count
    and venue come from our own holding, never worked out from the leader's."""
    if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
        return
    size = size_for(trade)
    if size <= 0:
        bevo.log(f"copytrade skip event={trade.id}: size 0 (LEADER={LEADER} SIZING={SIZING})")
        return

    ticker = trade.asset.symbol

    if trade.is_sell:
        assets = read_assets(trade)
        if assets is None:
            return
        row = stock_holding(assets, ticker)
        if row is None:
            bevo.log(f"copytrade skip event={trade.id}: no {ticker} stock holding to sell")
            return
        # The VENUE name (eth|sol), never a chain id: bevo-server reroutes a
        # numeric --chain onto a bare-symbol spot swap — a different asset.
        venue = str(row.get("chain") or "").strip()
        if not venue or venue.isdigit():
            bevo.log(f"copytrade skip event={trade.id}: the {ticker} holding names no venue")
            return
        owned = _num(row.get("shares"))
        price = stock_price(row, ticker)
        if not price:
            bevo.log(f"copytrade skip event={trade.id}: no price for {ticker} — cannot size the sell")
            return
        shares = min(size / price, owned)
        shares = math.floor(shares * 1e8) / 1e8  # floor, never round up — a rounded-up qty is rejected
        if bad_amount(shares):
            bevo.log(f"copytrade skip event={trade.id}: computed stock sell qty is 0 for {ticker}")
            return
        command = f"acp trade --token {ticker} --amount-shares {fmt(shares)} --chain {venue}"
    else:
        if too_small(trade, size, STOCK_MIN_USD, "stock buy"):
            return
        command = f"acp trade --token {ticker} --amount-usdc {fmt(size)}"

    result = bevo.trade(command=command, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} stock {ticker} ${size:.2f} status={status_of(result)} key={key}")


def copy_spot(trade, key):
    if CHAIN_IDS and trade.chain_id not in CHAIN_IDS:
        return
    if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
        return
    size = size_for(trade)
    if size <= 0:
        bevo.log(f"copytrade skip event={trade.id}: size 0 (LEADER={LEADER} SIZING={SIZING})")
        return
    if too_small(trade, size, SPOT_MIN_USD, "spot"):
        return

    # trade.asset is the exact token on the exact chain the leader traded —
    # its address verbatim plus the feed's own chain id. Never trade.token,
    # which is a display name, and never a chain of our own.
    ref = trade.asset.ref

    if trade.is_sell:
        assets = read_assets(trade)
        if assets is None:
            return
        row = spot_holding(assets, ref, trade.asset.chain_id)
        if row is None:
            bevo.log(f"copytrade skip event={trade.id}: nothing held of {trade.asset} to sell")
            return
        price = spot_price(ref, row, trade.asset.symbol)
        if not price:
            bevo.log(f"copytrade skip event={trade.id}: no price for {trade.asset} — cannot size the sell")
            return
        held = _num(row.get("balance"))
        qty = min(size / price, held)
        if bad_amount(qty):
            bevo.log(f"copytrade skip event={trade.id}: computed sell qty is 0 for {trade.asset}")
            return
        chain = row.get("chainId")
        chain_flag = f" --chain-in {chain}" if chain not in (None, "") else ""
        command = f"acp trade --token-in {ref}{chain_flag} --amount-in {fmt(qty)} --token-out usdc"
    else:
        chain_flag = f" --chain-out {trade.asset.chain_id}" if trade.asset.chain_id else ""
        command = f"acp trade --token-in usdc --amount-in {fmt(size)} --token-out {ref}{chain_flag}"

    result = bevo.trade(command=command, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} {trade.asset} ${size:.2f} status={status_of(result)} key={key}")


def main():
    for trade in bevo.trades():
        # The trigger already scopes the feed to LEADER (SKILL.md, Duty
        # procedure): a second owner check here would drop every event when
        # the owner gave a wallet and the feed reports a principal id.
        #
        # One key per LEADER EVENT, never a timestamp: the service pump can
        # replay rows it already handed over after a restart, and the same key
        # answers "already filed" instead of trading twice.
        key = f"copytrade:{bevo.SERVICE_ID}:{trade.id}"

        if trade.is_perp:
            if MIRROR_PERPS:
                copy_perp(trade, key)
            continue

        if trade.is_sell:
            if not MIRROR_SELLS:
                continue
        elif not trade.is_buy:
            continue  # a row the feed could not classify

        if trade.asset.ref is None:
            bevo.log(f"copytrade skip event={trade.id}: the feed named no token")
            continue

        # A leg with no ADDRESS is a bare ticker: a tokenized stock, or a
        # symbol the feed could not resolve to a contract. NEVER guess it onto
        # the spot rail — `acp trade --token-out AAPL` with no address is a
        # bare-symbol swap that resolves to whatever token happens to wear
        # that ticker, which is someone else's asset. Ask the catalog only
        # when the owner asked for stocks: token-search hides tokenized
        # stocks from an owner whose region does not permit them, so a
        # `False` here can mean "not a stock" OR "not yours to trade", and
        # both end the same way — skip, never substitute.
        if trade.asset.address is None:
            if MIRROR_STOCKS and is_stock_leg(trade):
                copy_stock(trade, key)
            else:
                bevo.log(f"copytrade skip event={trade.id}: {trade.asset.symbol} names no address and is not a stock we can trade")
            continue

        copy_spot(trade, key)


if __name__ == "__main__":
    main()
