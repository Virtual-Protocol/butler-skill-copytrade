"""The copy book's money rules: what is counted as copied, and what a leader's
exit sells or closes. Run against the hub's offline `bevo` stub
(Virtual-Protocol/butler-skills `tests/stub_bevo.py`), found at
$BUTLER_SKILLS_DIR or a butler-skills checkout beside this one.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _hub():
    candidates = []
    if os.environ.get("BUTLER_SKILLS_DIR"):
        candidates.append(Path(os.environ["BUTLER_SKILLS_DIR"]))
    candidates.append(ROOT.parent / "butler-skills")
    if len(ROOT.parents) > 3:
        # A worktree at <repo>/.claude/worktrees/<name>: the hub's worktree of the same name.
        candidates.append(ROOT.parents[3] / "butler-skills" / ".claude" / "worktrees" / ROOT.name)
        candidates.append(ROOT.parents[3] / "butler-skills")
    for hub in candidates:
        if (hub / "tests" / "stub_bevo.py").is_file():
            return hub
    pytest.exit("no butler-skills checkout with tests/stub_bevo.py; set BUTLER_SKILLS_DIR", 2)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bevo = _load("bevo", _hub() / "tests" / "stub_bevo.py")
os.environ["PARAMS"] = json.dumps({
    "SIZING": "fixed", "SIZE_USD": 25,
    "MIRROR_SELLS": True, "MIRROR_PERPS": True, "MIRROR_STOCKS": True,
})
duty = _load("copytrade_duty", ROOT / "duty.py")

LEADER = "11111111-1111-1111-1111-111111111111"
VIRTUAL = "0x0b3e328455c4059eeb9e3f84b5543f74e24e7e1b"
ACCEPTED = {"status": "accepted", "ok": True}
ASKED = {"status": "manual_signing_required", "ok": True, "asked": True}
REFUSED = {"status": "refused", "ok": False, "error": "pocket_empty"}


def key(kind, event_id):
    return "copy:%s:%s:%s" % (bevo.SERVICE_ID, kind, event_id)


def trade(event_id, **row):
    row.setdefault("principalId", LEADER)
    row.setdefault("chainId", "8453")
    return {"kind": "trade", "event": dict(row, id=event_id)}


def buy(event_id, amount_out=None, fill=None):
    return trade(event_id, type="SWAP", direction="buy", tokenInSymbol="USDC",
                 tokenOutSymbol="VIRTUAL", tokenOutAddress=VIRTUAL, usdValue=25,
                 amountOut=amount_out, fillPriceUsd=fill)


def sell(event_id, amount_in=None, by="address"):
    row = dict(type="SWAP", direction="sell", tokenInSymbol="VIRTUAL", tokenOutSymbol="USDC",
               amountIn=amount_in, usdValue=30)
    if by == "address":
        row["tokenInAddress"] = VIRTUAL
    return trade(event_id, **row)


def perp(event_id, coin, side, reduce_only=None, hl_event=None):
    return trade(event_id, type="HL", direction=side, tokenOutSymbol=coin, usdValue=500,
                 leverage=5, reduceOnly=reduce_only, hlEvent=hl_event)


def stock(event_id, direction):
    return trade(event_id, type="SWAP", direction=direction,
                 tokenOutSymbol="AAPL" if direction == "buy" else "USDC",
                 tokenInSymbol="AAPL" if direction == "sell" else "USDC",
                 usdValue=25, command="acp trade --token AAPL --amount-usdc 25")


def assets(virtual=180.5, positions=(), stocks=()):
    tokens = []
    if virtual:
        tokens.append({"chainId": 8453, "tokenAddress": VIRTUAL, "symbol": "VIRTUAL",
                       "balance": virtual, "usdPrice": 1.3})
    return {
        "spot": {"available": True, "tokens": tokens, "stocks": list(stocks)},
        "perps": {"available": True, "positions": list(positions)},
    }


def position(coin, side, size, mark):
    return {"coin": coin, "side": side, "size": size, "markPriceUsd": mark,
            "positionValueUsd": size * mark}


class World:
    """bevo-server as the duty sees it: `acp trade` answers, the ledger's
    verdicts, and the owner's fresh portfolio."""

    def __init__(self):
        self.answers = {}
        self.fates = {}
        self.body = assets()
        self.calls = []

    def run(self, argv, **_):
        self.calls.append(list(argv))
        answer = self.answers.get(argv[-1], ACCEPTED)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answer), stderr="")

    def settled(self, key, route=None, wait=0):
        return self.fates.get(key, "waiting")

    def read(self, path, params=None):
        if path != "/user-assets" or self.body is None:
            raise bevo.BevoError("no answer for %s" % path)
        return self.body

    def arg(self, flag, call=-1):
        argv = self.calls[call]
        return argv[argv.index(flag) + 1]


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bevo, "state", {})
    world = World()
    monkeypatch.setattr(subprocess, "run", world.run)
    monkeypatch.setattr(bevo, "settled", world.settled)
    monkeypatch.setattr(bevo, "read", world.read)
    monkeypatch.setattr(bevo, "exec_status", lambda key, route="trade": {"state": "unknown"})
    monkeypatch.setattr(duty, "SETTLE_WAIT_S", 0)
    duty.open_book()
    return world


