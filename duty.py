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

It keeps a book of what it copied, per leader and asset, in
`bevo.state["copies"]`, and sells or closes only that: the copy is counted
when bevo-server's ledger says the order executed, never when it was merely
filed, and a leader's exit unwinds the leader's share sold of the copy, capped
at what the owner actually holds. Anything it did not copy — the owner's own
tokens and positions, and copies an earlier version made — it never touches.
What it may spend without asking is the pocket the owner funds in the app,
and bevo-server holds that line.

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
import time

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

# The rails' own minimums, refused here rather than downstream: a leg under
# one comes back as a wire error a loop reads as an outage, and refusing it
# locally makes the skip a logged sentence instead.
SPOT_MIN_USD = 2.0
PERP_MIN_USD = 15.0
# A tokenized stock's floor is per ticker, not a constant — see stock_floor().
# This is what bevo-server answers when it cannot say, and what a failed read
# falls back to.
STOCK_MIN_DEFAULT_USD = 5.0
# Perp leverage bevo-server accepts. A figure outside it is CLAMPED, never
# refused: the leverage comes from the leader, and refusing the leg because
# they used 20x under a 5x cap stops the mirror silently.
LEVERAGE_MIN, LEVERAGE_MAX = 1, 50

EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# The copy book. Legs and seen events outlive bevo-server's exec ledger (rows
# purge after 7 days) by a day, so a leg is never forgotten while the ledger
# can still say what became of it.
STATE_KEY = "copies"
KEEP_S = 8 * 86400
# How long a burst waits for its own orders to settle, and how long an exit
# waits for the copied buys of its asset before sizing off them.
SETTLE_WAIT_S = 60
# Legs still open from earlier bursts are asked again no more often than this,
# and no more than RECHECK_MAX a burst: every ask is an exec read, and that
# budget is shared with the owner's chat.
RECHECK_S = 300
RECHECK_MAX = 20
DUST = 1e-9
# A leader left holding less than this share of what they had is treated as
# flat, so a rounding residue never leaves the copy open.
FLAT_SHARE = 1e-6

#: The book, loaded by open_book(). See there for its shape.
BOOK = {}
#: Keys whose legs this burst wrote or re-sent, settled at the burst's end.
BURST_KEYS = []
#: Copies the ledger forgot before they settled, told in the burst's note.
UNCONFIRMED = []


def say(text):
    """`bevo.log()`, flattened onto one line.

    A leader's token symbol or an error the rail echoed is untrusted text,
    and a newline inside it would start a line of its own in `duty_logs` —
    one that reads as if this program, or the supervisor, wrote it.
    """
    bevo.log(" ".join(str(text).split()))


def num(value):
    """A positive finite number, or None. Feed fields arrive as text or null."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


# ── the copy book ────────────────────────────────────────────────────────────


def open_book():
    """Load the book, or start one. Returns True when it is new.

    {"v": 1, "since": ISO,
     "pos":  {"<leader>|spot|<chain>|<ref>" | "<leader>|stock|<TICKER>" |
              "<leader>|perp|<COIN>": {rail, label, addr, chain, side, units,
                                       leaderUnits}},
     "legs": {"<idempotency key>": {pos, rail, label, side, dir, usd, price,
                                    lead, units, args, sentence, at, checked,
                                    done}},
     "seen": {"<leader event id>": epoch}}

    `units` is what this duty holds of the copy; `leaderUnits` what the leader
    acquired in the buys it copied, or None once one of them did not say.
    """
    global BOOK
    book = bevo.state.get(STATE_KEY)
    fresh = not (isinstance(book, dict) and book.get("v") == 1)
    if fresh:
        book = {"v": 1, "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for part in ("pos", "legs", "seen"):
        if not isinstance(book.get(part), dict):
            book[part] = {}
    BOOK = book
    save()
    return fresh


def save():
    """`bevo.state` is written to disk when a top-level key is assigned, and
    never when a dict inside it changes — so every change to the book ends
    here, or it is gone at the next restart."""
    bevo.state[STATE_KEY] = BOOK


def leader_of(ev):
    return str(ev.owner or ev.wallet or "")


def entry_for(poskey, rail, label, addr=None, chain=None, side=None):
    """The book's entry for one copied asset, made empty when there is none."""
    entry = BOOK["pos"].get(poskey)
    if entry is None:
        entry = {
            "rail": rail, "label": label, "addr": addr, "chain": chain, "side": side,
            "units": 0.0,
            # A stock row never states the leader's shares, so a stock's
            # count is unknown from the start.
            "leaderUnits": None if rail == "stock" else 0.0,
        }
        BOOK["pos"][poskey] = entry
    return entry


def pending(poskey, direction=None):
    """Keys of this asset's legs that have not settled."""
    return [
        key for key, leg in BOOK["legs"].items()
        if leg.get("pos") == poskey and not leg.get("done")
        and (direction is None or leg.get("dir") == direction)
    ]


