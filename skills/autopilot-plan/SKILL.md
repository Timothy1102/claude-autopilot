---
name: autopilot-plan
description: Turn a large change into an autopilot plan (SPEC.md + tasks.json) that the `autopilot` orchestrator runs unattended across many Claude Code sessions, each with its own model and effort. Use when the user runs /autopilot-plan or asks to plan work for an overnight or unattended run.
disable-model-invocation: true
---

# Autopilot planning

You are the planning session. The human is here now; after you finish they will leave, and a script will run each task as a separate `claude -p` session that cannot see this conversation and cannot ask anyone anything. Your job is to make every decision those sessions would otherwise need to ask about, and write it down.

Everything that matters must end up in files. If it was said here but is not in SPEC.md or tasks.json, the workers will never know it.

## Phase 1 — Understand the repo

1. Read CLAUDE.md / AGENTS.md and skim the project layout. Use Explore subagents for broad searches so your own context stays free for the plan.
2. Find the real commands for build, unit tests, type check and lint. Run them now. If the baseline does not pass, tell the human before going further: workers are judged by these commands, so a red baseline makes every task fail.
3. Note the conventions a worker must follow (module layout, error handling, naming, test style) so you can cite them in the spec.

## Phase 2 — Close every open decision

List the decisions a worker could get stuck on: product behaviour and edge cases, data model and migrations, API and interface shapes, error handling, libraries to add or avoid, backward compatibility, performance limits, security constraints, what is explicitly out of scope. Ask the human in small batches with AskUserQuestion, always offering your recommended option first with a one-line reason. When the human says "you decide", decide and record it as a decision. Do not move on while anything is still "TBD".

## Phase 3 — Write `.autopilot/<plan-id>/SPEC.md`

Pick a short kebab-case plan id (for example `billing-v2`). The spec should contain:

- **Goal** and **non-goals**.
- **Decisions**: each with the choice and the reason, numbered (D1, D2…) so tasks can cite them.
- **Design**: components, data model, interfaces with signatures or schemas, and the files or modules each part lives in.
- **Conventions** workers must follow, with paths to example code to imitate.
- **Verification strategy**: which commands prove the work is correct, and what new tests must exist.
- **Escalation rule**: which situations justify a worker returning "blocked" (keep this list short; everything else they decide and log).

## Phase 4 — Write `.autopilot/<plan-id>/tasks.json`

Split the work into tasks a fresh session can finish and verify on its own:

- One coherent change per task, typically touching no more than about 10 files, and small enough to finish well inside one context window. Prefer more small tasks over a few large ones.
- Each task must be **independently verifiable** by commands that exit non-zero on failure. Add task-specific test commands (for example a single test file) on top of the plan-wide `defaults.verify`.
- Each task's `instructions` must stand alone: say what to build, which spec sections and decisions apply (by number), and which existing code to imitate. Never write "as discussed".
- Order by dependency: foundations (types, schema, interfaces) first, then features, then integration. Use `depends_on` so a failure skips only the work that truly depends on it.
- Put tests in the same task as the code they test, or immediately before it.
- End with an integration task (full build and test suite, fix any fallout) and, for anything non-trivial, a final read-only review task on Opus that writes `.autopilot/<plan-id>/REVIEW.md`.

### Choosing model and effort per task

Pick the cheapest setting that will reliably succeed; the orchestrator escalates automatically on failure, so do not over-provision.

| Task shape | model | effort |
|---|---|---|
| Mechanical, fully specified: renames, boilerplate, config, fixtures, docs, wiring an existing pattern | `claude-haiku-5-5` | `medium` |
| Standard feature work with a clear spec and existing patterns to copy | `claude-sonnet-5-5` | `medium` |
| Multi-file changes, tricky but well-specified logic, edge-case-heavy tests | `claude-sonnet-5-5` | `high` |
| Architecture-sensitive or risky: concurrency, security, data migrations, cross-cutting refactors, hard debugging, the integration task, the final review | `claude-opus-5-5` | `high` (or `xhigh`) |

Set `review: true` on tasks where a wrong-but-green result would be costly (auth, money, data migrations, public APIs). Set `escalate_to` only when the default ladder (Haiku → Sonnet → Opus, then more effort) is wrong for that task.

### tasks.json format

```json
{
  "plan_id": "billing-v2",
  "base_branch": "main",
  "defaults": {
    "model": "claude-sonnet-5-5",
    "effort": "medium",
    "max_attempts": 3,
    "max_budget_usd": 8,
    "timeout_minutes": 90,
    "setup": ["cp ../myrepo/.env .env", "npm ci"],
    "verify": ["npm run typecheck", "npm run lint", "npm test -- --run"],
    "review": false,
    "reviewer": { "model": "claude-opus-5-5", "effort": "high" },
    "permission_mode": "auto"
  },
  "tasks": [
    {
      "id": "T01",
      "title": "Add invoice schema and migration",
      "depends_on": [],
      "model": "claude-sonnet-5-5",
      "effort": "high",
      "review": true,
      "context_files": ["src/db/schema.ts", "src/db/migrations/"],
      "instructions": "Implement the Invoice table described in SPEC §Design/Data model, following D2 and D3 …",
      "acceptance": ["Migration applies and rolls back cleanly", "Schema types exported from src/db/index.ts"],
      "verify": ["npm test -- --run src/db"]
    }
  ]
}
```

`setup` runs once, inside the new git worktree the orchestrator creates next to the repo (`../<repo>.autopilot-<plan-id>`), so copy any untracked files the tests need (such as `.env`) and install dependencies there. A fresh worktree has no build output (`dist/`, generated clients), so build workspace packages before any step that imports them (e.g. DB seeding). If setup fails it is retried on the next run.

## Phase 5 — Validate and hand off

1. Run `autopilot validate .autopilot/<plan-id>/tasks.json` and fix every error. Treat warnings about missing `verify` or `acceptance` as errors unless the human agrees otherwise.
2. Run `autopilot run .autopilot/<plan-id>/tasks.json --dry-run` and show the human the task table with models, efforts and dependencies.
3. Re-read SPEC.md as if you were a worker who has never seen this conversation. Fix anything a worker would need to ask about.
4. Hand off the run. The run must live in a herdr workspace, so it survives closing the terminal or dropping SSH, and so its state shows in the herdr sidebar.
   - Show the human the command, for example:
     `autopilot herdr .autopilot/<plan-id>/tasks.json --max-total-usd 60`
     It creates a herdr workspace named `autopilot <plan-id>`, starts the run in it and returns immediately.
   - If `HERDR_ENV=1` (this session is itself running inside herdr), offer to run that command for the human. Run it only after they say yes, because it starts spending their usage.
   - If herdr is not running or not installed, tell them to start `herdr` first, or to run `autopilot run …` directly in any terminal that will stay open.
