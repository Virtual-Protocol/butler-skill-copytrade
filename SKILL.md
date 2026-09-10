---
name: butler-copytrade
description: Copy, mirror or follow another member's trades — spot, tokenized stocks and perps, once or as a standing duty sized from your owner's own words.
version: 4.0.0
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"butler":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","copy trading","copy buys","mirror wallet","mirror trades","follow trader","follow wallet","copy perps","mirror perps","copy stocks"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","GET /butler-read/user-assets","GET /butler-read/token-search","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who should I copy?"},{"name":"SIZING","type":"enum","values":["fixed","cash_share","leader_share"],"required":true,"ask":"how much per copy — a fixed dollar figure, a share of your own cash, or a share of what they trade?"},{"name":"SIZE_USD","type":"usd","min":2,"max":10000,"help":"dollars per copy (SIZING=fixed)"},{"name":"SHARE","type":"number","min":0,"max":1,"help":"fraction for cash/leader share sizing (0.2 = 20%)"},{"name":"MAX_USD","type":"usd","min":2,"max":100000,"help":"per-trade ceiling"},{"name":"CHAIN_IDS","type":"chainIds","default":[],"help":"empty = the leader's chain; set only when specified. Spot only"},{"name":"MIRROR_SELLS","type":"bool","default":false,"help":"copy spot and stock sells too"},{"name":"MIN_LEADER_USD","type":"usd","default":0,"min":0,"max":100000,"help":"ignore trades smaller than this; never applied to a perp close"},{"name":"MIRROR_PERPS","type":"bool","default":false,"help":"copy their leveraged positions too"},{"name":"MIRROR_STOCKS","type":"bool","default":false,"help":"copy their tokenized stock trades too"},{"name":"PERP_LEVERAGE","type":"number","default":0,"min":0,"max":50,"help":"fixed leverage to open at; 0 = take the leader's own"},{"name":"PERP_MAX_LEVERAGE","type":"number","default":0,"min":0,"max":50,"help":"leverage ceiling; 0 = no ceiling"}],"dutyTemplate":"duty.py"}}
---

## When to use

Your owner asks to copy, mirror or follow another member's or wallet's trading —
once ("copy their last buy") or standing ("copy every buy they make"). Spot
tokens, tokenized stocks and leveraged perps, each behind its own switch.

**The knobs below ARE your owner's words.** This skill has no money defaults: no
size, no chain, no sells, no perps, no stocks unless they said so. If they did
not name a size, ask that one question before you create anything — a default
here is you deciding how much of their money to spend.

## Before you start

Resolve the leader:

```bash
bevo-read user <@handle>
```

On `user_not_found` ask for a wallet address and use `--wallets` in the
trade-activity read (and `"wallets"` in the trigger) instead of principal ids.

Then get, in their own words: **how much per copy**, and **which of the three
products** they mean. Map what they said onto the knobs:

| What they said | `SIZING` | with |
| --- | --- | --- |
| "$5 a trade" | `fixed` | `SIZE_USD: 5` |
| "20% of my wallet each" | `cash_share` | `SHARE: 0.2` — read live, every event |
| "20% of whatever she buys" | `leader_share` | `SHARE: 0.2` |
| "never more than $50" | (any) | `MAX_USD: 50` |
| "only her trades over $100" | (any) | `MIN_LEADER_USD: 100` |
| "her buys AND sells" | (any) | `MIRROR_SELLS: true` |
| "her perps / longs / shorts too" | (any) | `MIRROR_PERPS: true` |
| "her stocks too" / "her AAPL trades" | (any) | `MIRROR_STOCKS: true` |
| "everything she does" | (any) | all three of the above `true` |
| "but cap it at 3x" | (any) | `PERP_MAX_LEVERAGE: 3` |
| "always at 2x" | (any) | `PERP_LEVERAGE: 2` |
| "only on Base" | (any) | `CHAIN_IDS: [8453]` |

A share of THEIR wallet and a share of the LEADER's trade are different asks and
different modes — "20% of my wallet" is `cash_share`, "20% of what she buys" is
`leader_share`. Getting that wrong spends the wrong money.

Perps and stocks are OFF unless they asked. "Copy her trades" is spot: say what
you switched on and what you did not.

## Customize

