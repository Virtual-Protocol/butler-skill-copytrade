# Changelog

## 1.1.0

- The frontmatter namespace key is `metadata.butler`, not `metadata.bevo`. No field inside
  the block changed — only the key it hangs from. The published `index.json` is unaffected,
  because the registry flattens the block into top-level fields before publishing it.
- Requires a container image that reads `metadata.butler`; an older image reads the block as
  empty and loses `dutyTemplate`, `params` and `modes`.

## 1.0.1

- Renamed from `bevo-copytrade`; docs (validate with the hub's published standalone tools,
  no registry checkout) and a trimmed playbook — the skill is the delta over AGENTS.md.

## 1.0.0

- Initial release: one-off and duty modes, buy-only copy trading with per-event
  idempotency keys and a bounded local seen-set.
