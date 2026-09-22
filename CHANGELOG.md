# Changelog

## 9.0.0

**The daily caps are gone.** `MAX_PER_DAY` and `MAX_USD_PER_DAY` are removed, and
with them the ledger the duty kept in its own log to count them: no `requested` /
`released` lines, no re-reading `duty.log` / `duty.log.1`, no opening leg trimmed to
the day's remaining dollars, and none skipped because the rotated log starts after
midnight UTC. What a duty may spend without asking is the pocket the owner funds in
the app, and bevo-server enforces it. `MAX_USD`, the per-trade ceiling, stays.

- A leader event redelivered after a restart is still one leg: the idempotency keys
  are unchanged, so bevo-server's ledger answers the second `replay`.
- A `pocket_empty` / `wallet_short` refusal is reported like any other refusal.
- `recipe.json` goes to version 9 and supersedes `copytrade@8`. A duty already filed
  from `copytrade@6`–`@8` keeps its stored code, and with it both caps; re-file it
  to pick this up.

## 8.0.0

**A leg bevo-server refuses before it runs no longer counts toward the daily
caps.** bevo-server now answers a duty's trade with a definitive, pre-execution
402 — nothing sent, nothing reserved — when the pocket that funds it is used up
(`pocket_empty`) or, shipping next, when the owner's wallet can't cover what the
pocket could (`wallet_short`). `filed()` now recognises those two codes and calls
a new `release(key)`, which writes a `released <UTC time> key=<key>` line and
drops the key from the in-process ledger; the ledger reader walks `requested`
and `released` lines in order, so a released key is not counted and a later
retry of the same key counts again. Every other refusal — a 409, a timeout, an
unparseable answer — may have landed, and keeps counting exactly as before.

`recipe.json` goes to version 8 and supersedes `copytrade@7`.

## 7.0.0

**Sells are mirrored by default.** `MIRROR_SELLS` now defaults to `true`; an
owner who wants buys only turns it off. A duty already filed under `@6` keeps
the settings it stored — its `PARAMS` already carry `MIRROR_SELLS: false`
explicitly, since defaults are filled in at filing time, not read.

**The sizing figure is required for its mode.** `recipe.json`'s `params` gains
an `allOf` conditional: `SIZE_USD` is required when `SIZING` is `fixed`,
`SHARE` is required when `SIZING` is `cash_share` or `leader_share`. Before
this, `SIZING: fixed` with no `SIZE_USD` filed cleanly and the duty never
traded; now the Butler asks for the figure instead.

`recipe.json` goes to version 7 and supersedes `copytrade@6`.

## 6.0.0

**Daily caps, counted from the duty's own log.** Every leg is now written to the
log through `bevo.log()` as `requested <UTC time> key=<idempotency key> route=<leg>
usd=<value>` *before* its `acp trade` runs, and two new settings are counted from
those lines per UTC day:

- `MAX_PER_DAY` (default `20`) — opening legs: spot buys, stock buys, perp opens.
- `MAX_USD_PER_DAY` (unset by default) — dollars put into those legs. A buy that
  would cross it is trimmed to what is left, the way `MAX_USD` trims one leg.

The dollar value on each line is the duty's own figure — a buy's size, a perp open's
margin (its notional over the leverage it is placed at), a sell's amount at the
holding's price, a close's position value — never the server's. A perp that would
cross the dollar cap is trimmed until its margin fits. Exits (spot and stock sells, perp closes) are recorded with their value
but never capped. A leader event redelivered after a restart is re-sent under its
original key and not counted twice; a leg refused by the rail still counts. When the
rotated log (`duty.log.1`) starts after midnight UTC, today's earliest lines may be
gone, so opening legs are skipped rather than counted low.

Every other log line is flattened onto one line first: a leader's token symbol is
untrusted text, and a newline inside it must not be able to write a ledger entry.

`recipe.json` goes to version 6 and supersedes `copytrade@5`. A duty already filed
keeps its stored code; re-file it to pick this up.

## 5.0.0

**Breaking: this became a duty template bundle, not a skill.** The repo root
is now `recipe.json` + `duty.py` + `README.md` — no frontmatter, no prose
playbook. `SKILL.md` is gone; `duty.py` is now the whole execution, run
verbatim as the duty's stored `code`. This 5.0.0 counts a different artefact
than the old `4.0.1` did — that number tracked `SKILL.md`'s frontmatter
`version`, which no longer exists.

- The retired `bevo.trade(command=…, idempotency_key=…)` money rail (itself
  a 4.0.0 replacement for the even-older `bevo.buy`/`sell`/`long`/`short`/
  `close`/`stock_buy`/`stock_sell` verbs) is gone too — those SDK verbs were
  deleted from `bevo.py` on 2026-09-21 with no shim. Every leg now calls
  `subprocess.run(["acp", "trade", ..., "--idempotency-key", key])` directly.
- Settings, gates, idempotency-key shapes and command grammar per leg (spot /
  perp / tokenized stock) are unchanged from 4.0.1's intent; only the rail
  they're placed through moved.
