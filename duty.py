"""butler-copytrade duty — mirrors LEADER's trades on the Virtuals rails, one
trade per leader event, never twice. Every knob below is a word the owner
actually said; nothing here has a money default of its own. See SKILL.md.
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
CHAIN_IDS = [int(c) for c in json.loads(os.environ.get("CHAIN_IDS") or "[]")]
MIRROR_SELLS = str(os.environ.get("MIRROR_SELLS") or "false").lower() == "true"
MIN_LEADER_USD = float(os.environ.get("MIN_LEADER_USD") or 0)


def size_for(trade):
    """The USD to put into this copy, or 0 to skip it.

    `cash_share` reads the LIVE wallet on every event — the owner said "a
    share of my wallet", and a figure worked out when the duty was written is
    a different promise by the second trade. `leader_share` is a share of what
    the leader spent, which the event carries. `MAX_USD` caps all three, and
    only when the owner named a ceiling."""
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


def main():
    for trade in bevo.trades():
        # The trigger already scopes the feed to LEADER (SKILL.md, Duty
        # procedure): a second owner check here would drop every event when
        # the owner gave a wallet and the feed reports a principal id.
        if trade.is_sell:
            if not MIRROR_SELLS:
                continue
        elif not trade.is_buy:
            continue  # a perp, or a row the feed could not classify

        if CHAIN_IDS and trade.chain_id not in CHAIN_IDS:
            continue
        if MIN_LEADER_USD and (trade.usd_value or 0.0) < MIN_LEADER_USD:
            continue
        if trade.asset.ref is None:
            bevo.log(f"copytrade skip event={trade.id}: the feed named no token")
            continue

        size = size_for(trade)
        if size <= 0:
            bevo.log(f"copytrade skip event={trade.id}: size 0 (LEADER={LEADER} SIZING={SIZING})")
            continue

        # One key per LEADER EVENT, never a timestamp: the service pump can
        # replay rows it already handed over after a restart, and the same key
        # answers "already filed" instead of trading twice.
        key = f"copytrade:{bevo.SERVICE_ID}:{trade.id}"
        # trade.asset is the exact token on the exact chain the leader traded —
        # its address verbatim plus the feed's own chain id. Never trade.token,
        # which is a display name, and never a chain of our own.
        if trade.is_sell:
            result = bevo.sell(trade.asset, usd=size, idempotency_key=key)
        else:
            result = bevo.buy(trade.asset, usd=size, idempotency_key=key)
        # No notify(): the server tells the owner about every duty trade — a
        # receipt when it executes, an approval card when it asks.
        bevo.log(f"copytrade event={trade.id} {trade.asset} {result.summary} key={key}")


if __name__ == "__main__":
    main()
