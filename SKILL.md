---
name: butler-copytrade
description: Copy, mirror or follow another member's trades — spot, tokenized stocks and perps, once or as a standing duty sized from your owner's own words.
version: 4.0.1
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"butler":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","copy trading","copy buys","mirror wallet","mirror trades","follow trader","follow wallet","copy perps","mirror perps","copy stocks"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","GET /butler-read/user-assets","GET /butler-read/token-search","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who should I copy?"},{"name":"SIZING","type":"enum","values":["fixed","cash_share","leader_share"],"required":true,"ask":"how much per copy — a fixed dollar figure, a share of your own cash, or a share of what they trade?"},{"name":"SIZE_USD","type":"usd","min":2,"max":10000,"help":"dollars per copy (SIZING=fixed)"},{"name":"SHARE","type":"number","min":0,"max":1,"help":"fraction for cash/leader share sizing (0.2 = 20%)"},{"name":"MAX_USD","type":"usd","min":2,"max":100000,"help":"per-trade ceiling"},{"name":"CHAIN_IDS","type":"chainIds","default":[],"help":"empty = the leader's chain; set only when specified. Spot only"},{"name":"MIRROR_SELLS","type":"bool","default":false,"help":"copy spot and stock sells too"},{"name":"MIN_LEADER_USD","type":"usd","default":0,"min":0,"max":100000,"help":"ignore trades smaller than this; never applied to a perp close"},{"name":"MIRROR_PERPS","type":"bool","default":false,"help":"copy their leveraged positions too"},{"name":"MIRROR_STOCKS","type":"bool","default":false,"help":"copy their tokenized stock trades too"},{"name":"PERP_LEVERAGE","type":"number","default":0,"min":0,"max":50,"help":"fixed leverage to open at; 0 = take the leader's own"},{"name":"PERP_MAX_LEVERAGE","type":"number","default":0,"min":0,"max":50,"help":"leverage ceiling; 0 = no ceiling"}],"dutyTemplate":"duty.py"}}
---

## When to use

Your owner asks to copy, mirror or follow another member's or wallet's trading —
once ("copy their last buy") or standing ("copy every buy they make"). Spot
tokens, tokenized stocks and leveraged perps, each behind its own switch.

No money defaults: no size, no chain, no sells, no perps, no stocks unless they
said so.

## Before you start

Resolve the leader:

```bash
bevo-read user <@handle>
```

On `user_not_found` ask for a wallet address and use `--wallets` in the
trade-activity read (and `"wallets"` in the trigger) instead of principal ids.

Then get, in their own words, **how much per copy** and **which of the three
products** they mean. Sizing is three different asks:

| What they said | `SIZING` | with |
| --- | --- | --- |
| "$5 a trade" | `fixed` | `SIZE_USD: 5` |
| "20% of my wallet each" | `cash_share` | `SHARE: 0.2` — read live, every event |
| "20% of whatever she buys" | `leader_share` | `SHARE: 0.2` |

Everything else they said is one knob below; "everything she does" = all three
`MIRROR_*` switches `true`. Perps and stocks are OFF unless they asked; "copy
her trades" is spot. Say what you switched on and what you did not.

## Customize

- `LEADER` (required) — principal id or wallet.
- `SIZING` (required) — see the table above. Ask, never pick one. On a perp it
  sizes the POSITION (notional), never the margin.
