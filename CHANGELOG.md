# Changelog

Versioning follows [SemVer](https://semver.org). The version lives in `__version__` in `autopilot.py`.

## Unreleased
- Graceful stop: press `q` during `autopilot run` to stop once the running task is finished (state saved, rerun to resume); Ctrl+C still stops immediately. A help line is pinned at the end of the log stream.
- Environment health checks (`defaults.healthcheck`): checked before every attempt, when verification fails and when a worker reports `blocked`. A down environment pauses the run instead of failing the task and escalating; a failed verification is re-run once it is back, a worker blocked by the outage gets the task back in its own session, and if it never recovers the run stops with the attempt not counted.
- Flaky checks: a failed check is re-run alone before the work is rejected (`verify_flaky_retries`, default 1); a pass on the re-run is accepted, listed in REPORT.md and not cached. `verify_max_parallel` caps concurrent checks.
- Workers can no longer use `ScheduleWakeup` / `CronCreate`, and get `BASH_MAX_TIMEOUT_MS` = `verify_timeout_minutes`, so checks run in the foreground instead of being polled with `sleep`.
- `--claude-bin` accepts several comma-separated accounts; a usage limit switches to the next free one instead of sleeping (new session there, told about the partial work).
- The inlined SPEC.md moves into the worker's system prompt so later sessions read it from the prompt cache; `task.context_files` contents are inlined (`inline_context_kb`); a retry gets the rejected attempt's diff as a reference (`retry_diff_kb`).
- `validate` warns when a long plan has `defaults.verify` but no `full_verify` tier.
- REPORT.md shows why a run stopped early (budget, usage limit, environment down) and lists flaky checks.
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
