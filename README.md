# butler-copytrade

Copy another member's buys once or as a standing duty, one trade per leader event, never twice.

This repository is one Butler skill. It is published through the
[Butler Skill Hub](https://github.com/Virtual-Protocol/butler-skills), which pins
it as a git submodule at a tagged commit; Butler containers clone that commit.

- `SKILL.md` — the playbook (frontmatter + fixed sections; see the hub's
  [SKILL_STANDARD.md](https://github.com/Virtual-Protocol/butler-skills/blob/main/SKILL_STANDARD.md))
- `duty.py` — the code stage a `bevo-automation create --from-skill` duty runs
- `CHANGELOG.md` — one line per version; every change bumps `version` in SKILL.md and is tagged `vX.Y.Z`

## Validate before tagging

No Butler account, container or registry checkout needed — the hub publishes its validator
and replay harness as standalone files:

```bash
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/validate.py
curl -sSLO https://virtual-protocol.github.io/butler-skills/tools/replay.py
python3 validate.py --standalone .
python3 replay.py --standalone . --fixture trade-activity-page
```

`replay.py` downloads `stub_bevo.py` and any fixture it needs from the same site when they
are not already next to it. Keep the downloaded files out of the commit.

In CI the same two checks are a single step:

```yaml
- uses: Virtual-Protocol/butler-skills/.github/actions/validate@main
```
