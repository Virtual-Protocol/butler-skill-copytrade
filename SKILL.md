---
name: butler-copytrade
description: Copy, mirror or follow another member's trades — once, or as a standing duty sized from your owner's own words. One trade per leader event, never twice.
version: 2.0.0
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"butler":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","copy trading","copy buys","mirror wallet","mirror trades","follow trader","follow wallet"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who to copy"},{"name":"SIZING","type":"enum","values":["fixed","cash_share","leader_share"],"required":true,"ask":"how much per copy — a fixed amount, a share of your cash, or a share of what they trade?"},{"name":"SIZE_USD","type":"usd","min":2,"max":10000,"help":"SIZING=fixed: the dollars per copy"},{"name":"SHARE","type":"number","min":0,"max":1,"help":"SIZING=cash_share|leader_share: the fraction, 20% = 0.2"},{"name":"MAX_USD","type":"usd","min":2,"max":100000,"help":"per-trade ceiling — only when the owner named one"},{"name":"CHAIN_IDS","type":"chainIds","default":[],"help":"[] = whatever chain the leader traded on; set ONLY when the owner named a chain"},{"name":"MIRROR_SELLS","type":"bool","default":false,"help":"copy their sells too — only when the owner asked for sells"},{"name":"MIN_LEADER_USD","type":"usd","default":0,"min":0,"max":100000,"help":"ignore leader trades smaller than this"}],"dutyTemplate":"duty.py"}}
---

## When to use

Your owner asks to copy, mirror or follow another member's or wallet's trading —
once ("copy their last buy") or standing ("copy every buy they make").

**The knobs below ARE your owner's words.** This skill has no money defaults: no
size, no chain, no sells unless they said so. If they did not name a size, ask
that one question before you create anything — a default here is you deciding
how much of their money to spend.

## Before you start

Resolve the leader:

```bash
bevo-read user <@handle>
```

On `user_not_found` ask for a wallet address and use `--wallets` in the
trade-activity read (and `"wallets"` in the trigger) instead of principal ids.

Then get, in their own words: **how much per copy**, and whether they mean buys
only or sells too. Map what they said onto the knobs:

| What they said | `SIZING` | with |
| --- | --- | --- |
| "$5 a trade" | `fixed` | `SIZE_USD: 5` |
| "20% of my wallet each" | `cash_share` | `SHARE: 0.2` — read live, every event |
| "20% of whatever she buys" | `leader_share` | `SHARE: 0.2` |
| "never more than $50" | (any) | `MAX_USD: 50` |
| "only her trades over $100" | (any) | `MIN_LEADER_USD: 100` |
| "her buys AND sells" / "everything" | (any) | `MIRROR_SELLS: true` |
| "only on Base" | (any) | `CHAIN_IDS: [8453]` |

A share of THEIR wallet and a share of the LEADER's trade are different asks and
different modes — "20% of my wallet" is `cash_share`, "20% of what she buys" is
`leader_share`. Getting that wrong spends the wrong money.

## Customize

- `LEADER` (required, asked as "who should I copy?") — principal id or wallet.
- `SIZING` (required) — `fixed` | `cash_share` | `leader_share`. No default:
  ask rather than pick one.
- `SIZE_USD` — dollars per copy when `SIZING` is `fixed`.
- `SHARE` — the fraction (20% → `0.2`) when `SIZING` is `cash_share` (of the
  owner's spendable cash, read at trade time) or `leader_share` (of the
  leader's own USD size on that trade).
- `MAX_USD` — per-trade ceiling. Set it ONLY when they named a ceiling.
- `CHAIN_IDS` (default `[]`) — `[]` copies each trade on the chain the leader
  traded on, which is almost always right. Set it only when they named a chain;
  a chain filter they did not ask for is refused at create, and it is what
  silently drops a leader's tokenized-stock buys.
- `MIRROR_SELLS` (default `false`) — "copy her buys" means buys.
- `MIN_LEADER_USD` (default `0`) — skip leader trades below this size.

## One-off procedure

1. [FIXED] Read the leader's recent activity (newest-first):

   ```bash
   bevo-read trade-activity --principal-ids <leaderPrincipalId> --limit 20
   ```

2. [ADAPT] Pick the event your owner meant — default: the newest `direction:"buy"`.
3. [FIXED] Skip an event whose `direction`, `chainId`, `usdValue` or traded-leg
   address is null — take the next candidate rather than guessing a value.
4. [ADAPT] Size it exactly as they said (the table above). No size named, no trade.
5. [FIXED] Echo the token address, the chain, the leader's size and your size
   before filing anything.
6. [FIXED] File it — the token by ADDRESS on the event's OWN `chainId`, key from
   the event id. A buy takes `--chain-out`, a sell `--chain-in`:

   ```bash
   acp trade --token-in usdc --amount-in <usd> --token-out <tokenOutAddress> --chain-out <chainId> --idempotency-key copytrade:chat:<eventId>
   ```

7. [FIXED] On `accepted` or `manual_signing_required`, stop and report; on
   anything else, see "Idempotency and retries".

## Duty procedure

1. [ADAPT] Confirm what you are about to file back to them in one line: whose
   trades, how much per copy, buys or buys-and-sells, any cap or chain.
2. [FIXED] Trigger JSON — the leader's own row in the public feed:
   `{"kind":"trade","principalIds":["<leaderPrincipalId>"]}` (or
   `{"kind":"trade","wallets":["0x…"]}`). Do not add `"direction"`: `MIRROR_SELLS`
   decides that, and the duty needs to SEE a sell to ignore it.
3. [FIXED] `env` = the knobs above, from your owner's words (skill defaults,
   then their saved `bevo-hub set` prefs, then this ask's own values).
4. [ADAPT] `requestedDailyLimitUsdc` = their stated daily cap; `spec` and
   `yardstick` = what they asked for, in their words — name the chain in the
   `spec` when you set `CHAIN_IDS`, or the create is refused.
5. [FIXED] Create it — never hand-write the duty's code, the shim loads this
   skill's `duty.py`:

   ```bash
   bevo-automation create --from-skill butler-copytrade@2.0.0 '<json>'
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
| Leader event has a null direction, chain or traded-leg address | Skip it; take the next candidate. |
| create refused: `env.CHAIN_IDS` narrows to a chain the owner never named | Drop `CHAIN_IDS` (or `[]`) — or name their chain in the `spec`. |
| create refused: share-of-wallet ask whose `SIZING` is not `cash_share` | They said a share of THEIR money: `SIZING=cash_share`, `SHARE=<fraction>`. |
| create refused: `env.SIZING: required` | You never asked how much. Ask that one question, then create. |
| `accepted` | Done — report token, chain and size. |
| `manual_signing_required` | The card is already in Approvals; say so once, do not poll. |

## Limits

Spot only: a leader's perp is never mirrored, and a duty copy is one trade per
leader event with no averaging or laddering. `cash_share` sizes off spendable
cash, so a wallet with none skips the event rather than part-filling it.
Yanking this skill does not stop a duty already created from it (the duty keeps
its own copy of `duty.py`).

## Say to the owner

One-off: "Copied `<@leader>`'s buy — `<amount>` USD into `<token>` on chain
`<chainId>`." Say plainly whether sells are mirrored. Duty: "Created, pending —
arm it in Approvals; the card proposes your daily cap as its pocket; nothing
runs until then." Say the sizing back in their own words ("20% of your cash,
read fresh each time"), never as a dollar figure you worked out yourself.