def entry(rail_key):
    return duty.BOOK["pos"].get("%s|%s" % (LEADER, rail_key))


def units(rail_key):
    found = entry(rail_key)
    return found["units"] if found else 0


SPOT = "spot|8453|" + VIRTUAL


def later(monkeypatch, batch=()):
    """A later burst, with every open leg due for a recheck."""
    monkeypatch.setattr(duty, "RECHECK_S", -1)
    duty.burst(list(batch))


# ── what counts as copied ────────────────────────────────────────────────────


def test_an_accepted_buy_counts_only_once_it_executes(world, monkeypatch):
    duty.burst([buy(1)])
    assert units(SPOT) == 0
    world.fates[key("trade", 1)] = "executed"
    later(monkeypatch)
    assert units(SPOT) == pytest.approx(25 / 1.3)


def test_an_approval_card_counts_once_confirmed(world, monkeypatch):
    world.answers[key("trade", 1)] = ASKED
    world.fates[key("trade", 1)] = "approval"
    duty.burst([buy(1)])
    assert units(SPOT) == 0
    world.fates[key("trade", 1)] = "executed"
    later(monkeypatch)
    assert units(SPOT) == pytest.approx(25 / 1.3)


def test_a_rejected_buy_is_never_counted(world, monkeypatch):
    world.answers[key("trade", 1)] = ASKED
    world.fates[key("trade", 1)] = "dropped"
    world.answers[key("trade", 2)] = REFUSED
    world.fates[key("trade", 2)] = "executed"  # the answer already said no
    duty.burst([buy(1), buy(2)])
    later(monkeypatch)
    assert units(SPOT) == 0


@pytest.mark.parametrize("answer", [
    {"status": "executed", "ok": True, "executed": True, "replay": True},
    {"status": "accepted", "ok": True, "replay": True},
])
def test_a_replayed_buy_is_counted_once(world, monkeypatch, answer):
    world.answers[key("trade", 1)] = answer
    world.fates[key("trade", 1)] = "executed"
    duty.burst([buy(1)])
    later(monkeypatch, [buy(1)])
    later(monkeypatch, [buy(1)])
    assert units(SPOT) == pytest.approx(25 / 1.3)
    assert all(call == world.calls[0] for call in world.calls)


# ── what an exit sells ───────────────────────────────────────────────────────


def copied_virtual(world, monkeypatch, amount_out=None, fill=None):
    world.fates[key("trade", 1)] = "executed"
    duty.burst([buy(1, amount_out=amount_out, fill=fill)])
    world.calls.clear()


def test_a_sell_sells_the_copy_not_the_holding(world, monkeypatch):
    copied_virtual(world, monkeypatch)
    duty.burst([sell(2, amount_in=25, by="symbol")])
    assert float(world.arg("--amount-in")) == pytest.approx(25 / 1.3, abs=1e-8)
    assert float(world.arg("--amount-in")) <= 25 / 1.3


def test_the_copy_shrinks_to_what_the_owner_still_holds(world, monkeypatch):
    copied_virtual(world, monkeypatch)
    world.body = assets(virtual=10)
    duty.burst([sell(2)])
    assert world.arg("--amount-in") == "10"
    assert units(SPOT) == 0


def test_nothing_copied_places_nothing(world):
    duty.burst([sell(2), perp(3, "ETH", "short", hl_event="liquidation"), stock(4, "sell")])
    assert world.calls == []
    assert bevo.STATUS_REPORTS[-1]["state"] == "skipped"


def test_a_partial_exit_sells_the_leaders_share_then_the_rest(world, monkeypatch):
    copied_virtual(world, monkeypatch, amount_out=100, fill=2.5)
    world.fates[key("trade", 2)] = "executed"
    duty.burst([sell(2, amount_in=40)])
    assert world.arg("--amount-in") == "4"
    duty.burst([sell(3, amount_in=60)])
    assert world.arg("--amount-in") == "6"
    assert units(SPOT) == 0