def live(poskey):
    """True while the book holds some of this copy, or a copied buy of it is open."""
    entry = BOOK["pos"].get(poskey)
    return bool(entry) and (entry.get("units", 0) > DUST or bool(pending(poskey, "in")))


def spot_key(leader, chain, ref, symbol):
    """The book key of a spot copy: the exact one, else the single entry of
    this leader on this chain that the ref or symbol names. A leader buys by
    address and sells by symbol, or the other way round, and both are one copy.
    """
    exact = "%s|spot|%s|%s" % (leader, chain, ref)
    if exact in BOOK["pos"]:
        return exact
    names = {token_ref(ref)}
    if symbol:
        names.add(token_ref(symbol))
    prefix = "%s|spot|%s|" % (leader, chain)
    hits = [
        poskey for poskey, entry in BOOK["pos"].items()
        if poskey.startswith(prefix)
        and (entry.get("label") in names or (entry.get("addr") or None) in names)
    ]
    return hits[0] if len(hits) == 1 else exact


def stock_key(leader, ticker):
    return "%s|stock|%s" % (leader, ticker)


def perp_key(leader, coin):
    return "%s|perp|%s" % (leader, coin)


def copied_buy(poskey, usd, price, lead, side=None):
    """The leg record of a buy or open, credited only once it executes."""
    entry = BOOK["pos"][poskey]
    return {
        "pos": poskey, "rail": entry["rail"], "label": entry["label"], "side": side,
        "dir": "in", "usd": usd, "price": price, "lead": lead, "units": None,
    }


def copied_exit(poskey, units):
    """The leg record of a sell or close: `units` leave the copy when it is
    filed, and come back only if bevo-server says it was dropped."""
    entry = BOOK["pos"][poskey]
    return {
        "pos": poskey, "rail": entry["rail"], "label": entry["label"],
        "side": entry.get("side"), "dir": "out", "usd": None, "price": None,
        "lead": None, "units": units,
    }


class Fresh:
    """One fresh `/user-assets` read, taken on first use and shared after.

    None when it could not be read — never an empty portfolio, which would
    shrink every copy to nothing.
    """

    def __init__(self):
        self.tried = False
        self.body = None

    def get(self):
        if not self.tried:
            self.tried = True
            try:
                self.body = bevo.read("/user-assets", {"fresh": 1}) or {}
            except bevo.BevoError as error:
                say("holdings unavailable: %s" % error)
                self.body = None
        return self.body


def price_now(entry, fresh):
    """What one unit of a copied asset is worth now, from the owner's own
    holding or position. None when it cannot be said yet."""
    body = fresh.get()
    if entry["rail"] == "spot":
        held = held_row(entry.get("addr") or entry["label"], entry.get("chain"), spot_rows(body), strict=True)
        return num(held.price_usd) if held else None
    if entry["rail"] == "stock":
        held = stock_row(entry["label"], stock_rows(body))
        if not held:
            return None
        price = num(held.usd_per_share)
        if price is None and num(held.usd) and num(held.shares):
            price = held.usd / held.shares
        return price
    position = open_position(entry["label"], perp_rows(body))
    return num(position.mark_usd) if position else None


def resolve(key, fate, fresh):
    """Apply what became of one leg to the book. Each leg is applied once:
    its `done` flag, not the rail's `replay`, is what stops a second count.
    """
    leg = BOOK["legs"].get(key)
    if not leg or leg.get("done"):
        return
    leg["checked"] = time.time()
    entry = entry_for(leg["pos"], leg["rail"], leg["label"], side=leg.get("side"))
    inbound = leg.get("dir") == "in"
    if fate == "executed":
        if inbound:
            # Counted at the price it filled at when the feed says, else at
            # the holding's price now. Without either it waits for a later
            # burst rather than being counted at a guess.
            price = num(leg.get("price")) or price_now(entry, fresh)
            if price is None:
                return
            entry["units"] = entry.get("units", 0) + leg["usd"] / price
            if leg.get("lead") is None or entry.get("leaderUnits") is None:
                entry["leaderUnits"] = None
            else:
                entry["leaderUnits"] += leg["lead"]
            if leg.get("side"):
                entry["side"] = leg["side"]
    elif fate == "dropped":
        if not inbound:
            entry["units"] = entry.get("units", 0) + (leg.get("units") or 0)
    elif fate == "unknown":
        # The ledger forgot it: it may have run. A buy is not counted, so it
        # is never sold for the owner; a sell is not refunded, so nothing is
        # sold twice.
        if inbound:
            UNCONFIRMED.append(leg.get("sentence") or leg["label"])
    else:
        return
    leg["done"] = True
    leg["fate"] = fate


