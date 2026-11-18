# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/Timothy1102/claude-autopilot/security/advisories/new)
instead of opening a public issue. Include what you found, how to reproduce it, and the impact you expect.

You can expect an initial response within a few days. Once a fix is available it will be released and the
report credited (unless you prefer otherwise).

## Scope and threat model

autopilot launches `claude -p` sessions that read and edit files and run commands on your machine without
a human in the loop. Keep that in mind when assessing a report:

- Workers run inside a dedicated git worktree, but they are **not sandboxed**; they run with your user's permissions.
- The default `auto` permission mode uses Claude Code's classifier. `bypassPermissions` removes that safety net and
  should only be used in a container or VM without production credentials.
- `verify`, `setup` and `healthcheck` commands from `tasks.json` are executed as shell commands. Only run plans you trust.

See the [Safety section of the README](README.md#safety) for recommended precautions.
