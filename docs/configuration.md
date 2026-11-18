# Configuration

Everything about `tasks.json`, model selection and verification tiers. A complete sample plan is in [`examples/tasks.example.json`](../examples/tasks.example.json).

## tasks.json reference

| Field | Meaning |
|---|---|
| `defaults.verify` | Fast checks run after every task (typecheck, lint, targeted tests); may use `{changed_files}`. Non-zero exit = fail. |
| `defaults.full_verify` / `full_verify_every` | Heavy checks (full suite, build) run on tasks with `full_verify: true`, after every Nth task (`0` = off), and in the final gate. See [Fast verification](#fast-verification-without-losing-the-safety-net). |
| `defaults.final_verify` | Commands run once when every task is done. Default: `verify` + `full_verify`. |
| `defaults.verify_parallel` / `verify_fail_fast` / `verify_cache` | Run commands concurrently (default `true`), cancel the rest on the first failure (`true`), skip commands that already passed on an identical tree (`true`). |
| `defaults.verify_timeout_minutes` | Timeout per verify command (default 30). Also the worker's Bash timeout cap (`BASH_MAX_TIMEOUT_MS`). |
| `defaults.verify_max_parallel` / `verify_flaky_retries` | Cap on concurrently running checks (default `0` = all, at most 8); how many times failed checks are re-run alone before the work is rejected (default 1, `0` = off). |
| `defaults.healthcheck` | Commands that prove the environment is up (database, Docker, services); all must exit 0. Checked before every attempt, on verification failures and on `blocked`. See [Built-in time and cost savers](#built-in-time-and-cost-savers). |
| `defaults.healthcheck_timeout_seconds` / `healthcheck_wait_minutes` / `max_health_waits` | Per-command timeout (60 s); pause between checks while it is down (5 min); waits before the run stops (12). |
| `defaults.setup` | Run once when the worktree is created, e.g. copy `.env` and `npm ci`. |
| `defaults.permission_mode` | Default `auto` (a classifier vets each action). See [Safety](../README.md#safety). |
| `defaults.max_model` / `max_effort` | Hard ceiling for every session (workers, fix-ups, reviewer, escalation). Defaults: `claude-opus-5-5` / `xhigh`; anything higher is clamped down. |
| `defaults.max_budget_usd` | Cost ceiling per worker session, passed as `--max-budget-usd`. |
| `defaults.limit_wait_minutes` / `max_limit_waits` | On a usage limit, sleep then `--resume` the same session. If the message includes a reset time the script sleeps exactly that long; `limit_wait_minutes` is only the fallback. After `max_limit_waits` the run stops and the task stays pending. |
| `defaults.fixups` / `fixup_budget_usd` / `fixup_timeout_minutes` | Fix-up rounds in the same worker session before reset and escalation (default 2, extra $4 and 30 min per round). `task.fixups` overrides per task; `0` disables. |
| `defaults.inline_spec_kb` | Inline `SPEC.md` (and the tail of PROGRESS/DECISIONS) in the prompt when the spec is at most this size (default 48). `0` makes the worker always read files itself. |
| `defaults.inline_context_kb` | Paste the current contents of `task.context_files` into the prompt up to this total (default 24, `0` = off). |
| `defaults.retry_diff_kb` | Show the next attempt the rejected attempt's diff up to this size (default 40, `0` = off). |
| `defaults.review_inline_diff_kb` | Truncate the diff pasted to the reviewer at this size (default 120); the reviewer uses `git diff` for the rest. |
| `defaults.stream_tools` | Default `false`. Set `true` to also print every tool call (🔧 Bash commands, file paths) in the live view. |
| `defaults.stream` | Default `true`: use `stream-json` for the live view. Set `false` to fall back to `--output-format json` if your Claude Code version has stream issues. |
| `task.depends_on` | Task ids this task depends on. If a dependency fails or is blocked, this task is skipped; other branches continue. |
| `task.verify` | Task-specific checks, run in addition to `defaults.verify`. |
| `task.skip_default_verify` | `true`: run only `task.verify`, not `defaults.verify` (docs and read-only tasks). |
| `task.full_verify` | `true` forces the full tier for this task; `false` keeps it out of the `full_verify_every` checkpoints. |
| `task.review` | If `true`, a read-only reviewer session (default Opus/high) must approve the diff. |
| `task.escalate_to` | `{ "model": ..., "effort": ... }` used from the second attempt on. |
| `task.context_files` | Files the worker should look at first, saving exploration context. |

A complete example is in [`examples/tasks.example.json`](../examples/tasks.example.json).


## Model and effort selection

During planning, the strong model assigns a `model` and `effort` to each task:

| Kind of work | Suggested setting |
|---|---|
| Mechanical, fully specified changes | Haiku |
| Ordinary features | Sonnet / medium |
| Hard multi-file logic | Sonnet / high |
| Architecture, migrations, concurrency, security, integration, final review | Opus / high |

The rule is to pick the cheapest setting likely to succeed, because failures escalate automatically in two steps:

1. **Fix-up round.** Verify errors, reviewer feedback, or commit-hook failures are sent back to the same worker session, same model, with a small extra budget (`fixup_budget_usd`). That session already has the context and the half-done code, so it usually fixes things in minutes.
2. **Escalation.** Only if the fix-up fails: reset the worktree and retry with the next model (Haiku → Sonnet → Opus, effort at least `high`); if already on Opus, raise the effort.

Override per task with `escalate_to`, or set `fixups: 0` to skip step 1.

### Built-in time and cost savers

- **Fix-up before escalation** — a Sonnet task that fails a single test isn't thrown away and redone on Opus.
- **Spec inlined in the prompt** — if `SPEC.md` is under `inline_spec_kb` (default 48 KB), the worker gets it, plus the tail of `PROGRESS.md` and `DECISIONS.md`, directly in the task message, saving several file-read tool calls per task.
- **Bounded review diffs** — the diff pasted to the reviewer is truncated at `review_inline_diff_kb` (default 120 KB); the reviewer fetches the rest per file with `git diff`.
- **Account fallback** — with `--claude-bin claude,claude-personal` a usage limit on one account moves the work to the next free account instead of sleeping. A session can only be resumed on the account that created it, so the other account starts a new session that is told the partial work is in the tree (or, for a fix-up round, gets the task plus the rejection to fix). Sleeping happens only when every account is limited.
- **Environment health checks** — `defaults.healthcheck` (e.g. `pg_isready …`, `docker info`) runs before every attempt, when verification fails and when a worker reports `blocked`. While it fails the run waits (`healthcheck_wait_minutes`, up to `max_health_waits`) instead of failing the task and escalating to a stronger model for a reason that has nothing to do with the code; a failed verification is re-run once the environment is back, and a worker that was blocked by the outage gets the task back in its own session. If it never recovers the run stops with the task still pending and the attempt not counted, and REPORT.md says the environment is down. Workers are told to return `blocked` at once on an outage instead of spending an hour debugging Docker.
- **SPEC in the system prompt** — when `SPEC.md` is inlined it goes into the worker's system prompt (`run/worker-rules.md`) rather than the task message. It is the same for every task, so later sessions read it from the prompt cache instead of writing it again; only the task, PROGRESS.md and DECISIONS.md differ per task.
- **Context files inlined** — the current contents of `task.context_files` (files, not directories) are pasted into the task message up to `inline_context_kb` (default 24 KB), saving the worker's first read round-trips.
- **Retries see the rejected diff** — when an attempt is rejected and the tree is reset, its diff (up to `retry_diff_kb`, default 40 KB; the full diff is saved in `run/logs/<task>-a<n>.diff`) goes into the next attempt's prompt as a reference, so the stronger model reuses the good parts instead of rediscovering them.
- **No idle polling** — workers may not use `ScheduleWakeup`/`CronCreate`, and get `BASH_MAX_TIMEOUT_MS` equal to `verify_timeout_minutes`, so they run checks in the foreground instead of starting them in the background and polling with `sleep`.
- **Precise limit waits** — if a usage-limit message includes a reset time ("resets at 3am", "try again in 25 minutes"), the script sleeps for exactly that long (+1 minute, max 6 hours) instead of a fixed delay. After `max_limit_waits` it stops the run and leaves the task pending rather than burning an attempt and escalating to Opus for a non-code reason.
- **Per-task timing** — the report and `autopilot status --sessions` show duration and fix-up rounds per task, so you can tune models in the next plan.

### Fast verification without losing the safety net

Every task is gated by `defaults.verify` plus its own `task.verify`. Running the whole suite after every task is the slowest part of a run, so verification is tiered:

| Tier | Config | Runs |
|---|---|---|
| fast | `defaults.verify`, `task.verify` | after every task (lint, typecheck, tests for the changed files) |
| full | `defaults.full_verify` | on tasks with `"full_verify": true`, after every Nth task (`full_verify_every`), and in the final gate |
| final | `defaults.final_verify` (default: `verify` + `full_verify`) | once, when every task is done |

- **Parallel.** The commands of one verification run concurrently (`verify_parallel`, default `true`), so the wall time is the slowest command, not the sum. By default the first failure kills the rest (`verify_fail_fast`, default `true`); set it to `false` to collect every failure in one message. Set `verify_parallel` to `false` if your commands share state (the same build directory or test database).
- **Changed files only.** `{changed_files}` in any verify command expands to the shell-quoted files the task added, modified or renamed, e.g. `npx vitest related --run {changed_files}`, `jest --findRelatedTests {changed_files}`, `eslint {changed_files}`. If nothing changed the command is skipped (logged). In the final gate it covers the whole branch.
- **Skip defaults for a task.** `"skip_default_verify": true` runs only that task's own `verify` (docs, read-only review tasks). `"full_verify": false` keeps a task out of the checkpoints; `true` forces the full tier.
- **Cache.** A command that passed on a content-identical tree is not run again (`verify_cache`, default `true`): retries that reproduce the same tree, a fix-up round that changes nothing, or the final gate right after a task that already ran the same command. The key is the content of tracked and untracked files (not ignored files) plus the exact command; `PROGRESS.md` and `DECISIONS.md` are left out. Turn it off if a check depends on state outside the repo (database, network, clock).
- **Flaky checks.** A failed check is re-run alone, one at a time, before the work is rejected (`verify_flaky_retries`, default 1). Parallel suites can push the machine so hard that timing-sensitive tests time out; without this the worker gets a fix-up round, or the task is redone on a stronger model, for nothing. A check that fails and then passes alone is accepted, listed under "Flaky checks" in REPORT.md, and not cached (the final gate runs it again). Timeouts are not re-run. `verify_max_parallel` caps how many checks run at once when each one saturates the CPU.
- **Final gate.** When every task is done the whole branch is verified once more and the result goes into `REPORT.md`. This is what makes a light fast tier safe: a regression that slipped past targeted tests is caught here, and every task is its own commit, so `git bisect` finds the culprit.
- **No duplicate runs.** The worker runs only the fast tier itself; the full tier is left to the orchestrator and a failure is sent back to the same session.
- **Incremental tools.** Workers share one worktree and `git clean` does not remove ignored files, so caches such as `tsc --incremental`, `eslint --cache` and the vitest/jest caches survive between tasks.

```json
"defaults": {
  "verify": ["npm run typecheck", "npm run lint", "npx vitest related --run {changed_files}"],
  "full_verify": ["npm test -- --run", "npm run build"],
  "full_verify_every": 4
}
```

Trade-off: with a light fast tier a regression in a distant module shows up at the next checkpoint or the final gate instead of right away, where the worker no longer has the context. Give tasks that touch shared code (schema, types, config) `"full_verify": true`.

