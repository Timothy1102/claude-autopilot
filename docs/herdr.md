# Running with herdr

[herdr](https://herdr.dev) is an optional terminal workspace manager. It makes overnight runs more comfortable: a run survives closing the terminal, shows live status in a sidebar and notifies you when it finishes.

## Setup

```bash
brew install herdr                     # or: curl -fsSL https://herdr.dev/install.sh | sh
herdr integration install claude       # shows the planning session's status in herdr
```

herdr doesn't show popup notifications by default. To get one when a run finishes, add this to `~/.config/herdr/config.toml` and run `herdr server reload-config`:

```toml
[ui.toast]
delivery = "system"   # or "herdr" for in-app toasts
```

Then start a run with `autopilot herdr <tasks.json>`: it creates a workspace named `autopilot <plan-id>`, starts `autopilot run` inside it and returns immediately.

## What you get

When `autopilot run` executes inside a herdr pane (`HERDR_ENV=1`), it automatically does the following — no configuration needed:

- **Sidebar status.** The pane is `working` with a description like "T03 Invoice service · attempt 2 · sonnet-5-5/high", and the pane title shows progress ("3/7 done · $4.20"). At the end it switches to `done`, or `blocked` if any task failed or has an open question; herdr plays the matching sound and notification. You can also wait on it from a script: `herdr agent wait <pane-id> --until idle --until blocked`.
- **Live view.** Each worker runs with `--output-format stream-json`, so the pane shows what the worker is thinking as it happens (💭 reasoning, 💬 commentary, ↳ subagents), without the raw tool calls, plus clear markers for each step: 🔍 verification started/passed/FAILED, 🧐 review, ✅ task done. Set `defaults.stream_tools` to also see every tool call (🔧). The same lines are written to `run/live.log`. For a quick peek without opening the UI: `herdr pane read <pane-id> --source recent --lines 40`.
- **Auto-resume.** Autopilot registers its own resume command with herdr. After a herdr server restart, herdr reopens the pane and re-runs `autopilot run …`; `state.json` lets it continue from the interrupted task, and uncommitted changes from the interrupted attempt are discarded. Note that old processes don't survive a restart, and if the machine itself reboots you need to start herdr (`herdr` or `herdr server`) again.
- **Pane stays open** at the end so the done/blocked state is still visible when you return. Use `--no-hold` to exit immediately.
- **No hook interference.** If you've run `herdr integration install claude`, that hook fires in every Claude Code session. Autopilot strips `HERDR_*` variables from the worker environment so `claude -p` sessions don't overwrite the pane state.

**Remote machines.** For a dev box or server over SSH, install herdr and this kit there, connect with `herdr --remote <machine>`, and run `autopilot herdr …` on it. From your laptop, check status with `herdr --machine <name> agent list`.