- `LEADER` (required) — principal id or wallet.
- `SIZING` (required) — `fixed` | `cash_share` | `leader_share`. No default:
  ask rather than pick one. On a perp it sizes the POSITION (notional), never
  the margin, which is also what the leader's own row reports.
- `SIZE_USD` — dollars per copy when `SIZING` is `fixed`.
- `SHARE` — the fraction (20% → `0.2`) of the owner's spendable cash
  (`cash_share`, read at trade time) or of the leader's own USD size
  (`leader_share`).
- `MAX_USD` — per-trade ceiling. Set it ONLY when they named a ceiling.
- `CHAIN_IDS` (default `[]`) — `[]` copies each trade on the chain the leader
  traded on, which is almost always right. Set it only when they named a chain;
  a chain filter they did not ask for is refused at create. **Spot only** — a
  perp has no chain and a stock's chain belongs to its cash leg, so neither is
  ever filtered by it.
- `MIRROR_SELLS` (default `false`) — "copy her buys" means buys. Spot and stock
  sells only; a perp close is not a sell and is never gated by it.
- `MIN_LEADER_USD` (default `0`) — skip leader trades below this size. Applies
  to spot, stocks and perp opens; never to a perp close.
- `MIRROR_PERPS` (default `false`) — mirror their leveraged opens, and close
  when they close.
- `MIRROR_STOCKS` (default `false`) — mirror their tokenized stock trades.
- `PERP_LEVERAGE` (default `0`) — open at this leverage instead of the
  leader's. `PERP_MAX_LEVERAGE` (default `0`) caps whichever applies.

**A rule these knobs cannot express is not a dead end.** A filter on the token,
a second leader, a ceiling on total exposure across copies, a cooldown between
them — that is code, and code runs on every leader event:

```bash
bevo-hub fork butler-copytrade
```

Your owner's own copy, yours to edit. `duty.py` already shows the shape: each
`copy_*` function opens with its skip checks and returns early, so a new one
goes beside them. `bevo.read("/token-stats", {"tokens": "<address>:<chainId>"})`
for the token's liquidity or market cap, `bevo.state` for a running total or a
cooldown that survives a restart, `bevo.holdings()` for what they already hold.
Any knob your code reads is a `params` entry you add to the fork's OWN frontmatter —
an `env` key the skill never declared is refused at create. Then file it with
`bevo-automation create --from-skill <your-fork>`. The hub never overwrites a fork, and forking changes
nothing that guards the money — the same key per leader event, the same approval
cards, the same pocket. Tell your owner what you changed and why.

## One-off procedure

1. [FIXED] Read the leader's recent activity (newest-first):

   ```bash
   bevo-read trade-activity --principal-ids <leaderPrincipalId> --limit 20
   ```

2. [ADAPT] Pick the event your owner meant — default: the newest `direction:"buy"`.
3. [FIXED] Skip an event whose `direction`, `usdValue` or traded-leg address and
   symbol are all null — take the next candidate rather than guessing a value.
4. [ADAPT] Size it exactly as they said (the table above). No size named, no trade.
5. [FIXED] Echo the product, the token, the chain, the leader's size and your
   size before filing anything.
6. [FIXED] File it — key from the event id — in the shape the product takes.
   A spot token goes by ADDRESS on the event's OWN `chainId`, a buy taking
   `--chain-out` and a sell `--chain-in`:

   ```bash
   acp trade --token-in usdc --amount-in <usd> --token-out <tokenOutAddress> --chain-out <chainId> --idempotency-key copytrade:chat:<eventId>
   ```

   A perp (`type: "HL"`) takes the leader's side and coin, sized in notional:

   ```bash
   acp trade --side <long|short> --token <tokenOutSymbol> --amount-usdc <usd> --leverage <n> --idempotency-key copytrade:chat:<eventId>
   ```

   A tokenized stock takes the ticker with no `--side`; a sell is share-denominated
   and needs the venue and count from your owner's own holding, floored. `--chain`
   on a stock is the VENUE NAME (`eth` | `sol`) off that holding's own `chain`
   field, NEVER a chain id — a numeric one reroutes the order onto a bare-symbol
   spot swap, which is a different asset:

   ```bash
   acp trade --token <TICKER> --amount-usdc <usd> --idempotency-key copytrade:chat:<eventId>
   acp trade --token <TICKER> --amount-shares <n> --chain <venue> --idempotency-key copytrade:chat:<eventId>
   ```

