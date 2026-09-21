Copies another trader's moves onto the owner's own size.

The leader is named by the duty's `trade` trigger, not by a setting. Each trade they
make is gated, sized against the owner's own wallet, and placed as its own keyed leg.

| The leader did | This duty does | Unless |
| --- | --- | --- |
| bought a token | buys the same token, on the leader's chain | `CHAIN_IDS` excludes it, or the trade is under `MIN_LEADER_USD` |
| sold a token | sells what the owner holds of it, capped at their balance | `MIRROR_SELLS` is off (it is off by default) |
| opened a perp | opens the same side, at `PERP_LEVERAGE` or the leader's own | `MIRROR_PERPS` is off |
| closed a perp, or was liquidated | closes the owner's position, in USD notional | `MIRROR_PERPS` is off |
| bought or sold a tokenized stock | does the same, share-denominated | `MIRROR_STOCKS` is off |

## What it will not do

- **Mirror sells by default.** `MIRROR_SELLS` is `false`, so out of the box this
  follows the leader in and never follows them out. An owner who says "copy them"
  usually means both; say which they are getting.
- **Verify the token.** It copies what the leader actually traded — by address, or by
  symbol when that is what they typed — and never re-resolves through a catalogue. A
  leader buying something worthless is copied faithfully. The pocket is the limit.
- **Size from the leader's wallet.** `cash_share` is a share of the *owner's* cash;
  `leader_share` is a share of what the leader put in. `fixed` ignores both.
- **Exceed `MAX_USD` per trade**, when one is set — except on a perp close, which is
  deliberately unclamped: a close trimmed to a ceiling leaves a residual position,
  which is worse than not closing.
- **Exceed its daily caps.** Each leg is logged with its dollar value *before* it is
  sent, and `MAX_PER_DAY` / `MAX_USD_PER_DAY` count today's (UTC) lines, so a restart
  resets nothing. Only opening legs count, trimmed to the dollars left; exits are
  never capped. If the log no longer reaches back to midnight, buys are skipped.
- **Copy the owner's own trades.** Their own activity never arms a leg.
- **Spend before the owner funds it.** It spends through the pocket, which starts
  empty; until it is funded every leg becomes an approval card.

## Settings

| Name | Unit | Default | Means |
| --- | --- | --- | --- |
| `SIZING` | `fixed` \| `cash_share` \| `leader_share` | required | how much per copy — a flat figure, a share of the owner's cash, or a share of what the leader traded |
| `SIZE_USD` | US dollars per copy | — | used when `SIZING` is `fixed`; minimum 2 |
| `SHARE` | fraction 0–1 | — | used by `cash_share` / `leader_share`. `0.2` is 20% |
| `MAX_USD` | US dollars | unset | per-trade ceiling. Set only when the owner named one |
| `MAX_PER_DAY` | opening trades a day | `20` | buys and perp opens (UTC day) |
| `MAX_USD_PER_DAY` | US dollars a day | unset | into opening trades, a perp at its margin. Set only when named |
| `CHAIN_IDS` | chain ids | `[]` | empty means the leader's own chain. Set only when they named chains |
| `MIN_LEADER_USD` | US dollars | `0` | ignore the leader's trades smaller than this |
| `MIRROR_SELLS` | on/off | **off** | also follow spot sells |
| `MIRROR_PERPS` | on/off | **on** | also follow perp opens and closes |
| `MIRROR_STOCKS` | on/off | **on** | also follow tokenized-stock buys and sells |
| `PERP_LEVERAGE` | multiple | `0` | leverage for a copied open; `0` uses the leader's own |
| `PERP_MAX_LEVERAGE` | multiple | `0` | cap on a copied open's leverage; `0` is no cap (the venue's own still applies) |

`SIZING` is the only required one, and it decides which of `SIZE_USD` / `SHARE` is
read — a bare number from the owner is dollars or a percentage depending on it.

## Trigger

One `trade`, naming the leader by wallet or principal id:

```json
{ "kind": "trade", "wallets": ["0x…"] }
```

Do not add `direction`: the switches above decide which moves are copied, and a
trigger filter would also hide the rest from the duty's log.