def settle(keys, wait, fresh):
    """Ask the ledger about each leg, sharing one deadline across them all."""
    deadline = time.monotonic() + wait
    for key in keys:
        leg = BOOK["legs"].get(key)
        if not leg or leg.get("done"):
            continue
        fate = bevo.settled(key, wait=max(0, deadline - time.monotonic()))
        leg["checked"] = time.time()
        resolve(key, fate, fresh)
    save()


def recheck():
    """Ask again about legs earlier bursts left open: the oldest-asked first,
    at most RECHECK_MAX, none asked within RECHECK_S."""
    cutoff = time.time() - RECHECK_S
    due = sorted(
        (leg.get("checked") or 0, key) for key, leg in BOOK["legs"].items()
        if not leg.get("done") and (leg.get("checked") or 0) <= cutoff
    )
    if due:
        settle([key for _, key in due[:RECHECK_MAX]], 0, Fresh())


def prune():
    """Forget legs and events older than KEEP_S, and copies that are empty."""
    cutoff = time.time() - KEEP_S
    for key, leg in list(BOOK["legs"].items()):
        if (leg.get("at") or 0) < cutoff:
            if not leg.get("done") and leg.get("dir") == "in":
                UNCONFIRMED.append(leg.get("sentence") or leg.get("label"))
            del BOOK["legs"][key]
    for event, at in list(BOOK["seen"].items()):
        if (at or 0) < cutoff:
            del BOOK["seen"][event]
    for poskey, entry in list(BOOK["pos"].items()):
        if entry.get("units", 0) <= DUST and not pending(poskey):
            del BOOK["pos"][poskey]


def share_sold(ev, entry, sold, venue_closed):
    """The fraction of the copy the leader's exit stands for, and — the first
    time this event is seen — the leader's count after it.

    Exact only when both the leader's units sold and their units bought in the
    copied buys are known; otherwise the whole copy (the leader's share sold
    is unknown, and a copy left open after the leader is flat is the worse
    mistake). A venue close or liquidation is always the whole copy.
    """
    lead = entry.get("leaderUnits")
    if venue_closed:
        share, after = 1.0, 0.0
    elif sold is None or lead is None or lead <= 0:
        share, after = 1.0, None
    else:
        after = max(0.0, lead - sold)
        share = 1.0 if after <= lead * FLAT_SHARE else min(1.0, sold / lead)
    if str(ev.id) not in BOOK["seen"]:
        entry["leaderUnits"] = after if entry["rail"] != "stock" else None
    return share


def ready_to_unwind(poskey):
    """Settle this asset's open buys first (≤ SETTLE_WAIT_S), so an exit that
    follows its buy within seconds sizes off the copy that buy made."""
    fresh = Fresh()
    settle(pending(poskey, "in"), SETTLE_WAIT_S, fresh)
    return fresh


def skip(why):
    """A leg with nothing to do: logged and counted as skipped, not failed."""
    return "skip", why


# ── running `acp trade` ──────────────────────────────────────────────────────


def fmt(number):
    """Trim a float to something the CLI parses and a human can read."""
    return ("%.8f" % float(number)).rstrip("0").rstrip(".")


def money(usd):
    """A dollar figure as the owner reads it: `$3`, `$2.50`. A `$` followed by
    a digit is an amount to the app, never a token chip."""
    text = "%.2f" % float(usd)
    return "$" + (text[:-3] if text.endswith(".00") else text)


def qty(number):
    """A token or share quantity, to six significant figures."""
    return "%.6g" % float(number)


TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")


def tag(symbol, ref=None):
    """How a note names a token: `$VIRTUAL`, which the app renders as a chip
    that opens the token's card. A symbol a chip cannot hold is written as
    plain words, and with no symbol at all the address is shortened — a note
    never carries a whole address. The symbol is the leader's token's, so
    untrusted: only word characters, spaces, `.` and `-` survive, which keeps
    it from forming markdown (a link, emphasis) or a line of its own.
    """
    text = re.sub(r"[^\w .\-]", "", " ".join(str(symbol or "").split())).strip()
    if text and not (EVM_ADDRESS.match(text) or SOLANA_ADDRESS.match(text)):
        if TICKER.match(text) and re.search(r"[A-Za-z]", text):
            return "$" + text.upper()
        return text[:24]
    address = re.sub(r"[^\w]", "", str(ref or symbol or ""))
    if len(address) > 12:
        return "%s…%s" % (address[:6], address[-4:])
    return address or "a token"


def floor8(number):
    """Floored, not rounded: rounding up sells a quantity the owner does not
    have, and the rail answers that with a balance error, not a smaller fill."""
    return math.floor(number * 1e8) / 1e8


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


