# Changelog

## 3.1.0

**A rule the knobs cannot express is a fork, not a dead end.** The skill told
Butler to "never hand-write the duty's code" and stopped there, so an owner who
wanted anything outside the twelve knobs — a filter on the token, a second
leader, a cap on total exposure — got told no. `bevo-hub fork` has always been
the answer and the hub never overwrites a fork; the skill just never said so.

- `## Customize` ends with the fork path: what to reach for it for, where the
  seam is in `duty.py`, and the reads a forked condition would use.
- Duty step 5 keeps "do not hand-write this duty" but now points at the fork
  instead of dead-ending, and drops the `@3.0.0` pin — it was already a version
  behind at 3.0.1, and a pin cannot name a fork.

## 3.0.1

**Fixes a 3.0.0 regression that made the skill uninstallable in the US, Canada
and Switzerland.** 3.0.0 declared `requires.gates: [canSwap, canPerp, canStock]`.
A declared gate is an INSTALL requirement, not a list of what the skill can use:
the hub refuses the install outright (422 `requirements_unmet`) when any one of
them is false for that owner. A US owner is `canPerp:false, canStock:false`, so
3.0.0 took away the spot copy-trading they had at 2.0.0.

- `requires.gates` is back to `["canSwap"]`. Perps and stocks stay behind
  `MIRROR_PERPS` / `MIRROR_STOCKS`, both default false, and a leg the owner's
  region bars already refuses per-leg as a `TradeResult` — which is what the
  "A perp or stock leg refused for a permission gate" row in Failure handling
  was always describing. That row was unreachable while the gate blocked the
  install.

## 3.0.0

**Perps and tokenized stocks, each behind its own switch.** 2.0.0 mirrored spot
only; a leader's longs and their AAPL buys went past unseen. Both are now
copied when the owner asks for them, and only then.

- `MIRROR_PERPS` (default false) mirrors leveraged opens and, critically,
  CLOSES — including a stop or a liquidation the exchange forced. A close is
  risk coming off, so it is never gated by `MIRROR_SELLS`, `MIN_LEADER_USD`,
  `CHAIN_IDS` or a size knob: if the leader is out, the copy gets out.
  `PERP_LEVERAGE` (0 = the leader's own) and `PERP_MAX_LEVERAGE` bound it.
- `MIRROR_STOCKS` (default false) mirrors tokenized stock trades. The feed
  carries no flag for a stock — it is a SWAP row naming a ticker and no address
  — so the duty asks the catalog once per symbol and files the stock command
  shape, never the swap shape a spot token takes.
- `SIZING` on a perp sizes the POSITION (notional), never the margin. The
  leader's own `usdValue` is notional too, so `leader_share` compares like with
  like.
- `CHAIN_IDS` is now explicitly spot-only. A perp has no chain and a stock's
  chain belongs to its cash leg; filtering either on it silently dropped them.
- Venue minimums are enforced before filing, not rounded up to: $2 spot,
  $15 perp, $15 stock buy. `MIN_LEADER_USD` applies to spot, stocks and perp
  opens alike — never to a close.
- A leader's bare ticker that is not a tokenized stock you can trade is
  SKIPPED, never guessed onto the spot rail: a bare-symbol swap resolves to
  whatever token wears that ticker. A region that bars stocks looks identical
  to "not a stock" here, and both end the same way.
- Requires a server that publishes `reduceOnly` and `hlEvent` on the public
  trade feed, and a container whose SDK has `TradeEvent.is_perp` / `is_close`
  and `bevo.stock_buy` / `bevo.stock_sell` / `bevo.is_stock`. Without them a
  perp close is indistinguishable from an open and MUST NOT be mirrored.

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