def test_an_unstated_share_sells_the_whole_copy(world, monkeypatch):
    copied_virtual(world, monkeypatch, fill=2.5)
    duty.burst([sell(2, amount_in=40)])
    assert world.arg("--amount-in") == "10"


def test_a_dropped_sell_is_refunded(world, monkeypatch):
    copied_virtual(world, monkeypatch, amount_out=100, fill=2.5)
    world.answers[key("trade", 2)] = REFUSED
    duty.burst([sell(2, amount_in=40)])
    assert units(SPOT) == pytest.approx(10)
    world.answers[key("trade", 3)] = ASKED
    world.fates[key("trade", 3)] = "dropped"
    duty.burst([sell(3, amount_in=30)])
    later(monkeypatch)
    assert units(SPOT) == pytest.approx(10)


def test_a_server_error_waits_for_the_ledger(world, monkeypatch):
    # A 5xx may have been filed before it failed: never counted as dropped.
    world.answers[key("trade", 1)] = dict(REFUSED, httpStatus=502)
    world.fates[key("trade", 1)] = "executed"
    duty.burst([buy(1)])
    later(monkeypatch)
    assert units(SPOT) > 0

def test_a_stock_sell_sells_the_copied_shares(world, monkeypatch):
    world.body = assets(stocks=[{"ticker": "AAPL", "chain": "eth", "shares": 12.5,
                                 "usdPerShare": 190.0}])
    world.fates[key("stock-buy", 1)] = "executed"
    duty.burst([stock(1, "buy")])
    duty.burst([stock(2, "sell")])
    assert world.arg("--amount-shares") == duty.fmt(duty.floor8(25 / 190))


# ── perps ────────────────────────────────────────────────────────────────────


def copied_btc_long(world):
    world.body = assets(positions=[position("BTC", "long", 0.0125, 60250.0)])
    world.fates[key("perp-open", 1)] = "executed"
    duty.burst([perp(1, "BTC", "long")])
    world.calls.clear()


def test_a_perp_close_closes_the_copy_reduce_only(world):
    copied_btc_long(world)
    duty.burst([perp(2, "BTC", "long", reduce_only=True)])
    argv = world.calls[-1]
    assert world.arg("--side") == "short"
    assert float(world.arg("--amount-usdc")) == pytest.approx(25, abs=1e-6)
    assert "--reduce-only" in argv and "--size" not in argv


@pytest.mark.parametrize("live", [[], [position("BTC", "short", 0.01, 60250.0)]])
def test_a_flat_or_turned_position_clears_the_copy_without_an_order(world, live):
    copied_btc_long(world)
    world.body = assets(positions=live)
    duty.burst([perp(2, "BTC", "long", reduce_only=True)])
    assert world.calls == []
    assert units("perp|BTC") == 0


def test_an_open_against_the_owners_own_side_is_refused(world):
    world.body = assets(positions=[position("ETH", "short", 0.42, 2478.0)])
    duty.burst([perp(1, "ETH", "long")])
    assert world.calls == []
    assert units("perp|ETH") == 0


def test_an_exit_never_sells_the_owners_lot_on_another_chain(world, monkeypatch):
    copied_virtual(world, monkeypatch)
    world.body = assets(virtual=0)
    world.body["spot"]["tokens"].append({"chainId": 1, "tokenAddress": VIRTUAL, "symbol": "VIRTUAL",
                                         "balance": 5000, "usdPrice": 1.3})
    duty.burst([sell(2)])
    assert world.calls == []
    assert units(SPOT) == 0


def test_a_symbol_two_tokens_share_is_not_sold(world, monkeypatch):
    world.fates[key("trade", 1)] = "executed"
    duty.burst([trade(1, type="SWAP", direction="buy", tokenInSymbol="USDC",
                      tokenOutSymbol="VIRTUAL", usdValue=25)])
    world.body["spot"]["tokens"].append({"chainId": 8453, "tokenAddress": "0x" + "9" * 40,
                                         "symbol": "VIRTUAL", "balance": 9000, "usdPrice": 1.3})
    world.calls.clear()
    duty.burst([sell(2, by="symbol")])
    assert world.calls == []


def test_a_redelivered_flip_is_not_reopened(world, monkeypatch):
    copied_btc_long(world)
    world.fates[key("perp-close", 2)] = "executed"
    duty.burst([perp(2, "BTC", "short")])
    assert "--reduce-only" in world.calls[-1]
    world.body = assets(positions=[])
    world.calls.clear()
    duty.burst([perp(2, "BTC", "short")])
    assert world.calls == []
