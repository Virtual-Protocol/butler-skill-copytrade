"""Copy another trader's moves, on your own size.

Every trade the leaders you named make arrives on this duty's trigger. Each
one is gated (is it a rail you turned on? is it big enough to be worth
mirroring?), sized the way you chose, and placed with an idempotency key
derived from the LEADER's event — so the same move redelivered after a
restart is recognised by bevo-server's ledger and never filed twice.

What it will not do: size off anything it could not read, guess a side a perp
row did not name, or touch a rail whose switch is off. Every skip is a line
in this duty's log with its reason, and each burst of trades produces at most
one quiet note. It mirrors sells by default; an owner who wants buys only
turns `MIRROR_SELLS` off.

The program keeps no count of its own. What it may spend without asking is
the pocket the owner funds in the app, and bevo-server holds that line.

Settings: SIZING (fixed | cash_share | leader_share) with SIZE_USD or SHARE,
MAX_USD as a per-trade ceiling, CHAIN_IDS to pin the chains, MIN_LEADER_USD
to ignore small moves, and the MIRROR_SELLS / MIRROR_PERPS / MIRROR_STOCKS
switches.
"""

import bevo
import json
import math
import os
import re
import subprocess

PARAMS = json.loads(os.environ.get("PARAMS", "{}"))

SIZING = PARAMS.get("SIZING", "fixed")
SIZE_USD = PARAMS.get("SIZE_USD")
SHARE = PARAMS.get("SHARE")
MAX_USD = PARAMS.get("MAX_USD") or 0
CHAIN_IDS = [int(c) for c in (PARAMS.get("CHAIN_IDS") or [])]
MIN_LEADER = PARAMS.get("MIN_LEADER_USD") or 0
# Widening switches are read with an explicit `False` default and never with
# the schema's. A settings blob that lost a key must mirror LESS, not more.
# (Sells are still ON by default: the schema's `true` is filled into PARAMS
# when the duty is filed, so only a blob that LOST the key reads False here.)
MIRROR_SELLS = PARAMS.get("MIRROR_SELLS", False) is True
MIRROR_PERPS = PARAMS.get("MIRROR_PERPS", False) is True
MIRROR_STOCKS = PARAMS.get("MIRROR_STOCKS", False) is True
PERP_LEVERAGE = PARAMS.get("PERP_LEVERAGE") or 0
PERP_MAX_LEVERAGE = PARAMS.get("PERP_MAX_LEVERAGE") or 0

NAME = os.environ.get("BEVO_SERVICE_NAME") or "copytrade"

# The rails' own minimums, refused here rather than downstream: a leg under
# one comes back as a wire error a loop reads as an outage, and refusing it
# locally makes the skip a logged sentence instead.
SPOT_MIN_USD = 2.0
PERP_MIN_USD = 15.0
STOCK_MIN_USD = 15.0
# Perp leverage bevo-server accepts. A figure outside it is CLAMPED, never
# refused: the leverage comes from the leader, and refusing the leg because
# they used 20x under a 5x cap stops the mirror silently.
LEVERAGE_MIN, LEVERAGE_MAX = 1, 50

EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def say(text):
    """`bevo.log()`, flattened onto one line.

    A leader's token symbol or an error the rail echoed is untrusted text,
    and a newline inside it would start a line of its own in `duty_logs` —
    one that reads as if this program, or the supervisor, wrote it.
    """
    bevo.log(" ".join(str(text).split()))


# ── running `acp trade` ──────────────────────────────────────────────────────


def fmt(number):
    """Trim a float to something the CLI parses and a human can read."""
    return ("%.8f" % float(number)).rstrip("0").rstrip(".")


def token_ref(value):
    """What identifies a token on the wire.

    An EVM address is lowercased, because `0xAbC…` and `0xabc…` name one
    token and would otherwise derive two idempotency keys — one intent
    becoming two trades. A Solana mint is base58 and case-SENSITIVE, so it
    goes through verbatim; lowercasing one addresses nothing at all.
    """
    text = str(value or "").strip()
    if EVM_ADDRESS.match(text):
        return text.lower()
    if SOLANA_ADDRESS.match(text):
        return text
    return text.lstrip("$").upper()


