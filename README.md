# bevo-copytrade

Copy another member's buys once or as a standing duty, one trade per leader event, never twice.

This repository is one Butler skill. It is published through the
[Butler Skill Hub](https://github.com/Virtual-Protocol/butler-skills), which pins
it as a git submodule at a tagged commit; Butler containers clone that commit.

- `SKILL.md` — the playbook (frontmatter + fixed sections; see the hub's
  [SKILL_STANDARD.md](https://github.com/Virtual-Protocol/butler-skills/blob/main/SKILL_STANDARD.md))
- `duty.py` — the code stage a `bevo-automation create --from-skill` duty runs
- `CHANGELOG.md` — one line per version; every change bumps `version` in SKILL.md and is tagged `vX.Y.Z`

Validate before tagging (no Bevo account or container needed):

```bash
git clone --depth 1 https://github.com/Virtual-Protocol/butler-skills /tmp/butler-skills
python3 /tmp/butler-skills/scripts/validate.py --standalone .
python3 /tmp/butler-skills/tests/replay.py --standalone . --fixture trade-activity-page
```
