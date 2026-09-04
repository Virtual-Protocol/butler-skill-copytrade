---
name: butler-copytrade
description: Copy another member's buys once or as a standing duty, one trade per leader event, never twice. Use for "copy/mirror/follow <@handle or wallet>".
version: 1.0.1
metadata: {"openclaw":{"emoji":"🪞","requires":{"bins":["acp","bevo-read","bevo-automation"]}},"bevo":{"tier":"on-demand","modes":["one-off","duty"],"moneyMoving":true,"keywords":["copy trade","mirror wallet","follow trader"],"requires":{"routes":["GET /butler-read/user","GET /butler-read/trade-activity","POST /butler-exec/trade","POST /butler-exec/services"],"features":["tradeIdempotency","execRequestStatus"],"gates":["canSwap"],"bins":["acp","bevo-read","bevo-automation"]},"params":[{"name":"LEADER","type":"principalId|wallet","required":true,"ask":"who to copy"},{"name":"COPY_USDC_PER_TRADE","type":"usd","default":25,"min":2,"max":10000},{"name":"COPY_MAX_USDC","type":"usd","default":50,"min":2,"max":10000},{"name":"COPY_RATIO","type":"number","default":0,"min":0,"max":1,"help":"share of the leader's USD size; 0 = fixed size"},{"name":"CHAIN_IDS","type":"chainIds","default":[8453]}],"dutyTemplate":"duty.py"}}
---

## When to use

The owner asks to copy, mirror or follow another member's or wallet's buys — once ("copy
their last buy") or standing ("copy every buy they make"). Spot buys only; never a
substitute for a plain trade the owner sizes themselves.

## Before you start

Resolve the leader:

```bash
bevo-read user <@handle>
```

On `user_not_found` ask the owner for a wallet address and use `wallets:[...]` in the
trade-activity read instead. Get the sizing rule in the owner's own words (fixed USD per
copy or a ratio of the leader's size, plus a daily cap).

## Customize

- `LEADER` (required, asked as "who should I copy?") — the principalId or wallet to mirror.
- `COPY_USDC_PER_TRADE` (default $25) — fixed USD size per copy when `COPY_RATIO` is 0.
- `COPY_MAX_USDC` (default $50) — hard per-trade ceiling regardless of ratio.
- `COPY_RATIO` (default 0) — when > 0, size = leader's USD size × ratio, clamped to
  `COPY_MAX_USDC`.
- `CHAIN_IDS` (default `[8453]`) — only leader buys on one of these chains are copied.

## One-off procedure

1. [FIXED] Read the leader's recent activity (newest-first):

   ```bash
   bevo-read trade-activity --principal-ids <leaderPrincipalId> --limit 20
   ```

2. [ADAPT] Pick the event the owner meant — default: the newest `direction:"buy"`.
3. [FIXED] Skip it if `direction`, `chainId`, `tokenOutAddress` or `usdValue` is null, or if
   `chainId` (cast to int) is not in `CHAIN_IDS` — take the next candidate.
4. [ADAPT] Size from `COPY_USDC_PER_TRADE` / `COPY_RATIO`, clamped to `COPY_MAX_USDC`.
5. [FIXED] Echo token address, chain, the leader's size and your size to the owner before
   filing anything.
6. [FIXED] File it — token by address, a buy so `--chain-out`, key from the event id:

   ```bash
   acp trade --token-in usdc --amount-in <usd> --token-out <tokenOutAddress> --chain-out <chainId> --idempotency-key copytrade:chat:<eventId>
   ```

7. [FIXED] On `accepted` or `manual_signing_required`, stop and report; on anything else,
   see "Idempotency and retries".

## Duty procedure

1. [ADAPT] Confirm the trigger is what the owner meant: every buy by `LEADER`, on the
   allowed chains, sized by the params above.
2. [FIXED] Trigger JSON: `{"kind":"trade","principalIds":["<leaderPrincipalId>"],"direction":"buy"}`.
3. [FIXED] `env` = the params above (skill defaults, then the owner's saved `bevo-hub set`
   prefs, then this ask's own values).
4. [ADAPT] `requestedDailyLimitUsdc` = the owner's stated daily cap; `yardstick` = "Every buy
   by LEADER is mirrored once, within a minute, for $COPY_USDC_PER_TRADE, never twice."
5. [FIXED] Create it — never hand-write the duty's code, the shim loads this skill's `duty.py`:

   ```bash
   bevo-automation create --from-skill butler-copytrade@1.0.1 '<json>'
   ```

6. [FIXED] Report as in "Say to the owner".

## Idempotency and retries

Key: `copytrade:chat:<eventId>` (one-off), `copytrade:{SERVICE_ID}:<eventId>` (duty, see
`duty.py`) — one key per leader event, never a timestamp. Any error or uncertainty:
`bevo-read request <key>` first — do not re-run.

## Failure handling

| Outcome | What to do |
| --- | --- |
| `user_not_found` on the leader | Ask for a wallet address; read by `wallets` instead. |
| Leader event has a null field or an off-list chain | Skip it; take the next candidate. |
| `accepted` | Done — report token, chain and size. |
| `manual_signing_required` | Notify the owner once; do not poll in a loop. |

## Limits

Buys only in v1 — sells and perps are never mirrored. Yanking this skill does not stop a
duty already created from it (the duty keeps its own copy of `duty.py`).

## Say to the owner

One-off: "Copied LEADER's buy of `<amount>` USD into `<token>` on chain `<chainId>`. Sells
are not mirrored in this version." Duty: "Created, pending — arm it in Approvals; the card
proposes your daily cap as its pocket; nothing runs until then."
