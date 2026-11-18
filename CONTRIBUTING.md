# Contributing

Thanks for your interest in claude-autopilot. Bug reports, ideas and pull requests are all welcome.

## Ground rules

- **Standard library only.** `autopilot.py` has no third-party dependencies and must keep working on Python 3.9+.
- **Orchestration stays in code.** Decisions about retries, escalation and state belong in the script, not in prompts.
- **Be conservative with defaults.** A default that spends more money or touches more files needs a good reason.

## Development setup

```bash
git clone https://github.com/Timothy1102/claude-autopilot
cd claude-autopilot
./autopilot.py --version
```

No install step is needed; run the script in place. To try your checkout as the `autopilot` command, run `./install.sh` (it symlinks `autopilot.py`).

## Checks

Run these before opening a PR; CI runs the same ones.

```bash
python3 -m unittest discover -s tests -v   # smoke and unit tests
ruff check autopilot.py tests              # lint (pip install ruff / brew install ruff)
shellcheck -S warning install.sh                      # optional, if you touched the installer
```

The tests never call `claude`. To exercise a real run, point a throwaway repo at a tiny plan and use `--max-total-usd` to cap the spend.

## Pull requests

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep the change focused; one concern per PR.
3. Add or update tests when you change behavior that can be tested without a live Claude session.
4. Update `README.md` / `docs/` when you add or change an option, and add a line under **Unreleased** in `CHANGELOG.md`.
5. Use [Conventional Commits](https://www.conventionalcommits.org) for commit messages (`feat:`, `fix:`, `docs:`, `chore:`), matching the existing history.

## Releasing (maintainers)

1. Move the **Unreleased** notes in `CHANGELOG.md` under a new version heading.
2. Bump `__version__` in `autopilot.py` and the version badge in `README.md`.
3. Tag `vX.Y.Z` and create a GitHub release from the changelog entry.

## Reporting security issues

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