def answer_of(text):
    """The JSON `acp` printed, or None.

    None is NOT a refusal — the request may have landed. See `filed()`.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def filed(args, key, sentence):
    """Run one `acp trade` and read what it answered. Returns (ok, summary).

    `--idempotency-key` is written out literally as the last pair of the argv:
    that is what makes the same leader event redelivered after a restart the
    replay bevo-server's ledger recognises, and it is also how this duty is
    allowed to be filed at all — a money command with no key is refused.

    An unparseable answer is `unknown_outcome`, never "refused": the request
    may have landed. `bevo.exec_status(key)` is the one way to find out, and
    re-running with a NEW key is how one trade becomes two.
    """
    done = subprocess.run(
        ["acp", "trade", *args, "--idempotency-key", key],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    answer = answer_of(done.stdout)
    if answer is None:
        state = (bevo.exec_status(key) or {}).get("state")
        return False, (
            "%s — outcome UNKNOWN, the rail said nothing readable (exec_status: %s). "
            "Never re-run this with a new key." % (sentence, state)
        )
    if answer.get("executed"):
        return True, "%s — executed" % sentence
    if answer.get("asked"):
        return True, "%s — waiting for your owner's approval" % sentence
    if answer.get("ok"):
        return True, "%s — accepted, executing now" % sentence
    if answer.get("unrecognized"):
        return False, (
            "%s — the server answered %r, which this container does not recognise. "
            "Do NOT report it as done." % (sentence, answer.get("status"))
        )
    return False, "%s — refused: %s" % (sentence, answer.get("error") or answer.get("status"))


def refused(sentence, why):
    """A leg this duty declined before spending anything."""
    return False, "%s — refused: %s" % (sentence, why)


# ── reading the owner's own book ─────────────────────────────────────────────


def spot_rows():
    """Every spot holding, or None when nobody could look.

    `bevo.holdings()` answers `[]` for both an outage and an empty wallet,
    which is right for printing a portfolio and wrong for sizing a sell.
    """
    try:
        body = bevo.read("/user-assets", {"fresh": 1})
    except bevo.BevoError as error:
        say("holdings unavailable: %s" % error)
        return None
    spot = (body or {}).get("spot") or {}
    if spot.get("available") is not True:
        return None
    return [bevo.Holding(row) for row in (spot.get("tokens") or [])]


def held_row(ref, chain=None):
    """The owner's holding of one token. None = not held, False = unreadable.

    Three rules, each of them a real sell of the wrong thing: an EVM address
    matches case-insensitively and a Solana mint does NOT; a chain that was
    asked for filters the candidates; and among the survivors the LARGEST
    balance wins rather than whichever row the portfolio listed first.
    """
    rows = spot_rows()
    if rows is None:
        return False
    text = str(ref).strip()
    lowered = text.lower()
    evm_or_symbol = not SOLANA_ADDRESS.match(text) or EVM_ADDRESS.match(text)

    def names(holding):
        if evm_or_symbol:
            return (holding.symbol or "").lower() == lowered or (
                holding.address or ""
            ).lower() == lowered
        return (holding.address or "") == text

    matches = [row for row in rows if names(row)]
    if chain is not None:
        on_chain = [row for row in matches if row.chain_id == chain]
        if on_chain:
            matches = on_chain
    if not matches:
        return None
    return max(matches, key=lambda row: row.amount or 0)


def open_position(ref):
    """One open perp position. None = nothing open, False = unreadable.

    Full match first across every row, then the bare suffix: HIP-3 namespaces
    a coin as `<dex>:<coin>`, so `BTC` has to match `xyz:BTC` too — and two
    passes, because one loop lets `abc:BTC` win over a plain `BTC` later in
    the list.
    """
    needle = str(ref).upper()
    rows = bevo.positions()
    if rows is None:
        return False
    open_rows = [row for row in rows if (row.size or 0) > 0]
    for row in open_rows:
        if str(row.coin or "").upper() == needle:
            return row
    for row in open_rows:
        if str(row.coin or "").split(":")[-1].upper() == needle:
            return row
    return None


# ── the legs ─────────────────────────────────────────────────────────────────


def spot_buy(ev, usd, key):
    ref = token_ref(ev.asset.ref)
    sentence = "Buy %s" % ref
    if usd < SPOT_MIN_USD:
        return refused(sentence, "spot buys are $%s minimum, got $%s" % (fmt(SPOT_MIN_USD), fmt(usd)))
    args = ["--token-in", "usdc", "--amount-in", fmt(usd), "--token-out", ref]
    if ev.asset.chain_id is not None:
        args += ["--chain-out", str(ev.asset.chain_id)]
    return filed(args, key, sentence)


def spot_sell(ev, usd, key):
    """`usd=` is a REQUEST: converted at the holding's price and capped at it.

    Selling `usd/price` uncapped is how a duty asks for $50 of a token the
    owner holds $9 of and gets a wire error instead of a $9 sell. The chain
    pinned is the HOLDING's, never the leader's: the same symbol exists on
    several chains and the lot being sold is the one that was found.
    """
    ref = token_ref(ev.asset.ref)
    sentence = "Sell %s" % ref
    held = held_row(ref, ev.asset.chain_id)
    if held is False:
        return refused(sentence, "could not read your holdings, so %s was not sized" % ref)
    if held is None or not held.amount:
        return refused(sentence, "you don't hold any %s" % ref)
    if not held.price_usd:
        return refused(sentence, "no price for %s" % ref)
    # Floored, not rounded: rounding up sells a quantity the owner does not
    # have, and the rail answers that with a balance error, not a smaller fill.
    amount = math.floor(min(usd / held.price_usd, held.amount) * 1e8) / 1e8
    if not (math.isfinite(amount) and amount > 0 and float(fmt(amount)) > 0):
        return refused(sentence, "the computed sell amount is 0 for %s" % ref)
    args = ["--token-in", held.address or ref]
    if held.chain_id is not None:
        args += ["--chain-in", str(held.chain_id)]
    args += ["--amount-in", fmt(amount), "--token-out", "usdc"]
    return filed(args, key, sentence)


def leverage_for(ev):
    """The leverage a copied open is placed at. Returns (lev, asked, why).

    `why` is set, and `lev` None, when the figure is not a number at all.
    """
    try:
        asked = float(PERP_LEVERAGE or ev.leverage or 1)
    except (TypeError, ValueError):
        return None, None, "the leverage was not a number"
    if not math.isfinite(asked):
        return None, None, "the leverage was not a finite number"
    ceiling = LEVERAGE_MAX
    if PERP_MAX_LEVERAGE:
        ceiling = min(ceiling, int(PERP_MAX_LEVERAGE))
    return int(min(max(math.trunc(asked), LEVERAGE_MIN), ceiling)), asked, None


def perp_open(ev, usd, key):
    """`usd` is NOTIONAL — what `--amount-usdc` means on the perp rail."""
    ref = token_ref(ev.coin)
    side = "short" if ev.is_short else "long"
    sentence = "%s %s" % (side.capitalize(), ref)
    if usd < PERP_MIN_USD:
        return refused(sentence, "perps are $%s minimum, got $%s" % (fmt(PERP_MIN_USD), fmt(usd)))
    lev, asked, why = leverage_for(ev)
    if why:
        return refused(sentence, why)
    args = [
        "--side", side,
        "--token", ref,
        "--amount-usdc", fmt(usd),
        "--leverage", str(lev),
    ]
    ok, summary = filed(args, key, sentence)
    if lev != math.trunc(asked):
        # Said where the owner reads it, not only in the log: the clamp
        # changed the size of a position they are about to see a card for.
        summary += " (leverage %sx clamped to %dx)" % (fmt(asked), lev)
    return ok, summary


def perp_close(ev, key):
    """Sized in USD NOTIONAL and never `--size`, which bevo-server refuses.

    `buildPerpParams` leaves `amountUsdc` undefined for a `--size` close and
    the "perps must be sized in USD notional" guard answers 400 — it grants
    no reduce-only exemption. Do not "fix" this back without reading it.
    """
    ref = token_ref(ev.coin)
    sentence = "Close %s" % ref
    position = open_position(ref)
    if position is False:
        return refused(sentence, "could not read your perp account, so %s was not closed" % ref)
    if position is None:
        return refused(sentence, "no open %s position" % ref)
    side = str(position.side or "").lower()
    if side not in ("long", "short"):
        # Never guess. Closing the wrong way DOUBLES the position.
        return refused(sentence, "the open %s position reported no side" % ref)
    notional = position.usd
    if notional is None and position.mark_usd is not None and position.size is not None:
        notional = abs(position.size * position.mark_usd)
    if notional is None or not (
        math.isfinite(notional) and notional > 0 and float(fmt(notional)) > 0
    ):
        return refused(sentence, "the open %s position's value could not be read" % ref)
    args = [
        "--side", "short" if side == "long" else "long",
        "--token", ref,
        "--amount-usdc", fmt(notional),
        "--reduce-only",
    ]
    return filed(args, key, sentence)


def stock_buy(ev, usd, key):
    """A tokenized stock's own grammar: no `--side`, a ticker, and a floor."""
    raw = str(ev.coin or "").strip()
    sentence = "Buy %s" % raw
    if EVM_ADDRESS.match(raw) or SOLANA_ADDRESS.match(raw):
        # `--token 0x…` is the spot grammar with a stock's flags, and the rail
        # answers it by buying whatever token that address is.
        return refused(sentence, "a tokenized stock is named by its ticker, not an address")
    if usd < STOCK_MIN_USD:
        return refused(sentence, "stock buys are $%s minimum, got $%s" % (fmt(STOCK_MIN_USD), fmt(usd)))
    ref = token_ref(raw)
    args = ["--token", ref, "--amount-usdc", fmt(usd)]
    return filed(args, key, "Buy %s" % ref)


