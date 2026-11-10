# Changelog

Versioning follows [SemVer](https://semver.org). The version lives in `__version__` in `autopilot.py`.

## Unreleased
- `defaults.max_model` / `max_effort` (default `claude-opus-5-5` / `xhigh`): hard cap on every session.
- Task commits now include the plan id: `autopilot(<plan-id> <task-id>): <title>` (was `autopilot(<task-id>): …`).

## 0.1.0
- Initial versioned release: herdr integration, live reporting, elapsed-time tracking, fix-up rounds.
