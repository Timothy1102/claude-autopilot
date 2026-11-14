# Changelog

Versioning follows [SemVer](https://semver.org). The version lives in `__version__` in `autopilot.py`.

## Unreleased
- `--claude-bin` / `--claude` (or `$AUTOPILOT_CLAUDE`) now accepts shell aliases like `claude-personal` and `VAR=val claude` commands, so runs (including `autopilot herdr`) can use a different Claude account. Default stays `claude`.
- `defaults.max_model` / `max_effort` (default `claude-opus-5-5` / `xhigh`): hard cap on every session.
- Task commits now include the plan id: `<plan-id>(<task-id>): <title>` (was `autopilot(<task-id>): …`).

## 0.1.0
- Initial versioned release: herdr integration, live reporting, elapsed-time tracking, fix-up rounds.