def stock_sell(ev, usd, key):
    """Sized off `spot.stocks`, never the look-alike `spot.tokens` row.

    The two disagree on a share-multiplier venue like xStocks — `tokens` is
    the raw on-chain balance and `shares` is what the venue sells — so a sell
    sized off the token row sells the wrong quantity.
    """
    ref = token_ref(ev.coin)
    sentence = "Sell %s" % ref
    rows = bevo.stocks()
    if rows is None:
        return refused(sentence, "could not read your stock holdings, so %s was not sized" % ref)
    matches = [row for row in rows if (row.ticker or "") == ref]
    held = max(matches, key=lambda row: row.shares or 0) if matches else None
    if held is None or not held.shares:
        return refused(sentence, "you don't hold any %s" % ref)
    price = held.usd_per_share
    if not price and held.usd and held.shares:
        price = held.usd / held.shares
    if not price:
        return refused(sentence, "no price for %s" % ref)
    # Capped at what is held and floored, for the same reasons as a spot sell.
    shares = math.floor(min(usd / price, held.shares) * 1e8) / 1e8
    if shares <= 0:
        return refused(sentence, "the computed sell amount is 0 for %s" % ref)
    if held.venue is None:
        # A numeric `--chain` is rerouted onto a bare-symbol spot swap.
        return refused(sentence, "no venue on the %s holding" % ref)
    args = ["--token", ref, "--amount-shares", fmt(shares), "--chain", str(held.venue)]
    return filed(args, key, sentence)