7. [FIXED] On `accepted` or `manual_signing_required`, stop and report; on
   anything else, see "Idempotency and retries".

## Duty procedure

1. [ADAPT] Confirm what you are about to file back to them in one line: whose
   trades, which products, how much per copy, any cap, leverage or chain.
2. [FIXED] Trigger JSON — the leader's own row in the public feed:
   `{"kind":"trade","principalIds":["<leaderPrincipalId>"]}` (or
   `{"kind":"trade","wallets":["0x…"]}`). Do not add `"direction"`: the mirror
   switches decide what is copied, and the duty needs to SEE a sell or a close
   to act on it.
3. [FIXED] `env` = the knobs above, from your owner's words (skill defaults,
   then their saved `bevo-hub set` prefs, then this ask's own values).
4. [ADAPT] `requestedDailyLimitUsdc` = their stated daily cap; `spec` and
   `yardstick` = what they asked for, in their words — name the chain in the
   `spec` when you set `CHAIN_IDS`, or the create is refused.
5. [FIXED] Create it — the shim loads this skill's `duty.py`, so never hand-write
   from scratch what this skill already does. A rule its knobs cannot express is a
   fork (see "Customize"), which files exactly the same way, under its own name:

   ```bash
   bevo-automation create --from-skill butler-copytrade '<json>'
   ```

6. [FIXED] Report as in "Say to the owner".

## Idempotency and retries

Key: `copytrade:chat:<eventId>` (one-off), `copytrade:{SERVICE_ID}:<eventId>`
(duty, see `duty.py`) — one key per leader event, never a timestamp. The service
pump can hand back rows it already delivered after a restart; the same key is
what makes that a no-op instead of a second real trade. Any error or
uncertainty: `bevo-read request <key>` first — do not re-run.

## Failure handling

| Outcome | What to do |
| --- | --- |
| `user_not_found` on the leader | Ask for a wallet address; read and trigger by `wallets` instead. |
| Leader event names no token at all | Skip it; take the next candidate. |
| create refused: `env.CHAIN_IDS` narrows to a chain the owner never named | Drop `CHAIN_IDS` (or `[]`) — or name their chain in the `spec`. |
| create refused: share-of-wallet ask whose `SIZING` is not `cash_share` | They said a share of THEIR money: `SIZING=cash_share`, `SHARE=<fraction>`. |
| create refused: `env.SIZING: required` | You never asked how much. Ask that one question, then create. |
| Leader traded a bare ticker that is not a stock you can trade | Skipped, never guessed onto the spot rail — a bare-symbol swap resolves to whatever token wears that ticker. A region that bars stocks looks the same here. |
| A perp or stock leg refused for a permission gate | That product is not available to this owner; offer the spot mode and leave the switch off. |
| Copy size under a venue minimum ($2 spot, $15 perp, $15 stock buy) | Not filed. Say the minimum rather than rounding their size up to it. |
| Closing a perp the owner never opened | A no-op, not an error — the duty skips it and moves on. |
| `accepted` | Done — report product, token and size. |
| `manual_signing_required` | The card is already in Approvals; say so once, do not poll. |

## Limits

One trade per leader event, with no averaging or laddering. A perp CLOSE is
mirrored whenever the leader's position comes off — by their own hand, a stop,
or a liquidation — and is never gated by a size, chain or minimum knob; but the
copy closes ALL of that coin's position, not the leader's fraction of theirs.
Leverage is the leader's unless the owner named a figure: their 40x is 40x of
their conviction on their balance, not the owner's — and a position the
exchange reported rather than the leader placing it through us carries no
leverage at all, so a copy of one opens at 1x. The notional is still theirs;
the margin is not. Perp margin is never moved
between the spot and perp accounts — a perp copy with no perp cash simply
refuses. Yanking this skill does not stop a duty already created from it (the
duty keeps its own copy of `duty.py`).

## Say to the owner

One-off: "Copied `<@leader>`'s `<buy|long|stock buy>` — `<amount>` USD into
`<token>`<` on chain <chainId>` for a spot leg, `at <n>x` for a perp>." Duty:
"Created, pending — arm it in Approvals; the card proposes your daily cap as its
pocket; nothing runs until then." Say back, in their own words, the sizing ("20%
of your cash, read fresh each time") and exactly which products are mirrored and
which are not — never as a dollar figure you worked out yourself.