- `SIZE_USD` — dollars per copy when `SIZING` is `fixed`.
- `SHARE` — the fraction (20% → `0.2`) for `cash_share` (of the owner's
  spendable cash, read at trade time) or `leader_share` (of the leader's size).
- `MAX_USD` — per-trade ceiling. Set it ONLY when they named a ceiling.
- `CHAIN_IDS` (default `[]`) — `[]` copies each trade on the chain the leader
  traded on. Set it (Base → `[8453]`) only when they named a chain; an
  unasked-for chain filter is refused at create. **Spot only** — perps and stocks are never filtered by it.
- `MIRROR_SELLS` (default `false`) — spot and stock sells only; a perp close is
  not a sell and is never gated by it.
- `MIN_LEADER_USD` (default `0`) — skip leader trades below this size: spot,
  stocks and perp opens, never a perp close.
- `MIRROR_PERPS` (default `false`) — mirror their leveraged opens, and close
  when they close.
- `MIRROR_STOCKS` (default `false`) — mirror their tokenized stock trades.
- `PERP_LEVERAGE` (default `0`) — open at this leverage instead of the leader's.
  `PERP_MAX_LEVERAGE` (default `0`) caps whichever applies.

A rule these knobs cannot express — a token filter, a second leader, a ceiling
on total exposure, a cooldown — is code:

```bash
bevo-hub fork butler-copytrade
```

In the fork's `duty.py` each `copy_*` function opens with its skip checks and
returns early; a new one goes beside them. `bevo.read("/token-stats",
{"tokens": "<address>:<chainId>"})` for liquidity or market cap, `bevo.state`
for a total or cooldown that survives a restart, `bevo.holdings()` for what they
hold. Any knob your code reads is a `params` entry in the fork's OWN frontmatter
— an undeclared `env` key is refused at create. File it with
`bevo-automation create --from-skill <your-fork>`; say what you changed.

## One-off procedure

1. [FIXED] Read the leader's recent activity (newest-first):

   ```bash
   bevo-read trade-activity --principal-ids <leaderPrincipalId> --limit 20
   ```

2. [ADAPT] Pick the event your owner meant — default: the newest `direction:"buy"`.
3. [FIXED] Skip an event whose `direction`, `usdValue` or traded-leg address and
   symbol are all null — take the next candidate, never guess a value.
4. [ADAPT] Size it exactly as they said. No size named, no trade.
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
   field, NEVER a chain id:

   ```bash
   acp trade --token <TICKER> --amount-usdc <usd> --idempotency-key copytrade:chat:<eventId>
   acp trade --token <TICKER> --amount-shares <n> --chain <venue> --idempotency-key copytrade:chat:<eventId>
   ```

7. [FIXED] On `accepted` or `manual_signing_required`, stop and report; on
   anything else, see "Idempotency and retries".

## Duty procedure

1. [ADAPT] Confirm back in one line: whose trades, which products, how much per
   copy, any cap, leverage or chain.
2. [FIXED] Trigger JSON — the leader's own row in the public feed:
   `{"kind":"trade","principalIds":["<leaderPrincipalId>"]}` (or
   `{"kind":"trade","wallets":["0x…"]}`). Do not add `"direction"` — the duty
   must SEE a sell or a close to mirror it, and the switches decide the rest.
3. [FIXED] `env` = the knobs above, from your owner's words (skill defaults,
   then their saved `bevo-hub set` prefs, then this ask's own values).
4. [ADAPT] `requestedDailyLimitUsdc` = their stated daily cap; `spec` and
   `yardstick` = what they asked for, in their words — name the chain in the
   `spec` when you set `CHAIN_IDS`, or the create is refused.
5. [FIXED] Create it — the shim loads this skill's `duty.py`; never hand-write
   what this skill already does. A fork (see "Customize") files the same way,
   under its own name:

   ```bash
   bevo-automation create --from-skill butler-copytrade '<json>'
   ```

6. [FIXED] Report as in "Say to the owner".

## Idempotency and retries

Key: `copytrade:chat:<eventId>` (one-off), `copytrade:{SERVICE_ID}:<eventId>`
(duty, see `duty.py`) — one key per leader event, never a timestamp; the pump
re-delivers rows after a restart and the key makes that a no-op. Any error or
uncertainty: `bevo-read request <key>` first — do not re-run.

## Failure handling

| Outcome | What to do |
| --- | --- |
| `user_not_found` on the leader | Ask for a wallet address; read and trigger by `wallets` instead. |
| Leader event names no token at all | Skip it; take the next candidate. |
| create refused: `env.CHAIN_IDS` narrows to a chain the owner never named | Drop `CHAIN_IDS` (or `[]`) — or name their chain in the `spec`. |
| create refused: share-of-wallet ask whose `SIZING` is not `cash_share` | `SIZING=cash_share`, `SHARE=<fraction>`. |
| create refused: `env.SIZING: required` | Ask how much per copy, then create. |
| Leader traded a bare ticker that is not a stock you can trade | Skipped, never guessed onto the spot rail; a region that bars stocks looks the same. |
| A perp or stock leg refused for a permission gate | Not available to this owner; offer the spot mode and leave the switch off. |
| Copy size under a venue minimum ($2 spot, $15 perp, $15 stock buy) | Not filed. Say the minimum; never round their size up to it. |
| Closing a perp the owner never opened | A no-op, not an error — skip it and move on. |
| `accepted` | Done — report product, token and size. |
| `manual_signing_required` | The card is already in Approvals; say so once, do not poll. |

## Limits

One trade per leader event, no averaging or laddering. A perp CLOSE is mirrored
however the leader's position came off (their hand, a stop, a liquidation) and
is never gated by a size, chain or minimum knob; but the copy closes ALL of that
coin's position, not the leader's fraction. Leverage is the leader's unless the
owner named a figure, and a position the exchange reported rather than the
leader placing it through us carries none, so a copy of one opens at 1x. Perp
margin is never moved between the spot and perp accounts — a perp copy with no
perp cash refuses. Yanking this skill does not stop a duty already created from
it (it keeps its own copy of `duty.py`).

## Say to the owner

One-off: "Copied `<@leader>`'s `<buy|long|stock buy>` — `<amount>` USD into
`<token>`<` on chain <chainId>` for a spot leg, `at <n>x` for a perp>." Duty:
"Created, pending — arm it in Approvals; the card proposes your daily cap as its
pocket; nothing runs until then." Say back their own sizing words, and which
products are mirrored and which are not — never a dollar figure you worked out
yourself.