# ── routing and sizing ───────────────────────────────────────────────────────


def route_for(ev):
    """Which leg this leader trade becomes, or a reason it becomes none.

    The ORDER is the rule. A perp close is routed before any size knob is
    consulted, because closing a position the leader closed is not sized —
    refusing it for being under MIN_LEADER_USD would leave the mirror open
    after the leader is flat.
    """
    usd = ev.usd_value or 0
    under_min = MIN_LEADER > 0 and usd < MIN_LEADER
    thin = "$%.2f is under MIN_LEADER_USD $%.2f" % (usd, MIN_LEADER)

    if ev.is_perp:
        if not MIRROR_PERPS:
            return None, "a perp, and MIRROR_PERPS is off"
        if ev.is_close:
            return "perp-close", None
        if under_min:
            return None, thin
        return "perp-open", None

    if ev.is_sell and not MIRROR_SELLS:
        return None, "a sell, and MIRROR_SELLS is off"
    if not ev.is_buy and not ev.is_sell:
        return None, "a row the feed could not classify"

    if ev.is_stock:
        if not MIRROR_STOCKS:
            return None, "a stock, and MIRROR_STOCKS is off"
        if under_min:
            return None, thin
        # CHAIN_IDS is deliberately not applied to a stock: its venue is a
        # name, not a chain id, so the filter would drop every one of them.
        return ("stock-sell" if ev.is_sell else "stock-buy"), None

    if CHAIN_IDS and ev.asset.chain_id not in CHAIN_IDS:
        return None, "chain %s is not in CHAIN_IDS" % (ev.asset.chain_id,)
    if under_min:
        return None, thin
    return ("spot-sell" if ev.is_sell else "spot-buy"), None


