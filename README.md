# butler-copytrade

One Butler skill — copy another member's trades, once or as a standing duty. It is published
through the [Butler Skill Hub](https://github.com/Virtual-Protocol/butler-skills), which pins
it at a resolved commit; Butler containers clone that commit.

- `SKILL.md` — the playbook (frontmatter + fixed sections; see the hub's
  [SKILL_STANDARD.md](https://github.com/Virtual-Protocol/butler-skills/blob/main/SKILL_STANDARD.md))
- `duty.py` — the code stage a `bevo-automation create --from-skill` duty runs
- `CHANGELOG.md` — one line per version; every change bumps `version` in SKILL.md and is tagged `vX.Y.Z`

## Validate before tagging

The hub publishes its validator and replay harness as standalone files:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

`replay.py` pulls `stub_bevo.py` and any fixture it needs from the same site. Keep the
downloaded files out of the commit.

In CI both are one step:

```yaml
- uses: Virtual-Protocol/butler-skills/.github/actions/validate@main
```
