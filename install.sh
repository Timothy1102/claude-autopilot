#!/usr/bin/env bash
# Installs the autopilot CLI and the /autopilot-plan skill for every repo on this machine.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
SKILLS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"

mkdir -p "$BIN_DIR" "$SKILLS_DIR"
chmod +x "$KIT/autopilot.py"
ln -sf "$KIT/autopilot.py" "$BIN_DIR/autopilot"
rm -rf "$SKILLS_DIR/autopilot-plan"
cp -R "$KIT/skills/autopilot-plan" "$SKILLS_DIR/autopilot-plan"

echo "Installed:"
echo "  CLI   -> $BIN_DIR/autopilot (make sure $BIN_DIR is on your PATH)"
echo "  Skill -> $SKILLS_DIR/autopilot-plan (use /autopilot-plan in Claude Code)"
command -v claude >/dev/null && echo "  claude $(claude --version)" || echo "  warning: 'claude' not found on PATH"
