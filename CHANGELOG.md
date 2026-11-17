# Changelog

Versioning follows [SemVer](https://semver.org). The version lives in `__version__` in `autopilot.py`.

## Unreleased
- `defaults.fixups` now defaults to 2 (was 1): a second fix-up round in the same session before reset and escalation.
- Cleaner logs: the live view shows the model's reasoning/commentary (💭/💬, wrapped to the terminal width) instead of raw tool calls (`defaults.stream_tools` brings them back); verification and review steps log when they start and finish (🔍 / 🧐); finished tasks print `✅ T01: DONE` and failed ones `❌`.
- Faster verification: the commands of one verification run in parallel (`verify_parallel`) and cancel each other on the first failure (`verify_fail_fast`); `{changed_files}` limits a command to the files a task changed; `task.skip_default_verify` skips `defaults.verify` for a task; a command that already passed on a content-identical tree is not run again (`verify_cache`).
- Tiered verification: `defaults.full_verify` (heavy checks) runs on tasks with `full_verify: true`, every `full_verify_every` tasks, and in a new final gate (`final_verify`, default `verify` + `full_verify`) that runs once all tasks are done. The result is shown in `REPORT.md`, the summary and the herdr state.
- The worker runs only the fast tier itself; the full tier is left to the orchestrator, so it is no longer executed twice per task.
- `validate` checks the new fields and warns when `{changed_files}` is used without any full-suite command.
- `--claude-bin` / `--claude` (or `$AUTOPILOT_CLAUDE`) now accepts shell aliases like `claude-personal` and `VAR=val claude` commands, so runs (including `autopilot herdr`) can use a different Claude account. Default stays `claude`.
- `defaults.max_model` / `max_effort` (default `claude-opus-5-5` / `xhigh`): hard cap on every session.
- Task commits now include the plan id: `<plan-id>(<task-id>): <title>` (was `autopilot(<task-id>): …`).

## 0.1.0
- Initial versioned release: herdr integration, live reporting, elapsed-time tracking, fix-up rounds.
