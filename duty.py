"""butler-copytrade duty — mirrors LEADER's trades on the Virtuals rails, one
trade per leader event, never twice. Spot swaps, tokenized stocks and perps.
Every knob below is a word the owner actually said; nothing here has a money
default of its own. See SKILL.md.
"""
import json
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
    is why the skill asks."""
    lev = PERP_LEVERAGE or (trade.leverage or 1)
    if PERP_MAX_LEVERAGE:
        lev = min(lev, PERP_MAX_LEVERAGE)
    return max(1, int(lev))


def too_small(trade, size, floor, kind):
    if size >= floor:
        return False
    bevo.log(f"copytrade skip event={trade.id}: ${size:.2f} is under the ${floor:.2f} {kind} minimum")
    return True


def copy_perp(trade, key):
    """A perp leg. A CLOSE is risk coming off and is never gated by a size
    knob, MIN_LEADER_USD or CHAIN_IDS: if the leader is out — by their own
    hand, a stop, or a liquidation the exchange forced — the copy gets out
    too. bevo.close() reads our own side and size and refuses when we hold
    nothing, so a close we never opened is a no-op, not an error."""
    if trade.is_close:
        result = bevo.close(trade.asset.ref, idempotency_key=key)
        bevo.log(f"copytrade event={trade.id} close {trade.asset.ref} ({trade.hl_event or 'by hand'}) {result.summary} key={key}")
        return

    if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
        return
    size = size_for(trade)
    if size <= 0 or too_small(trade, size, PERP_MIN_USD, "perp"):
        return

    lev = leverage_for(trade)
    open_side = bevo.short if trade.is_short else bevo.long
    result = open_side(trade.asset.ref, usd=size, leverage=lev, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} {'short' if trade.is_short else 'long'} {trade.asset.ref} ${size:.2f} at {lev}x {result.summary} key={key}")


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
    address, so it needs the stock command shape — never the swap shape a
    spot token takes. bevo.stock_sell() resolves our own share count and venue
    from the holding and refuses when we hold none."""
    if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
        return
    size = size_for(trade)
    if size <= 0:
        bevo.log(f"copytrade skip event={trade.id}: size 0 (LEADER={LEADER} SIZING={SIZING})")
        return

    if trade.is_sell:
        result = bevo.stock_sell(trade.asset.symbol, usd=size, idempotency_key=key)
    else:
        if too_small(trade, size, STOCK_MIN_USD, "stock buy"):
            return
        result = bevo.stock_buy(trade.asset.symbol, usd=size, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} stock {trade.asset.symbol} ${size:.2f} {result.summary} key={key}")


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
    if trade.is_sell:
        result = bevo.sell(trade.asset, usd=size, idempotency_key=key)
    else:
        result = bevo.buy(trade.asset, usd=size, idempotency_key=key)
    bevo.log(f"copytrade event={trade.id} {trade.asset} ${size:.2f} {result.summary} key={key}")


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
        # the spot rail — bevo.buy("AAPL") is a bare-symbol swap that resolves
        # to whatever token happens to wear that ticker, which is someone
        # else's asset. Ask the catalog only when the owner asked for stocks:
        # token-search hides tokenized stocks from an owner whose region does
        # not permit them, so a `False` here can mean "not a stock" OR "not
        # yours to trade", and both end the same way — skip, never substitute.
        if trade.asset.address is None:
            if MIRROR_STOCKS and is_stock_leg(trade):
                copy_stock(trade, key)
            else:
                bevo.log(f"copytrade skip event={trade.id}: {trade.asset.symbol} names no address and is not a stock we can trade")
            continue

        copy_spot(trade, key)


if __name__ == "__main__":
    main()
