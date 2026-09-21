# butler-skill-copytrade — copy another trader's moves, on your own size

**This duty spends your money.** Every leg it places goes through the pocket
you arm it with, and a leg over that pocket's limit becomes an approval card
rather than a trade.

## What it does

One trigger, `trade`: name the leaders by principal id or by wallet.

```json
{ "kind": "trade", "wallets": ["0x…"] }
```

Do not add `direction` — the switches below decide which of their moves are
copied, and a trigger filter would hide the rest from the duty's log as well.

Each trade the leaders make is gated, sized and placed:

| The leader did | This duty does | Unless |
| --- | --- | --- |
| bought a token | buys the same token, on the leader's chain | `CHAIN_IDS` excludes it, or the trade is under `MIN_LEADER_USD` |
| sold a token | sells what you hold of it, capped at your balance | `MIRROR_SELLS` is off |
| opened a perp | opens the same side, at `PERP_LEVERAGE` or the leader's own | `MIRROR_PERPS` is off |
| closed a perp (or was liquidated) | closes your position, unsized | `MIRROR_PERPS` is off |
| bought or sold a tokenized stock | does the same, share-denominated | `MIRROR_STOCKS` is off |

It copies what the leader actually traded — by address, or by symbol when
that is what they typed — and never re-resolves through a token catalogue
first. Each copied leg is keyed per source event, so a re-delivered event
replays instead of filing twice. A copied perp close is sized in USD notional
(`--amount-usdc … --reduce-only`), because the rail refuses a `--size` close.
The duty spends through the owner's pocket — it starts empty and only the
owner can fund it, so an unfunded duty asks before it trades. Three switches
default in ways worth stating explicitly: `MIRROR_SELLS` defaults **false**,
`MIRROR_PERPS` and `MIRROR_STOCKS` default **true**.

## Settings

| Setting | Type | Default | Meaning |
| --- | --- | --- | --- |
| `SIZING` | enum: `fixed` \| `cash_share` \| `leader_share` | — (required) | how much per copy — a fixed dollar figure, a share of your own cash, or a share of what they trade? |
| `SIZE_USD` | number | — | dollars per copy (SIZING=fixed) |
| `SHARE` | number | — | fraction for cash/leader share sizing (0.2 = 20%) |
| `MAX_USD` | number | — | per-trade ceiling; set only when the owner named one |
| `CHAIN_IDS` | array of integers | `[]` | empty = the leader's chain; set only when specified |
| `MIRROR_SELLS` | boolean | `false` | copy spot sells too |
| `MIN_LEADER_USD` | number | `0` | ignore leader trades smaller than this |
| `MIRROR_PERPS` | boolean | `true` | copy the leader's perp opens and closes, at your own leverage |
| `MIRROR_STOCKS` | boolean | `true` | copy the leader's tokenized-stock buys and sells |
| `PERP_LEVERAGE` | number | `0` | leverage for a copied perp open; 0 = use the leader's own |
| `PERP_MAX_LEVERAGE` | number | `0` | cap on the leverage of a copied open; 0 = no cap (the venue's 50x still applies) |

`LEADER` is not a param — the leader is named by the duty's `trade` trigger
(`wallets` / `principalIds`), not by settings.

## How it is used

An owner asks their butler to copy a trader, and the butler files
`duty_create {recipe: "copytrade@5", params: {...}}` with a `trade` trigger
naming the leader.

## What it will not do

- Size off a wallet read that failed. `cash_share` skips the trade instead.
- Open a perp whose side the feed did not name.
- File the same leader trade twice. Each leg's key is derived from the
  leader's own event, so a redelivery after a restart is recognised as a
  replay.
