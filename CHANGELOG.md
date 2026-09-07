# Changelog

## 2.0.0

**Breaking: every money default is gone.** The old template shipped `$25` a copy,
a `$50` cap and `CHAIN_IDS: [8453]`, and a 20-scenario test run showed what that
costs: "20% of my wallet per trade" became $25, and a leader's tokenized-stock
buys on Robinhood were dropped by a Base filter nobody asked for. The knobs are
now the owner's words and nothing else.

- `SIZING` (required, no default): `fixed` (`SIZE_USD`) | `cash_share` (`SHARE`
  of the owner's spendable cash, read LIVE on every event) | `leader_share`
  (`SHARE` of the leader's own size). `MAX_USD` caps, only when they named one.
  `COPY_USDC_PER_TRADE`, `COPY_MAX_USDC` and `COPY_RATIO` are removed.
- `CHAIN_IDS` defaults to `[]` — copy each trade on the chain the leader
  actually traded on. `bevo-automation` refuses a create whose `CHAIN_IDS`
  narrows to a chain the owner's spec never names.
- `MIRROR_SELLS` (default false) — sells are mirrored only when they asked;
  `MIN_LEADER_USD` skips a leader's small trades.
- `duty.py` moves onto the rails: `bevo.buy(trade.asset, …)` /
  `bevo.sell(trade.asset, …)` instead of a hand-built `acp trade` command, so a
  copy lands on the exact token and the exact chain the leader traded, with the
  address verbatim. The hand-rolled seen-set is gone — the per-event
  idempotency key already makes a replayed event a no-op — and so is the
  `manual_signing_required` note: the server tells the owner about every duty
  trade.
- Requires a container whose SDK has `TradeEvent.asset` and `bevo.buy(chain=)`.

## 1.1.0

- The namespace key is `metadata.butler` (was `metadata.bevo`).

## 1.0.1

- Renamed from `bevo-copytrade`; docs (validate with the hub's published standalone tools,
  no registry checkout) and a trimmed playbook — the skill is the delta over AGENTS.md.

## 1.0.0

- Initial release: one-off and duty modes, buy-only copy trading with per-event
  idempotency keys and a bounded local seen-set.