def filed(args, key, sentence, leg=None):
    """Run one `acp trade` and read what it answered. Returns (ok, summary);
    summary is None for a replay — already filed, nothing to tell.

    `--idempotency-key` is written out literally as the last pair of the argv:
    that is what makes the same leader event redelivered after a restart the
    replay bevo-server's ledger recognises, and it is also how this duty is
    allowed to be filed at all — a money command with no key is refused.

    The leg is written to the book, and a sell's units reserved, BEFORE the
    command runs: a restart in between must find the key, never sell those
    units a second time.

    An unparseable answer is `unknown_outcome`, never "refused": the request
    may have landed. `bevo.exec_status(key)` is the one way to find out, and
    re-running with a NEW key is how one trade becomes two.
    """
    if leg is not None and key not in BOOK["legs"]:
        now = time.time()
        leg.update(args=list(args), sentence=sentence, at=now, checked=now, done=False)
        BOOK["legs"][key] = leg
        if leg["dir"] == "out":
            BOOK["pos"][leg["pos"]]["units"] -= leg["units"]
        save()
    if key in BOOK["legs"] and key not in BURST_KEYS:
        BURST_KEYS.append(key)

    done = subprocess.run(
        ["acp", "trade", *args, "--idempotency-key", key],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    answer = answer_of(done.stdout)
    booked(key, answer)
    if answer is None:
        state = (bevo.exec_status(key) or {}).get("state")
        return False, (
            "%s — outcome UNKNOWN, the rail said nothing readable (exec_status: %s). "
            "Never re-run this with a new key." % (sentence, state)
        )
    if answer.get("replay"):
        # The ledger already had this key: a redelivered leader event, answered
        # from the first send. Nothing new was filed, and the owner was told
        # the first time — a summary here re-notifies the same approval card on
        # every redelivery. None is "ok, say nothing".
        return True, None
    if answer.get("executed"):
        return True, "%s — done" % sentence
    if answer.get("asked"):
        return True, "%s — waiting for your approval" % sentence
    if answer.get("ok"):
        return True, "%s — placed" % sentence
    if answer.get("unrecognized"):
        return False, (
            "%s — the server answered %r, which this container does not recognise. "
            "Do NOT report it as done." % (sentence, answer.get("status"))
        )
    return False, "%s — refused: %s" % (sentence, answer.get("error") or answer.get("status"))


def booked(key, answer):
    """What an answer settles in the book now; everything else waits for the
    ledger. Only a refusal the server stated is dropped, and only `executed`
    is counted — never `replay`: the unreachable path answers `replay: true,
    executed: true` the first time it sees a key, so the leg's own record is
    the one thing that knows whether it was already counted.
    """
    leg = BOOK["legs"].get(key)
    if not leg or leg.get("done") or answer is None:
        return
    # A 5xx is `refused` on the wire too, but the server may have filed it
    # before failing: that one waits for the ledger like any other unknown.
    server_error = (num(answer.get("httpStatus")) or 0) >= 500
    if answer.get("ok") is False and answer.get("status") == "refused" and not server_error:
        resolve(key, "dropped", None)
    elif answer.get("executed"):
        resolve(key, "executed", Fresh())
    save()


def refused(sentence, why):
    """A leg this duty declined before spending anything."""
    return False, "%s — refused: %s" % (sentence, why)


# ── reading the owner's own book ─────────────────────────────────────────────


def spot_rows(body):
    """Every spot holding in a fresh read, or None when nobody could look.

    `bevo.holdings()` answers `[]` for both an outage and an empty wallet,
    which is right for printing a portfolio and wrong for sizing a sell.
    """
    if body is None:
        return None
    spot = body.get("spot") or {}
    if spot.get("available") is not True:
        return None
    return [bevo.Holding(row) for row in (spot.get("tokens") or [])]


def stock_rows(body):
    """Tokenized-stock holdings, from `spot.stocks`, or None when unreadable."""
    if body is None:
        return None
    spot = body.get("spot") or {}
    if spot.get("available") is not True:
        return None
    return [bevo.StockHolding(row) for row in (spot.get("stocks") or [])]


def perp_rows(body):
    """Open perp positions, or None when the perp account could not be read."""
    if body is None:
        return None
    perps = body.get("perps") or {}
    if perps.get("available") is not True:
        return None
    return [bevo.PerpPosition(row) for row in (perps.get("positions") or [])]


def held_row(ref, chain, rows, strict=False):
    """The owner's holding of one token. None = not held, False = unreadable.

    Three rules, each of them a real sell of the wrong thing: an EVM address
    matches case-insensitively and a Solana mint does NOT; a chain that was
    asked for filters the candidates; and among the survivors the LARGEST
    balance wins rather than whichever row the portfolio listed first.

    `strict` is for the copy's own lot: only its chain counts, so a copy the
    owner sold by hand is never refilled from their lot on another chain, and
    a symbol two tokens on that chain share is False (cannot be told apart),
    never the larger of the two.
    """
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
        if on_chain or strict:
            matches = on_chain
    if not matches:
        return None
    by_symbol = not (EVM_ADDRESS.match(text) or SOLANA_ADDRESS.match(text))
    if strict and by_symbol and len({(row.address or "").lower() for row in matches}) > 1:
        return False
    return max(matches, key=lambda row: row.amount or 0)


def stock_row(ticker, rows):
    """The owner's holding of one stock. None = not held, False = unreadable."""
    if rows is None:
        return False
    matches = [row for row in rows if (row.ticker or "") == ticker]
    return max(matches, key=lambda row: row.shares or 0) if matches else None


def open_position(ref, rows):
    """One open perp position. None = nothing open, False = unreadable.

    Takes the rows of a FRESH read: a cached portfolio still shows a position
    the leader's close already flattened, and sizing a close off it closes
    what is not there.

    Full match first across every row, then the bare suffix: HIP-3 namespaces
    a coin as `<dex>:<coin>`, so `BTC` has to match `xyz:BTC` too — and two
    passes, because one loop lets `abc:BTC` win over a plain `BTC` later in
    the list.
    """
    if rows is None:
        return False
    needle = str(ref).upper()
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
    sentence = "Buy %s of %s" % (money(usd), tag(ev.asset.symbol, ref))
    if usd < SPOT_MIN_USD:
        return refused(sentence, "spot buys are $%s minimum, got $%s" % (fmt(SPOT_MIN_USD), fmt(usd)))
    chain = ev.asset.chain_id
    args = ["--token-in", "usdc", "--amount-in", fmt(usd), "--token-out", ref]
    if chain is not None:
        args += ["--chain-out", str(chain)]
    poskey = spot_key(leader_of(ev), chain, ref, ev.asset.symbol)
    address = token_ref(ev.asset.address) if ev.asset.address else None
    entry_for(poskey, "spot", token_ref(ev.asset.symbol or ref), address, chain)
    # Feed fields read off `raw`: a container on an older SDK has no
    # attribute for them, and a missing one is "not stated", never zero.
    leg = copied_buy(poskey, usd, num(ev.raw.get("fillPriceUsd")), num(ev.raw.get("amountOut")))
    return filed(args, key, sentence, leg)


def spot_sell(ev, key):
    """Sells this duty's own copy of the token the leader disposed of.

    The token is the one that went IN, so a leader's swap out of a copied
    token into another unwinds the copy without buying the other. The amount
    is the leader's share sold of the copy, capped at the holding; the chain
    pinned is the HOLDING's, never the leader's: the same symbol exists on
    several chains and the lot being sold is the one that was found.
    """
    ref = token_ref(ev.token_in_address or ev.token_in)
    sentence = "Sell %s" % ref
    poskey = spot_key(leader_of(ev), ev.chain_id, ref, ev.token_in)
    if not live(poskey):
        return skip("nothing copied to unwind for %s" % ref)
    fresh = ready_to_unwind(poskey)
    entry = BOOK["pos"][poskey]
    share = share_sold(ev, entry, num(ev.amount_in), False)
    held = held_row(entry.get("addr") or ref, entry.get("chain"), spot_rows(fresh.get()), strict=True)
    if held is False:
        save()
        return skip("could not read your holdings, or tell your %s on that chain apart, so it was not sized" % ref)
    holding = (held.amount or 0) if held else 0
    # Shrink only: what the owner sold by hand is gone from the copy too.
    entry["units"] = min(entry["units"], holding)
    save()
    if entry["units"] <= DUST:
        return skip("nothing copied to unwind for %s" % ref)
    amount = floor8(min(share * entry["units"], holding))
    if not (math.isfinite(amount) and amount > 0 and float(fmt(amount)) > 0):
        return skip("the copied amount of %s rounds to 0" % ref)
    args = ["--token-in", held.address or ref]
    if held.chain_id is not None:
        args += ["--chain-in", str(held.chain_id)]
    args += ["--amount-in", fmt(amount), "--token-out", "usdc"]
    sentence = "Sell %s %s" % (qty(amount), tag(ev.token_in or entry.get("label"), ref))
    return filed(args, key, sentence, copied_exit(poskey, amount))


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
    """`usd` is NOTIONAL — what `--amount-usdc` means on the perp rail.

    Refused against an open position of the owner's on the other side: the
    venue nets one account per coin, so the copy would shrink the owner's own
    position instead of opening one.
    """
    ref = token_ref(ev.coin)
    side = "short" if ev.is_short else "long"
    sentence = "%s %s" % (side.capitalize(), ref)
    if usd < PERP_MIN_USD:
        return refused(sentence, "perps are $%s minimum, got $%s" % (fmt(PERP_MIN_USD), fmt(usd)))
    lev, asked, why = leverage_for(ev)
    if why:
        return refused(sentence, why)
    position = open_position(ref, perp_rows(Fresh().get()))
    if position is False:
        return refused(sentence, "could not read your perp account")
    if position is not None and str(position.side or "").lower() != side:
        return refused(sentence, "you hold a %s %s position of your own; copying the %s would net against it" % (
            str(position.side or "sideless").lower(), ref, side))
    poskey = perp_key(leader_of(ev), ref)
    entry = entry_for(poskey, "perp", ref, side=side)
    if not live(poskey):
        entry["side"] = side
    args = [
        "--side", side,
        "--token", ref,
        "--amount-usdc", fmt(usd),
        "--leverage", str(lev),
    ]
    leg = copied_buy(poskey, usd, num(ev.raw.get("fillPriceUsd")), num(ev.raw.get("amountOut")), side)
    sentence = "%s %s of %s at %dx" % (side.capitalize(), money(usd), tag(ref), lev)
    ok, summary = filed(args, key, sentence, leg)
    if summary is not None and lev != math.trunc(asked):
        # Said where the owner reads it, not only in the log: the clamp
        # changed the size of a position they are about to see a card for.
        summary += " (they used %sx; capped at %dx)" % (fmt(asked), lev)
    return ok, summary


def perp_close(ev, key):
    """Closes this duty's own copy, reduce-only, in USD NOTIONAL.

    The leader's share closed of the copy's coins, times the mark: the owner's
    own size on the same coin is never part of it, and reduce-only bounds the
    order at the live position. Never `--size`, which bevo-server refuses:
    `buildPerpParams` leaves `amountUsdc` undefined for a `--size` close and
    the "perps must be sized in USD notional" guard answers 400 — it grants
    no reduce-only exemption. Do not "fix" this back without reading it.
    """
    ref = token_ref(ev.coin)
    sentence = "Close %s" % ref
    poskey = perp_key(leader_of(ev), ref)
    if not live(poskey):
        return skip("nothing copied to unwind for %s" % ref)
    fresh = ready_to_unwind(poskey)
    entry = BOOK["pos"][poskey]
    venue_closed = ev.hl_event in ("liquidation", "exchange_close")
    share = share_sold(ev, entry, num(ev.raw.get("amountOut")), venue_closed)
    position = open_position(ref, perp_rows(fresh.get()))
    if position is False:
        save()
        return skip("could not read your perp account, so %s was not closed" % ref)
    side = str(position.side or "").lower() if position else ""
    if position and side not in ("long", "short"):
        # Never guess. Closing the wrong way DOUBLES the position.
        save()
        return skip("the open %s position reported no side" % ref)
    if position is None or side != entry.get("side"):
        # Flat, or the owner turned it round by hand: the copy is gone.
        entry["units"] = 0.0
        save()
        return skip("no %s %s position left to close; the copy is cleared" % (entry.get("side"), ref))
    entry["units"] = min(entry["units"], position.size or 0)
    save()
    if entry["units"] <= DUST:
        return skip("nothing copied to unwind for %s" % ref)
    if not num(position.mark_usd):
        return skip("no mark price for %s, so the copy was not sized" % ref)
    coins = min(share * entry["units"], position.size)
    notional = floor8(coins * position.mark_usd)
    if not (math.isfinite(notional) and notional > 0 and float(fmt(notional)) > 0):
        return skip("the copied %s position rounds to $0" % ref)
    args = [
        "--side", "short" if side == "long" else "long",
        "--token", ref,
        "--amount-usdc", fmt(notional),
        "--reduce-only",
    ]
    sentence = "Close %s of %s %s" % (money(notional), tag(ref), side)
    return filed(args, key, sentence, copied_exit(poskey, coins))


def stock_floor(ticker):
    """The smallest buy the venues take for this tokenized stock right now.

    Per listing, not a constant: $5 on most tickers and $22 on the illiquid
    tail, the cost buffer included, and it moves as venues go thin. bevo-server
    computes it with the rule the planner gates on, so a leg under it is
    refused here as a sentence rather than downstream as a wire error. A read
    that fails falls back to the server's own default and lets the rail decide.
    """
    try:
        return float(bevo.read("/stock-limits", {"ticker": ticker})["minUsd"])
    except (bevo.BevoError, KeyError, TypeError, ValueError):
        return STOCK_MIN_DEFAULT_USD


def stock_buy(ev, usd, key):
    """A tokenized stock's own grammar: no `--side`, a ticker, and a floor.

    No fill price is taken from the feed: its unit can be the venue's token
    rather than a share, so the copy is counted at the holding's price per share.
    """
    raw = str(ev.coin or "").strip()
    sentence = "Buy %s" % raw
    if EVM_ADDRESS.match(raw) or SOLANA_ADDRESS.match(raw):
        # `--token 0x…` is the spot grammar with a stock's flags, and the rail
        # answers it by buying whatever token that address is.
        return refused(sentence, "a tokenized stock is named by its ticker, not an address")
    ref = token_ref(raw)
    floor = stock_floor(ref)
    if usd < floor:
        return refused(sentence, "%s buys are $%s minimum, got $%s" % (ref, fmt(floor), fmt(usd)))
    poskey = stock_key(leader_of(ev), ref)
    entry_for(poskey, "stock", ref)
    args = ["--token", ref, "--amount-usdc", fmt(usd)]
    sentence = "Buy %s of %s" % (money(usd), tag(ref))
    return filed(args, key, sentence, copied_buy(poskey, usd, None, None))


def stock_sell(ev, key):
    """Sells this duty's own copy, sized off `spot.stocks`, never the
    look-alike `spot.tokens` row.

    The two disagree on a share-multiplier venue like xStocks — `tokens` is
    the raw on-chain balance and `shares` is what the venue sells — so a sell
    sized off the token row sells the wrong quantity. The feed never states a
    leader's shares, so the leader's share sold is unknown: the whole copy.
    """
    ref = token_ref(ev.coin)
    sentence = "Sell %s" % ref
    poskey = stock_key(leader_of(ev), ref)
    if not live(poskey):
        return skip("nothing copied to unwind for %s" % ref)
    fresh = ready_to_unwind(poskey)
    entry = BOOK["pos"][poskey]
    share = share_sold(ev, entry, None, False)
    held = stock_row(ref, stock_rows(fresh.get()))
    if held is False:
        save()
        return skip("could not read your stock holdings, so %s was not sized" % ref)
    holding = (held.shares or 0) if held else 0
    entry["units"] = min(entry["units"], holding)
    save()
    if entry["units"] <= DUST:
        return skip("nothing copied to unwind for %s" % ref)
    shares = floor8(min(share * entry["units"], holding))
    if shares <= 0:
        return skip("the copied amount of %s rounds to 0" % ref)
    if held.venue is None:
        # A numeric `--chain` is rerouted onto a bare-symbol spot swap.
        return refused(sentence, "no venue on the %s holding" % ref)
    args = ["--token", ref, "--amount-shares", fmt(shares), "--chain", str(held.venue)]
    sentence = "Sell %s shares of %s" % (qty(shares), tag(ref))
    return filed(args, key, sentence, copied_exit(poskey, shares))


# ── routing and sizing ───────────────────────────────────────────────────────


def flips_copy(ev):
    """A leader's open on the other side of a perp this duty copied: on the
    venue it closes the leader's position first, so it unwinds the copy."""
    if ev.is_close or not isinstance(ev.is_short, bool):
        return False
    poskey = perp_key(leader_of(ev), token_ref(ev.coin))
    side = "short" if ev.is_short else "long"
    return live(poskey) and BOOK["pos"][poskey].get("side") not in (None, side)


def swaps_out_of_copy(ev):
    """A leader's buy paid for with a token this duty copied from them."""
    if not ev.is_buy or not (ev.token_in_address or ev.token_in):
        return False
    ref = token_ref(ev.token_in_address or ev.token_in)
    return live(spot_key(leader_of(ev), ev.chain_id, ref, ev.token_in))


def route_for(ev):
    """Which leg this leader trade becomes, or a reason it becomes none.

    The ORDER is the rule. Every exit — a sell, a perp close, a swap out of a
    copied token, an open against the copied side — is routed before any size
    knob is consulted: it unwinds what this duty copied, whatever the leader's
    notional, so refusing it for being under MIN_LEADER_USD or off CHAIN_IDS
    would leave the copy open after the leader is out.
    """
    usd = ev.usd_value or 0
    under_min = MIN_LEADER > 0 and usd < MIN_LEADER
    thin = "$%.2f is under MIN_LEADER_USD $%.2f" % (usd, MIN_LEADER)

    if ev.is_perp:
        if not MIRROR_PERPS:
            return None, "a perp, and MIRROR_PERPS is off"
        if ev.is_close or flips_copy(ev):
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
        if ev.is_sell:
            return "stock-sell", None
        if under_min:
            return None, thin
        # CHAIN_IDS is deliberately not applied to a stock: its venue is a
        # name, not a chain id, so the filter would drop every one of them.
        return "stock-buy", None

    if ev.is_sell:
        return "spot-sell", None
    if swaps_out_of_copy(ev):
        # The copied token is sold; the one the leader swapped into is not
        # bought.
        if not MIRROR_SELLS:
            return None, "a swap out of a copied token, and MIRROR_SELLS is off"
        return "spot-sell", None
    if CHAIN_IDS and ev.asset.chain_id not in CHAIN_IDS:
        return None, "chain %s is not in CHAIN_IDS" % (ev.asset.chain_id,)
    if under_min:
        return None, thin
    return "spot-buy", None


#: The routes that put money in. Only these are sized; an exit's size is the copy.
ENTRIES = ("spot-buy", "perp-open", "stock-buy")


def size_for(ev):
    """Dollars to put behind a buy or open, or 0 with a reason.

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
        return spot_sell(ev, key)
    if route == "perp-close":
        return perp_close(ev, key)
    if route == "perp-open":
        if not isinstance(ev.is_short, bool):
            return None
        return perp_open(ev, usd, key)
    if route == "stock-buy":
        return stock_buy(ev, usd, key)
    if route == "stock-sell":
        return stock_sell(ev, key)
    return None


def again(key):
    """A leader event whose leg is already in the book: redelivered after a
    restart. Re-sent with the same argv and key, so the ledger answers it as
    the replay it is — never re-sized off a copy the first send already
    reserved from."""
    leg = BOOK["legs"][key]
    if leg.get("done"):
        return True, None
    return filed(leg["args"], key, leg["sentence"])


#: How a copied leg reads on the owner's Duties row: "Copied VIRTUAL buy".
VERBS = {
    "spot-buy": "buy", "spot-sell": "sell", "stock-buy": "buy", "stock-sell": "sell",
    "perp-close": "close",
}


def burst(batch):
    """One burst of leader trades, start to finish."""
    del BURST_KEYS[:]
    del UNCONFIRMED[:]
    recheck()

    told = []
    skipped = 0
    failed = 0
    last_copied = None
    asked = False
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
            BOOK["seen"][str(ev.id)] = time.time()
            continue

        # An exit is not sized — it unwinds the copy, whatever the leader's
        # notional was.
        usd = 0
        if route in ENTRIES:
            usd, reason = size_for(ev)
            if usd <= 0:
                skipped += 1
                say("skipped %s: %s" % (ev.id, reason))
                BOOK["seen"][str(ev.id)] = time.time()
                continue

        key = key_for(ev, route)
        if key not in BOOK["legs"] and str(ev.id) in BOOK["seen"]:
            # Handled once already. Its route can differ now (a flip that
            # closed the copy reads as an open the second time), and one
            # leader event never becomes a second, different order.
            skipped += 1
            say("skipped %s: already handled" % (ev.id,))
            continue
        if key in BOOK["legs"]:
            result = again(key)
        else:
            result = place(ev, route, usd, key)
        BOOK["seen"][str(ev.id)] = time.time()
        if result is None:
            skipped += 1
            say("skipped %s: the event named no side" % (ev.id,))
            continue
        ok, summary = result
        if ok == "skip":
            skipped += 1
            say("skipped %s: %s" % (ev.id, summary))
        elif ok and summary is None:
            say("skipped %s: already filed (replay of %s)" % (ev.id, key))
        elif ok:
            told.append(summary)
            # The ticker the owner knows, never the address behind it.
            verb = VERBS.get(route, "trade")
            if route == "perp-open":
                verb = "short" if ev.is_short else "long"
            # A perp close is neither a buy nor a sell, so `asset` reads the
            # wrong leg; the coin is on whichever side carries a symbol.
            coin = ev.asset.symbol or ev.token_out or ev.token_in
            if route == "spot-sell":
                coin = ev.token_in or coin
            last_copied = "%s %s" % (coin, verb) if coin else "a %s" % verb
            asked = asked or "approval" in summary
        else:
            skipped += 1
            failed += 1
            say("skipped %s: %s" % (ev.id, summary))

    settle(list(BURST_KEYS), SETTLE_WAIT_S, Fresh())
    prune()
    save()

    # The owner's Duties row: recipe.json `status` words each state.
    if asked:
        bevo.status("approval")
    elif last_copied:
        bevo.status("copied", what=last_copied)
    elif failed:
        bevo.status("failed")
    elif skipped:
        bevo.status("skipped")

    # One note per burst, and only when something actually happened. A duty
    # that notifies on every quiet batch is one the owner mutes, after which
    # the note that mattered is unheard too.
    #
    # The note is read by the owner in this duty's own chat: one line per
    # trade, no duty name in front (the chat's header names it), the token as
    # `$TICKER` (a chip that opens its card) and the amount. Why a trade was
    # skipped stays in the log.
    for what in UNCONFIRMED:
        told.append("%s — could not be confirmed, so it is not counted as copied" % what)
    if told:
        if skipped:
            told.append("Didn't copy %d other trade%s" % (skipped, "" if skipped == 1 else "s"))
        bevo.notify("\n".join(told), quiet=True)


def main():
    if open_book():
        # Once, when the book starts: whatever an earlier version copied is
        # not in it, and is the owner's to sell or close now.
        bevo.notify("tracking")
    bevo.status("starting")
    for batch in bevo.batches(seconds=3):
        burst(batch)


if __name__ == "__main__":
    main()