def size_for(ev):
    """Dollars to put behind a leg, or 0 with a reason.

    A read that failed is never a zero: `cash_share` off an unreadable wallet
    skips the trade rather than sizing it at nothing or at everything.
    """
    if SIZING in ("cash_share", "leader_share"):
        if not SHARE or SHARE <= 0:
            return 0, "%s: SHARE is not set — nothing to size" % SIZING
        if SIZING == "cash_share":
            wallet = bevo.balance()
            cash = wallet.cash_usd
            if not wallet.available or cash is None:
                return 0, "cash_share: the wallet could not be read — not sizing"
            if cash <= 0:
                return 0, "cash_share: wallet cash is $0 — nothing to size"
            usd = cash * SHARE
        else:
            usd = (ev.usd_value or 0) * SHARE
    else:
        usd = SIZE_USD or 0

    if MAX_USD > 0:
        usd = min(usd, MAX_USD)
    if not math.isfinite(usd) or usd <= 0:
        return 0, "size 0 (SIZING=%s)" % SIZING
    return usd, None


def key_for(ev, route):
    """A leg's idempotency key. EXPLICIT, and in the retired stage's formats.

    Continuity, not style: bevo-server's ledger already holds keys minted as
    `copy:<duty>:trade:<event>` by the graph recipe these templates replace,
    so a leader event redelivered across the migration is recognised as the
    replay it is. A spot buy and a spot sell of the same event share one key,
    exactly as they did.
    """
    if route in ("spot-buy", "spot-sell"):
        return "copy:%s:trade:%s" % (bevo.SERVICE_ID, ev.id)
    return "copy:%s:%s:%s" % (bevo.SERVICE_ID, route, ev.id)


def place(ev, route, usd, key):
    """The leg itself."""
    if route == "spot-buy":
        return spot_buy(ev, usd, key)
    if route == "spot-sell":
        return spot_sell(ev, usd, key)
    if route == "perp-close":
        return perp_close(ev, key)
    if route == "perp-open":
        if not isinstance(ev.is_short, bool):
            return None
        return perp_open(ev, usd, key)
    if route == "stock-buy":
        return stock_buy(ev, usd, key)
    if route == "stock-sell":
        return stock_sell(ev, usd, key)
    return None


for batch in bevo.batches(seconds=3):
    told = []
    skipped = 0
    for raw in batch:
        ev = bevo.typed(raw)
        if not isinstance(ev, bevo.TradeEvent) or ev.id is None:
            skipped += 1
            say("skipped: a row this duty copies nothing from")
            continue

        route, reason = route_for(ev)
        if route is None:
            skipped += 1
            say("skipped %s: %s" % (ev.id, reason))
            continue

        # A close is not sized — there is a position to flatten, whatever the
        # leader's notional was.
        usd = 0
        if route != "perp-close":
            usd, reason = size_for(ev)
            if usd <= 0:
                skipped += 1
                say("skipped %s: %s" % (ev.id, reason))
                continue

        key = key_for(ev, route)

        result = place(ev, route, usd, key)
        if result is None:
            skipped += 1
            say("skipped %s: the event named no side" % (ev.id,))
            continue
        ok, summary = result
        if ok:
            told.append(summary)
        else:
            skipped += 1
            say("skipped %s: %s" % (ev.id, summary))

    # One note per burst, and only when something actually happened. A duty
    # that notifies on every quiet batch is one the owner mutes, after which
    # the note that mattered is unheard too.
    if told:
        line = "%s: %s" % (NAME, "; ".join(told))
        if skipped:
            line += "; %d skipped — see duty_logs" % skipped
        bevo.notify(line[:500], quiet=True)