- `recipe.json` declares `"supersedes": ["copytrade@3", "copytrade@4"]`, so a
  duty whose stored ref still reads `copytrade@4` (or `@3`) keeps resolving.

### Migration — existing duties must be re-filed

A duty filed from the old bundle keeps running its own stored `duty.py` and
does not auto-migrate. Re-file it (`duty_create` with `recipe: "copytrade@5"`)
to pick up this version; there is no in-place upgrade.

## 4.0.1

**Trimmed to just enough context.** SKILL.md 14,234 -> 11,370 chars (-20.1%), README
2,820 -> 2,343. Explanation only: every knob, gate and command shape is unchanged, and
the hub replay produces byte-identical commands for all seven legs.

- duty.py's docstrings no longer narrate which SDK function each fallback chain "used
  to" be. Naming a removed rail re-teaches it; each rule now stands on its own.
- The one-off and duty procedures no longer restate each other's gates.

## 4.0.0

**Breaking: the SDK's money verbs are gone.** `bevo.buy`/`sell`/`long`/`short`/
`close`/`stock_buy`/`stock_sell` were each a one-line rewrite of an `acp trade`
CLI string that hid the grammar and drifted from it; `bevo.trade(command=…)` is
now the ONE money rail. `duty.py` builds every `acp trade` command directly —
spot buy/sell, perp open/close and both tokenized-stock shapes — and does the
sizing arithmetic the verbs used to do (sell/close quantities, prices, holding
and position lookups) itself, off a single
`bevo.read("/user-assets", {"fresh": 1})` per leg.

Every knob, gate, idempotency key, venue minimum and the never-guess-a-bare-ticker
rule are unchanged. The commands are flag-for-flag what the verbs emitted. But
the arithmetic AROUND them moved into the duty, and three things it used to
inherit from the SDK are genuinely different:

- **A spot sell now refuses when nothing is held, and never sells more than the
  holding.** `bevo.sell(usd=)` sized purely off a price — dollars ÷ price, with
  no holdings read at all — so a mirrored sell of a token the owner did not own
  (or owned less of than the leader's size) went to the server to be refused
  there, or filled short. The duty reads the holding first, skips the leg when
  there is none, and caps the quantity at what is actually held. A stock sell
  already behaved this way (`stock_sell` refused `NOT_HELD` and capped at the
  share count); a spot sell now matches it.
- **The price source changed.** The verbs priced through
  `/butler-read/token-price` (CoinGecko's canonical listing, 10 s cache) and
  fell back to `token-search`. The duty prices off the holding row it just read
  — that row's own `usdPrice`, or a stock row's `usdPerShare`, or that same
  row's `usdValueUsd / shares` — and only then asks `token-search`, by address
  and then by symbol. Same intent, different numbers at the margin: an
  illiquid token can quote differently on the portfolio row than on the
  canonical listing, so a sell's share count can differ slightly from what
  3.x would have computed for the same event.
- **An out-of-range leverage clamps instead of refusing.** `bevo.long`/`short`
  answered `LEVERAGE_OUT_OF_RANGE` and filed nothing outside 1–50x. The duty
  clamps into that range and logs the clamp, so a leader's 100x is copied at
  the ceiling rather than dropped.

Also better, not just different: the sizing read is one snapshot per leg rather
than a lookup per question (`sell` priced and read holdings separately, `close`
made its own `/user-assets` call), and that read carries `fresh=1` explicitly in
the duty's own code — the flag every removed verb passed, and the one thing a
hand-rolled wallet read must not drop, since the server's portfolio cache can be
ten minutes stale and would size a burst's second leg off the balance from
before its first.

### Migration — existing duties must be re-created

**A version bump does not migrate a duty already in the field.** A filed duty
stores its own snapshot of `duty.py` at create time, and the supervisor
materializes that stored copy beside the container's CURRENT `bevo.py` on every
run (`api/bevo_duty/code/supervisor.py`, `_materialize`). So an existing
copytrade duty keeps running its 3.x code — the code that calls `bevo.buy` /
`sell` / `long` / `short` / `close` / `stock_buy` / `stock_sell` — against an
SDK where those names no longer exist, and every leg dies on `AttributeError`.
Installing or updating this skill changes nothing about it.

Every copytrade duty created before 4.0.0 must be **deleted and re-created**
from this version. There is no in-place fix and no automatic migration.

### Container requirement — the other way round from 3.x

4.0.0's `duty.py` uses only `bevo.trades`, `bevo.read`, `bevo.trade`,
`bevo.is_stock`, `bevo.balance`, `bevo.log`, `bevo.BevoError` and
`bevo.SERVICE_ID`, all of which predate the verb removal — so it runs on ANY
container, before or after the SDK drops them. The constraint runs the other
way: a duty FILED from 3.x breaks the moment its container's SDK drops the
verbs, which is what the Migration note above is about.

## 3.1.1

The fork paragraph said "add the knobs you need" without saying where. `merged_env`
rejects any `env` key the skill never declared, so a forked duty whose new code reads a
new knob is refused at create until that knob is a `params` entry in the fork's own
frontmatter. Says so now.

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
