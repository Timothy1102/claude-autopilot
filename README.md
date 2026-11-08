# claude-autopilot

Run a large task with [Claude Code](https://claude.com/claude-code) from start to finish, unattended — for example overnight.

You and a strong model settle every decision up front in a `SPEC.md` and a `tasks.json`. After that, a Python script runs each task in its own fresh `claude -p` session (clean context, its own model and effort level), verifies the result, commits it, retries on failure, and leaves a report for the morning.

```
 You + Opus (interactive)            autopilot (unattended)                 You (next morning)
┌──────────────────────────┐      ┌──────────────────────────────┐      ┌─────────────────────┐
│ /autopilot-plan          │      │ for each task:               │      │ read REPORT.md      │
│  → SPEC.md               │ ───► │   worker session (claude -p) │ ───► │ review git diff     │
│  → tasks.json            │      │   verify → review → commit   │      │ answer open questions│
│  (20–60 min, you decide) │      │   fix-up / retry / escalate  │      │ merge or open a PR  │
└──────────────────────────┘      └──────────────────────────────┘      └─────────────────────┘
```

## Why it works overnight

**Orchestration lives in code, not in Claude.** Task order, retry counts, model escalation, and state are managed by the script and persisted in `state.json`. If a Claude session crashes or hits a usage limit, nothing is lost — re-run the same command and it resumes where it stopped.

**It doesn't trust self-reports.** A worker saying "done" isn't enough. The script runs your `verify` commands (tests, typecheck, lint) and, for important tasks, a read-only Opus reviewer inspects the diff. Only if both pass does the script commit. On failure, the error is first sent back to the *same worker session* (`--resume`, so context and half-finished code are still there — cheap and fast) for one fix-up round. Only if that also fails is the worktree reset to the last good commit, the error added to the next attempt's prompt, and the model escalated.

**Nobody asks anybody.** Workers run with `--permission-prompts none`, so they have no way to ask you questions. When something is ambiguous they pick the option that best fits the spec and log it in `DECISIONS.md`. Only when they truly can't continue (contradictory spec, missing credentials, irreversible action) do they return `blocked` with a question and a suggested answer. The script records it in the report, skips the tasks that depend on it, and keeps going with independent branches of work.

## Requirements

- [Claude Code](https://claude.com/claude-code) **v2.1.259 or newer** (needs the `--permission-prompts` flag)
- Python 3.9+ (standard library only — no pip install)
- git
- macOS, Linux, or WSL
- Optional but recommended for overnight runs: [herdr](https://herdr.dev) (terminal workspace manager — see [Running with herdr](#running-with-herdr))

## Installation

One install works for every repo:

```bash
git clone https://github.com/<your-username>/claude-autopilot ~/.claude-autopilot
~/.claude-autopilot/install.sh
```

This creates the `autopilot` command in `~/.local/bin` and installs the `/autopilot-plan` skill into `~/.claude/skills/`. Make sure `~/.local/bin` is on your `PATH`.

Optional herdr setup:

```bash
brew install herdr                     # or: curl -fsSL https://herdr.dev/install.sh | sh
herdr integration install claude       # shows the planning session's status in herdr
```

herdr doesn't show popup notifications by default. To get one when a run finishes, add this to `~/.config/herdr/config.toml` and run `herdr server reload-config`:

```toml
[ui.toast]
delivery = "system"   # or "herdr" for in-app toasts
```

## Usage

### 1. Plan (you're present, ~20–60 minutes)

Start your strongest model in the repo you want to change:

```bash
cd ~/code/myrepo
claude --model claude-opus-5-5 --effort xhigh
> /autopilot-plan Rewrite the billing module to be invoice-based, ...
```

The skill reads the codebase, runs the tests to confirm the baseline is green, then asks you about each group of decisions (always with a recommended option). It then writes `.autopilot/<plan-id>/SPEC.md` and `tasks.json`, assigns a model and effort level to every task, and runs `autopilot validate` plus `--dry-run` so you can see the result.

This is the only step that needs you. The time you spend here determines the quality of the whole run.

### 2. Run (you go to sleep)

At the end of planning the skill prints the command to launch (and offers to run it for you):

```bash
autopilot herdr .autopilot/billing-v2/tasks.json --max-total-usd 60
```

This creates a herdr workspace named `autopilot billing-v2`, starts `autopilot run` inside it, and returns immediately. You can close the terminal, shut the laptop, or lose your SSH connection — the run lives on in the herdr server. Without herdr, run it directly (ideally inside `tmux`/`screen`):

```bash
autopilot run .autopilot/billing-v2/tasks.json --max-total-usd 60
```

The script creates a dedicated git worktree at `../<repo>.autopilot-<plan-id>` on branch `autopilot/<plan-id>`, so your main checkout is untouched and you can keep working. Every successful task becomes one commit.

### 3. Review (next morning)

- Check the herdr sidebar: the workspace shows **done** if every task finished, or **blocked** if something needs you.
- Read `.autopilot/<plan-id>/run/REPORT.md` — a status table, costs, and a "Needs you" section listing open questions and last errors.
- Review the work with `git log` and `git diff main...autopilot/<plan-id>`.
- If there are open questions, add the decisions to `SPEC.md` (or edit `tasks.json`) in your main checkout, then resume:

```bash
autopilot run .autopilot/billing-v2/tasks.json --retry-failed
```

When you're happy, merge the branch or open a PR.

## Commands

```
autopilot validate <tasks.json>     # check the plan file for errors
autopilot run      <tasks.json>     # run the plan in this terminal
autopilot herdr    <tasks.json>     # start the run in a new herdr workspace and return
autopilot status   <tasks.json>     # show task states (add --sessions for per-session detail)
```

Options for `run` / `herdr`:

| Option | Meaning |
|---|---|
| `--dry-run` | Print the model/effort for each task's first attempt and retry, without running anything |
| `--retry-failed` | Reset failed / blocked / skipped tasks to pending |
| `--only T03,T04` | Run only the listed task ids |
| `--max-total-usd N` | Stop the whole run once estimated total cost exceeds N |
| `--notify-cmd "..."` | Shell command that receives a one-line summary on stdin when the run ends |
| `--force-clean` | Discard uncommitted changes in the worktree |
| `--no-hold` | Inside herdr, exit when done instead of waiting for Enter |
| `--claude-bin PATH` | Use a specific `claude` binary |

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
- **Precise limit waits** — if a usage-limit message includes a reset time ("resets at 3am", "try again in 25 minutes"), the script sleeps for exactly that long (+1 minute, max 6 hours) instead of a fixed delay. After `max_limit_waits` it stops the run and leaves the task pending rather than burning an attempt and escalating to Opus for a non-code reason.
- **Per-task timing** — the report and `autopilot status --sessions` show duration and fix-up rounds per task, so you can tune models in the next plan.

## Running with herdr

When `autopilot run` executes inside a herdr pane (`HERDR_ENV=1`), it automatically does the following — no configuration needed:

- **Sidebar status.** The pane is `working` with a description like "T03 Invoice service · attempt 2 · sonnet-5-5/high", and the pane title shows progress ("3/7 done · $4.20"). At the end it switches to `done`, or `blocked` if any task failed or has an open question; herdr plays the matching sound and notification. You can also wait on it from a script: `herdr agent wait <pane-id> --until idle --until blocked`.
- **Live view.** Each worker runs with `--output-format stream-json`, so the pane prints every step as it happens (💬 thinking, 🔧 tool and file, ↳ subagents). The same lines are written to `run/live.log`. For a quick peek without opening the UI: `herdr pane read <pane-id> --source recent --lines 40`.
- **Auto-resume.** Autopilot registers its own resume command with herdr. After a herdr server restart, herdr reopens the pane and re-runs `autopilot run …`; `state.json` lets it continue from the interrupted task, and uncommitted changes from the interrupted attempt are discarded. Note that old processes don't survive a restart, and if the machine itself reboots you need to start herdr (`herdr` or `herdr server`) again.
- **Pane stays open** at the end so the done/blocked state is still visible when you return. Use `--no-hold` to exit immediately.
- **No hook interference.** If you've run `herdr integration install claude`, that hook fires in every Claude Code session. Autopilot strips `HERDR_*` variables from the worker environment so `claude -p` sessions don't overwrite the pane state.

**Remote machines.** For a dev box or server over SSH, install herdr and this kit there, connect with `herdr --remote <machine>`, and run `autopilot herdr …` on it. From your laptop, check status with `herdr --machine <name> agent list`.

## tasks.json reference

| Field | Meaning |
|---|---|
| `defaults.verify` | Command(s) run after every task (typecheck, lint, test). Non-zero exit = fail. |
| `defaults.setup` | Run once when the worktree is created, e.g. copy `.env` and `npm ci`. |
| `defaults.permission_mode` | Default `auto` (a classifier vets each action). See [Safety](#safety). |
| `defaults.max_budget_usd` | Cost ceiling per worker session, passed as `--max-budget-usd`. |
| `defaults.limit_wait_minutes` / `max_limit_waits` | On a usage limit, sleep then `--resume` the same session. If the message includes a reset time the script sleeps exactly that long; `limit_wait_minutes` is only the fallback. After `max_limit_waits` the run stops and the task stays pending. |
| `defaults.fixups` / `fixup_budget_usd` / `fixup_timeout_minutes` | Fix-up rounds in the same worker session before reset and escalation (default 1, extra $4 and 30 min per round). `task.fixups` overrides per task; `0` disables. |
| `defaults.inline_spec_kb` | Inline `SPEC.md` (and the tail of PROGRESS/DECISIONS) in the prompt when the spec is at most this size (default 48). `0` makes the worker always read files itself. |
| `defaults.review_inline_diff_kb` | Truncate the diff pasted to the reviewer at this size (default 120); the reviewer uses `git diff` for the rest. |
| `defaults.stream` | Default `true`: use `stream-json` for the live view. Set `false` to fall back to `--output-format json` if your Claude Code version has stream issues. |
| `task.depends_on` | Task ids this task depends on. If a dependency fails or is blocked, this task is skipped; other branches continue. |
| `task.verify` | Task-specific checks, run in addition to `defaults.verify`. |
| `task.review` | If `true`, a read-only reviewer session (default Opus/high) must approve the diff. |
| `task.escalate_to` | `{ "model": ..., "effort": ... }` used from the second attempt on. |
| `task.context_files` | Files the worker should look at first, saving exploration context. |

A complete example is in [`examples/tasks.example.json`](examples/tasks.example.json).

## Safety

- Workers are forbidden from `git push`, `commit`, `reset`, `rebase`, `merge`, and switching branches. Only the script commits, and only inside its own worktree.
- The default `auto` permission mode has a classifier vet each command on your behalf and block risky ones. If you'd rather use `bypassPermissions` so nothing is ever refused, do so **only** in a devcontainer, Docker, or VM with no production credentials — never on your main machine.
- Add deny rules in `~/.claude/settings.json` for anything you never want run unattended (deploys, production database commands, etc.).
- `--max-total-usd` stops the run when estimated total cost passes the threshold. The figure is a client-side estimate and may differ from your actual bill.
- On a subscription plan, an overnight run consumes your session and weekly limits. The script sleeps until the limit resets and continues, but a weekly limit may not reset overnight.

## Limitations and alternatives

Tasks run **sequentially**, by design: each task builds on the previous verified commit, there are no merge conflicts, and for an overnight run sequential throughput is usually enough. Inside a task, the worker still uses subagents to parallelize searching.

Claude Code has other multi-agent options that fit different situations:

- **Dynamic workflows** (`/workflows`, the `ultracode` keyword) — fan-out of many identical agents, such as auditing hundreds of files or migrating independent files, within an interactive session.
- **Projects** on claude.ai/code — when you want the work to run in the cloud even with your machine off.
- **Agent teams** (experimental, off by default) — when you want a lead coordinating sessions that talk to each other.

autopilot fits best for an ordered chain of tasks on your own machine or server, with verification gates between steps.

## Repository layout

```
autopilot.py                 # the orchestrator (standard library only)
prompts/worker.md            # rules for workers, loaded via --append-system-prompt-file
prompts/reviewer.md          # rules for the reviewer
skills/autopilot-plan/       # the /autopilot-plan skill used in the planning session
examples/tasks.example.json  # sample plan file
install.sh                   # installs the command and the skill
```

In the repo you're working on, each plan lives in `.autopilot/<plan-id>/`. The `run/` directory (state, per-session JSONL logs, `live.log`, `REPORT.md`) is added to `.git/info/exclude` automatically. `PROGRESS.md` and `DECISIONS.md` live in the worktree and travel with each commit so later workers know what earlier ones did.

## Versioning

[SemVer](https://semver.org); see [CHANGELOG.md](CHANGELOG.md). Check your version with `autopilot --version`.

## License

[MIT](LICENSE)
