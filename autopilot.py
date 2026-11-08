#!/usr/bin/env python3
"""
autopilot — run a multi-session Claude Code plan unattended (e.g. overnight).

Each task in tasks.json runs as its own fresh `claude -p` session, with its own
model and effort, inside a dedicated git worktree. The orchestrator (this
script, not Claude) runs the verification commands, optionally asks a reviewer
session to check the diff, commits on success, retries with a stronger model on
failure, and skips work that depends on a failed or blocked task.

Usage:
  autopilot validate <tasks.json>
  autopilot run      <tasks.json> [--dry-run] [--retry-failed] [--only T03,T04]
                                  [--max-total-usd 40] [--notify-cmd "..."]
                                  [--force-clean] [--no-hold] [--claude-bin claude]
  autopilot herdr    <tasks.json> [same options as run]   # start the run in a new herdr workspace
  autopilot status   <tasks.json>

Inside a herdr pane (HERDR_ENV=1) the run reports its state to the herdr sidebar
(working / blocked / done), registers a resume command so herdr restarts it after
a server restart, sends a herdr notification at the end, and stays open so the
final state is still visible when you come back.

Python 3.9+, standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = KIT_DIR / "prompts"

MODEL_LADDER = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
EFFORT_LADDER = ["low", "medium", "high", "xhigh", "max"]

DEFAULTS = {
    "model": "claude-sonnet-5-5",
    "effort": "medium",
    "max_attempts": 3,
    "max_budget_usd": 8.0,          # per worker session (passed to --max-budget-usd)
    "timeout_minutes": 90,          # per worker session
    "verify": [],                   # shell commands run after every task
    "verify_timeout_minutes": 30,
    "setup": [],                    # shell commands run once when the worktree is created
    "review": False,                # run a reviewer session on every task's diff
    "reviewer": {"model": "claude-opus-5-5", "effort": "high", "max_budget_usd": 4.0},
    "permission_mode": "auto",      # auto | acceptEdits | dontAsk | bypassPermissions
    "limit_wait_minutes": 30,       # sleep when a usage/rate limit is hit
    "max_limit_waits": 16,
    "extra_args": [],               # appended to every `claude` call
    "stream": True,                 # stream-json: live view of what each worker is doing
}

# Workers must never touch git history; the orchestrator owns commits.
DENIED_TOOLS = ",".join([
    "Bash(git push)", "Bash(git push *)", "Bash(git commit)", "Bash(git commit *)",
    "Bash(git reset *)", "Bash(git rebase *)", "Bash(git switch *)",
    "Bash(git checkout -b *)", "Bash(git worktree *)", "Bash(git merge *)",
])

RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "summary"],
    "properties": {
        "status": {"type": "string", "enum": ["done", "blocked", "failed"]},
        "summary": {"type": "string"},
        "notes_for_next_tasks": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "recommended_answer": {"type": "string"},
    },
}

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "issues"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "changes_requested"]},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}

LIMIT_RE = re.compile(
    r"usage limit|rate[ _-]?limit|\b429\b|overloaded|quota|limit (reached|resets?)|try again (at|later|in)",
    re.I,
)


# --------------------------------------------------------------------------- utils

def ts() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tail(text: str, n: int = 6000) -> str:
    text = text or ""
    return text if len(text) <= n else "…(truncated)…\n" + text[-n:]


class Logger:
    def __init__(self, path: Path | None):
        self.path = path

    def __call__(self, msg: str) -> None:
        line = f"[{ts()}] {msg}"
        print(line, flush=True)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def child_env() -> dict:
    """Environment for workers, verify and setup commands.

    HERDR_* variables are removed so that herdr's Claude Code integration hooks
    (if installed) don't fire inside `claude -p` workers and overwrite the state
    that autopilot itself reports for its pane.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}


def run(cmd, cwd, check=True, timeout=None, input_text=None, shell=False):
    p = subprocess.run(
        cmd, cwd=str(cwd), input=input_text, capture_output=True, text=True,
        timeout=timeout, shell=shell, executable="/bin/bash" if shell else None,
        env=child_env(),
    )
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stdout}\n{p.stderr}")
    return p


def parse_json_output(stdout: str):
    """`claude -p --output-format json` prints one JSON object; fall back to the last JSON line."""
    stdout = (stdout or "").strip()
    candidates = [stdout] + [ln for ln in reversed(stdout.splitlines()) if ln.startswith("{")]
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            continue
    return None


def parse_stream_result(lines):
    """Return the final `result` event from stream-json output, if any."""
    for line in reversed(lines):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def describe_event(ev: dict):
    """One short human-readable line per worker event, for the live view."""
    if ev.get("type") != "assistant":
        return []
    indent = "    ↳ " if ev.get("parent_tool_use_id") else ""
    out = []
    for block in (ev.get("message") or {}).get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            txt = " ".join(block["text"].split())
            out.append(f"{indent}💬 {txt[:160]}")
        elif block.get("type") == "tool_use":
            inp = block.get("input") or {}
            detail = next((str(inp[k]) for k in ("description", "command", "file_path", "pattern", "path",
                                                   "url", "query", "prompt") if inp.get(k)), "")
            detail = " ".join(detail.split())[:110]
            out.append(f"{indent}🔧 {block.get('name', '?')} {detail}".rstrip())
    return out


class Herdr:
    """Best-effort reporting to herdr (https://herdr.dev) when running inside a herdr pane."""

    SOURCE = "autopilot"
    AGENT = "autopilot"

    def __init__(self, disabled=False):
        self.bin = os.environ.get("HERDR_BIN_PATH")
        self.pane = os.environ.get("HERDR_PANE_ID")
        self.enabled = (not disabled and os.environ.get("HERDR_ENV") == "1"
                        and bool(self.bin) and bool(self.pane))

    def _call(self, *args):
        if not self.enabled:
            return None
        try:
            return subprocess.run([self.bin, *args], capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001  — herdr must never break the run
            return None

    @staticmethod
    def _seq():
        return str(time.time_ns() // 1000)

    def report(self, state, message=None, resume_argv=None):
        args = ["pane", "report-agent", self.pane, "--source", self.SOURCE, "--agent", self.AGENT,
                "--state", state, "--seq", self._seq()]
        if message:
            args += ["--message", message[:200]]
        if resume_argv:
            args += ["--"] + resume_argv
        self._call(*args)

    def title(self, text, summary=None):
        args = ["pane", "report-metadata", self.pane, "--source", "autopilot-display",
                "--title", text[:80], "--seq", self._seq()]
        if summary:
            args += ["--token", f"summary={summary[:80]}"]
        self._call(*args)

    def notify(self, title, body, sound="done"):
        self._call("notification", "show", title[:80], "--body", body[:300], "--sound", sound)

    def release(self):
        self._call("pane", "release-agent", self.pane, "--source", self.SOURCE, "--agent", self.AGENT,
                   "--seq", self._seq())


def git(cwd, *args, check=True) -> str:
    return run(["git", *args], cwd, check=check).stdout.strip()


# --------------------------------------------------------------------------- plan

def load_plan(tasks_path: Path):
    errors, warnings = [], []
    try:
        plan = json.loads(tasks_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, None, [f"cannot parse {tasks_path}: {e}"], []

    d = {**DEFAULTS, **plan.get("defaults", {})}
    d["reviewer"] = {**DEFAULTS["reviewer"], **plan.get("defaults", {}).get("reviewer", {})}

    pid = plan.get("plan_id", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", pid or ""):
        errors.append("plan_id is required and may only contain letters, digits, . _ -")
    tasks = plan.get("tasks") or []
    if not tasks:
        errors.append("tasks is empty")
    ids = [t.get("id") for t in tasks]
    if len(ids) != len(set(ids)) or not all(ids):
        errors.append("every task needs a unique, non-empty id")
    idset = set(ids)
    for t in tasks:
        tid = t.get("id")
        for key in ("title", "instructions"):
            if not t.get(key):
                errors.append(f"{tid}: missing '{key}'")
        for dep in t.get("depends_on", []):
            if dep not in idset:
                errors.append(f"{tid}: depends_on unknown task {dep}")
        if not (t.get("verify") or d["verify"]):
            warnings.append(f"{tid}: no verify commands — only the worker's self-report will be checked")
        if not t.get("acceptance"):
            warnings.append(f"{tid}: no acceptance criteria")
        m = t.get("model", d["model"])
        if m not in MODEL_LADDER and m not in ("opus", "sonnet", "haiku", "fable"):
            warnings.append(f"{tid}: model '{m}' is not in the known ladder; escalation will only bump effort")
    spec = tasks_path.parent / "SPEC.md"
    if not spec.exists():
        errors.append(f"missing {spec}")
    if not errors:
        try:
            topo_order(tasks)
        except ValueError as e:
            errors.append(str(e))
    return plan, d, errors, warnings


def topo_order(tasks):
    by_id = {t["id"]: t for t in tasks}
    indeg = {t["id"]: len(t.get("depends_on", [])) for t in tasks}
    order, ready = [], [t["id"] for t in tasks if indeg[t["id"]] == 0]
    while ready:
        tid = ready.pop(0)
        order.append(by_id[tid])
        for t in tasks:
            if tid in t.get("depends_on", []):
                indeg[t["id"]] -= 1
                if indeg[t["id"]] == 0:
                    ready.append(t["id"])
    if len(order) != len(tasks):
        raise ValueError("dependency cycle in tasks")
    return order


def pick_model(task, d, attempt):
    model = task.get("model", d["model"])
    effort = task.get("effort", d["effort"])
    if attempt <= 1:
        return model, effort
    esc = task.get("escalate_to") or d.get("escalate_to")
    if esc:
        return esc.get("model", model), esc.get("effort", effort)
    # automatic ladder: stronger model first, then more effort
    if model in MODEL_LADDER and MODEL_LADDER.index(model) < len(MODEL_LADDER) - 1:
        return MODEL_LADDER[MODEL_LADDER.index(model) + 1], "high" if effort in ("low", "medium") else effort
    if effort in EFFORT_LADDER and EFFORT_LADDER.index(effort) < EFFORT_LADDER.index("xhigh"):
        return model, EFFORT_LADDER[EFFORT_LADDER.index(effort) + 1]
    return model, effort


# --------------------------------------------------------------------------- runner

class Runner:
    def __init__(self, tasks_path: Path, args):
        self.args = args
        self.tasks_path = tasks_path
        self.plan_dir = tasks_path.parent
        self.repo = Path(git(self.plan_dir, "rev-parse", "--show-toplevel"))
        self.rel = self.plan_dir.relative_to(self.repo)
        self.plan, self.d, errors, warnings = load_plan(tasks_path)
        if errors:
            raise SystemExit("Plan is invalid:\n  " + "\n  ".join(errors))
        self.pid = self.plan["plan_id"]
        self.run_dir = self.plan_dir / "run"
        self.log = Logger(None if args.dry_run else self.run_dir / "autopilot.log")
        for w in warnings:
            self.log("warning: " + w)
        self.wt = self.repo.parent / f"{self.repo.name}.autopilot-{self.pid}"
        self.branch = f"autopilot/{self.pid}"
        self.state_path = self.run_dir / "state.json"
        self.crashed = False            # set by _load_state when a task was mid-run last time
        self.state = self._load_state()
        self.claude = args.claude_bin
        self.herdr = Herdr(disabled=args.dry_run)
        self.current = ""               # short description of what is running, for herdr

    # ---- state
    def _load_state(self):
        st = {"plan_id": None, "total_cost_usd": 0.0, "tasks": {}}
        if self.state_path.exists():
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
        st["plan_id"] = self.plan["plan_id"]
        for t in self.plan["tasks"]:
            s = st["tasks"].setdefault(t["id"], {"status": "pending", "attempts": 0, "cost_usd": 0.0, "history": []})
            if s["status"] == "running":
                s["status"] = "pending"
                self.crashed = True
            if getattr(self.args, "retry_failed", False) and s["status"] in ("failed", "blocked", "skipped"):
                s.update(status="pending", attempts=0)
                s.pop("reason", None)
        return st

    def save(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)
        self.write_report()

    # ---- worktree
    def ensure_worktree(self):
        exclude = Path(git(self.repo, "rev-parse", "--git-path", "info/exclude"))
        if not exclude.is_absolute():
            exclude = self.repo / exclude
        line = f"/{self.rel.as_posix()}/run/"
        if line not in (exclude.read_text() if exclude.exists() else ""):
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a") as f:
                f.write(f"\n{line}\n")

        created = False
        if not self.wt.exists():
            base = self.plan.get("base_branch") or git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
            has_branch = run(["git", "rev-parse", "--verify", "--quiet", self.branch], self.repo, check=False).returncode == 0
            if has_branch:
                git(self.repo, "worktree", "add", str(self.wt), self.branch)
            else:
                git(self.repo, "worktree", "add", "-b", self.branch, str(self.wt), base)
            created = True
            self.log(f"created worktree {self.wt} on branch {self.branch} (from {base})")
        elif git(self.wt, "status", "--porcelain"):
            if self.crashed or self.args.force_clean:
                # Leftovers of an unverified attempt from a run that was killed (e.g. herdr server
                # restart or reboot); they were never committed, so discarding them is safe.
                self.log("discarding uncommitted leftovers from the interrupted attempt")
                self.reset_clean()
            else:
                raise SystemExit(
                    f"Worktree {self.wt} has uncommitted changes. Commit them there yourself, "
                    "or rerun with --force-clean to discard them."
                )

        self.wt_plan = self.wt / self.rel
        self.wt_plan.mkdir(parents=True, exist_ok=True)
        for src in self.plan_dir.rglob("*"):
            relp = src.relative_to(self.plan_dir)
            if src.is_dir() or relp.parts[0] == "run" or relp.name in ("PROGRESS.md", "DECISIONS.md"):
                continue
            dst = self.wt_plan / relp
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for name, header in (("PROGRESS.md", "# Progress log\n"), ("DECISIONS.md", "# Decisions made by workers\n")):
            p = self.wt_plan / name
            if not p.exists():
                p.write_text(header, encoding="utf-8")
        git(self.wt, "add", "-A", str(self.rel))
        if run(["git", "diff", "--cached", "--quiet"], self.wt, check=False).returncode != 0:
            git(self.wt, "commit", "-m", f"autopilot({self.pid}): sync plan")

        # A failed setup leaves a half-prepared worktree; remember that so the next run retries it
        # instead of silently skipping setup because the worktree already exists.
        marker = Path(git(self.wt, "rev-parse", "--absolute-git-dir")) / "autopilot-setup-failed"
        if created or marker.exists():
            if not created:
                self.log("previous setup failed; running setup again")
            marker.write_text("setup did not complete\n")
            for cmd in self.d["setup"]:
                self.log(f"setup: {cmd}")
                p = run(cmd, self.wt, check=False, shell=True, timeout=self.d["verify_timeout_minutes"] * 60)
                if p.returncode != 0:
                    raise SystemExit(f"setup command failed: {cmd}\n{tail(p.stdout + p.stderr)}\n"
                                     "Fix the setup command and rerun; setup will be retried.")
            marker.unlink()

    def reset_clean(self):
        git(self.wt, "reset", "--hard", "-q", "HEAD")
        git(self.wt, "clean", "-fdq")

    # ---- claude
    def _spawn(self, cmd, text_in, out_path, timeout_s, live):
        """Run a worker, tee its stdout to out_path, print a live view, kill the group on timeout."""
        err_path = out_path.with_suffix(".stderr.txt")
        with open(err_path, "w", encoding="utf-8") as err_f, open(out_path, "w", encoding="utf-8") as out_f:
            proc = subprocess.Popen(cmd, cwd=str(self.wt), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=err_f, text=True, env=child_env(), start_new_session=True)
            timed_out = threading.Event()

            def kill():
                timed_out.set()
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(10)
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            timer = threading.Timer(timeout_s, kill)
            timer.daemon = True
            timer.start()
            try:
                proc.stdin.write(text_in)
                proc.stdin.close()
            except BrokenPipeError:
                pass
            lines = []
            live_log = self.run_dir / "live.log"
            with open(live_log, "a", encoding="utf-8") as live_f:
                for line in proc.stdout:
                    lines.append(line)
                    out_f.write(line)
                    if not live or not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    for msg in describe_event(ev):
                        print(f"   │ {msg}", flush=True)
                        live_f.write(f"[{ts()}] {self.current} {msg}\n")
            proc.wait()
            timer.cancel()
        stderr = err_path.read_text(encoding="utf-8")
        if timed_out.is_set():
            return -1, lines, stderr + "\nTIMEOUT: worker exceeded its time limit"
        return proc.returncode, lines, stderr

    def claude_call(self, *, prompt, model, effort, schema, rules_file, budget, tag,
                    permission_mode=None, allowed_tools=None, timeout_min=None):
        """Runs claude -p; waits and resumes on usage/rate limits. Returns (data|None, raw, cost_delta)."""
        resume_sid, waits, cost, prev_total = None, 0, 0.0, 0.0
        while True:
            stream = bool(self.d.get("stream", True))
            cmd = [
                self.claude, "-p",
                "--model", model, "--effort", effort,
                "--permission-mode", permission_mode or self.d["permission_mode"],
                "--permission-prompts", "none",
                "--output-format", "stream-json" if stream else "json",
                "--json-schema", json.dumps(schema),
                "--append-system-prompt-file", str(rules_file),
                "--max-budget-usd", str(budget),
                "--disallowedTools", DENIED_TOOLS,
            ]
            if stream:
                cmd.append("--verbose")
            if allowed_tools:
                cmd += ["--allowedTools", allowed_tools]
            cmd += list(self.d["extra_args"])
            text_in = prompt
            if resume_sid:
                cmd += ["--resume", resume_sid]
                text_in = "You were interrupted by a usage/rate limit. Continue the task exactly where you left off."
            logs = self.run_dir / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            n = len(list(logs.glob(f"{tag}-*.jsonl")))
            out_path = logs / f"{tag}-{n}.jsonl"
            rc, lines, stderr = self._spawn(cmd, text_in, out_path,
                                            (timeout_min or self.d["timeout_minutes"]) * 60, live=stream)
            stdout = "".join(lines)
            data = parse_stream_result(lines) if stream else None
            if data is None:
                data = parse_json_output(stdout)
            if isinstance(data, dict):
                total = float(data.get("total_cost_usd") or 0)
                cost += max(total - prev_total, 0) if resume_sid else total
                prev_total = total
            raw = f"{stdout}\n{stderr}"
            errored = rc != 0 or (isinstance(data, dict) and data.get("is_error"))
            # Only look for limit messages in stderr and the final result, never in tool output
            # (a worker reading code about rate limiting must not look like a usage limit).
            limit_text = stderr + "\n" + "".join(lines[-2:]) + "\n" + str((data or {}).get("result", ""))
            if errored and LIMIT_RE.search(tail(limit_text, 4000)) and waits < self.d["max_limit_waits"]:
                waits += 1
                if isinstance(data, dict) and data.get("session_id"):
                    resume_sid = data["session_id"]
                self.log(f"{tag}: usage/rate limit hit, sleeping {self.d['limit_wait_minutes']} min "
                         f"(wait {waits}/{self.d['max_limit_waits']})")
                self.herdr.report("working", f"{self.current}: waiting for usage limit reset")
                time.sleep(self.d["limit_wait_minutes"] * 60)
                continue
            if errored:
                return None, raw, cost
            return data, raw, cost

    # ---- verification
    def verify(self, task):
        for cmd in task.get("verify", []) + self.d["verify"]:
            try:
                p = run(cmd, self.wt, check=False, shell=True, timeout=self.d["verify_timeout_minutes"] * 60)
            except subprocess.TimeoutExpired:
                return False, f"$ {cmd}\n(timed out)"
            if p.returncode != 0:
                return False, f"$ {cmd}\n(exit {p.returncode})\n{tail(p.stdout + p.stderr)}"
        return True, ""

    def review(self, task, start_head):
        git(self.wt, "add", "-A")
        stat = git(self.wt, "diff", "--cached", "--stat", start_head)
        diff = git(self.wt, "diff", "--cached", start_head)
        if len(diff) > 300_000:
            diff = diff[:300_000] + "\n…(diff truncated; read the files directly if needed)…"
        prompt = (
            f"Review task {task['id']}: {task['title']}\n\n"
            f"Spec: {self.rel.as_posix()}/SPEC.md\n\nInstructions given to the worker:\n{task['instructions']}\n\n"
            "Acceptance criteria:\n" + "\n".join(f"- {a}" for a in task.get("acceptance", [])) +
            f"\n\nDiff stat:\n{stat}\n\nStaged diff:\n{diff}\n"
        )
        r = self.d["reviewer"]
        data, raw, cost = self.claude_call(
            prompt=prompt, model=r["model"], effort=r["effort"], schema=REVIEW_SCHEMA,
            rules_file=PROMPTS_DIR / "reviewer.md", budget=r["max_budget_usd"], tag=f"{task['id']}-review",
            permission_mode="dontAsk", allowed_tools="Read,Grep,Glob", timeout_min=30,
        )
        so = (data or {}).get("structured_output") or {}
        if not so:
            self.log(f"{task['id']}: reviewer produced no verdict; accepting on verification alone")
            return True, [], cost, "reviewer unavailable"
        return so.get("verdict") == "approve", so.get("issues", []), cost, None

    # ---- one task
    def build_prompt(self, task, feedback):
        rel = self.rel.as_posix()
        parts = [
            f"# Autopilot task {task['id']}: {task['title']}",
            f"Plan `{self.pid}`. Read first: `{rel}/SPEC.md`, `{rel}/PROGRESS.md`, `{rel}/DECISIONS.md`.",
            "## What to do\n" + task["instructions"],
        ]
        if task.get("acceptance"):
            parts.append("## Acceptance criteria\n" + "\n".join(f"- {a}" for a in task["acceptance"]))
        if task.get("context_files"):
            parts.append("## Files likely relevant\n" + "\n".join(f"- `{f}`" for f in task["context_files"]))
        cmds = task.get("verify", []) + self.d["verify"]
        if cmds:
            parts.append("## Verification (the orchestrator re-runs these after you finish; all must pass)\n"
                         + "\n".join(f"- `{c}`" for c in cmds))
        if feedback:
            parts.append("## A previous attempt at this task was rejected\n"
                         "The working tree was reset to the last good commit. Avoid repeating the problem below.\n\n"
                         + feedback)
        return "\n\n".join(parts) + "\n"

    def run_task(self, task):
        tid, st = task["id"], self.state["tasks"][task["id"]]
        st["status"] = "running"
        self.save()
        feedback = st.get("last_feedback")
        while st["attempts"] < task.get("max_attempts", self.d["max_attempts"]):
            st["attempts"] += 1
            attempt = st["attempts"]
            model, effort = pick_model(task, self.d, attempt)
            self.reset_clean()
            start_head = git(self.wt, "rev-parse", "HEAD")
            self.log(f"{tid} attempt {attempt}: {model} / {effort}")
            self.current = f"{tid} a{attempt}"
            self.herdr_progress(f"{tid} {task['title']} · attempt {attempt} · {model.replace('claude-', '')}/{effort}")
            started = time.time()
            data, raw, cost = self.claude_call(
                prompt=self.build_prompt(task, feedback), model=model, effort=effort,
                schema=RESULT_SCHEMA, rules_file=PROMPTS_DIR / "worker.md",
                budget=task.get("max_budget_usd", self.d["max_budget_usd"]), tag=f"{tid}-a{attempt}",
                timeout_min=task.get("timeout_minutes"),
            )
            st["cost_usd"] += cost
            self.state["total_cost_usd"] += cost
            entry = {"attempt": attempt, "model": model, "effort": effort, "cost_usd": round(cost, 4),
                     "minutes": round((time.time() - started) / 60, 1),
                     "session_id": (data or {}).get("session_id")}
            st["history"].append(entry)

            if data is None:
                feedback = "The session crashed, timed out or hit its budget cap:\n" + tail(raw, 3000)
                entry["outcome"] = "error"
                self.log(f"{tid}: session error")
                self.save()
                continue
            so = data.get("structured_output") or {}
            status = so.get("status", "unreported")
            summary = so.get("summary") or tail(data.get("result", ""), 1500)

            if status == "blocked":
                st.update(status="blocked", question=so.get("question", summary),
                          recommended_answer=so.get("recommended_answer", ""))
                entry["outcome"] = "blocked"
                self.log(f"{tid}: BLOCKED — {st['question']}")
                self.reset_clean()
                self.save()
                return
            if status == "failed":
                feedback = "The previous worker gave up and reported:\n" + summary
                entry["outcome"] = "worker-failed"
                self.save()
                continue

            ok, out = self.verify(task)
            if not ok:
                feedback = "Verification failed:\n" + out
                entry["outcome"] = "verify-failed"
                self.log(f"{tid}: verification failed")
                self.save()
                continue

            if task.get("review", self.d["review"]):
                approved, issues, rcost, note = self.review(task, start_head)
                st["cost_usd"] += rcost
                self.state["total_cost_usd"] += rcost
                if note:
                    entry["review_note"] = note
                if not approved:
                    feedback = "A reviewer requested changes:\n" + "\n".join(f"- {i}" for i in issues)
                    entry["outcome"] = "review-rejected"
                    self.log(f"{tid}: reviewer requested changes ({len(issues)} issues)")
                    self.save()
                    continue

            with (self.wt_plan / "PROGRESS.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## {tid} — {task['title']}\n- Done on attempt {attempt} ({model}, {effort})\n"
                        f"- Summary: {summary}\n")
                if so.get("notes_for_next_tasks"):
                    f.write(f"- Notes for next tasks: {so['notes_for_next_tasks']}\n")
                for dcs in so.get("decisions", []):
                    f.write(f"- Decision: {dcs}\n")
            git(self.wt, "add", "-A")
            c = run(["git", "commit", "-m", f"autopilot({tid}): {task['title']}", "-m", summary[:3000]],
                    self.wt, check=False)
            if c.returncode != 0:
                feedback = "The git pre-commit hook rejected the change:\n" + tail(c.stdout + c.stderr, 3000)
                entry["outcome"] = "commit-hook-failed"
                self.save()
                continue
            entry["outcome"] = "done"
            st.update(status="done", summary=summary, commit=git(self.wt, "rev-parse", "--short", "HEAD"))
            st.pop("last_feedback", None)
            self.log(f"{tid}: DONE ({st['commit']}, ${st['cost_usd']:.2f})")
            self.save()
            return

        st.update(status="failed", last_feedback=feedback)
        self.reset_clean()
        self.log(f"{tid}: FAILED after {st['attempts']} attempts")
        self.save()

    # ---- whole plan
    def run_all(self):
        order = topo_order(self.plan["tasks"])
        only = set(self.args.only.split(",")) if self.args.only else None
        if self.args.dry_run:
            print(f"Plan {self.pid}: {len(order)} tasks, worktree would be {self.wt}, branch {self.branch}\n")
            for t in order:
                m, e = pick_model(t, self.d, 1)
                m2, e2 = pick_model(t, self.d, 2)
                deps = ",".join(t.get("depends_on", [])) or "-"
                print(f"  {t['id']:<6} {m}/{e:<7} (retry: {m2}/{e2})  deps={deps}  {t['title']}")
            return
        version = run([self.claude, "--version"], self.repo, check=False)
        if version.returncode != 0:
            raise SystemExit(f"cannot run '{self.claude} --version'")
        self.log(f"claude {version.stdout.strip()} — starting plan {self.pid}")
        if self.herdr.enabled:
            self.log(f"herdr detected (pane {self.herdr.pane}): reporting state to the sidebar")
        self.herdr_progress("preparing worktree")
        self.ensure_worktree()
        self.save()
        try:
            while True:
                progressed = False
                for t in order:
                    st = self.state["tasks"][t["id"]]
                    if st["status"] != "pending" or (only and t["id"] not in only):
                        continue
                    dep_status = {dd: self.state["tasks"][dd]["status"] for dd in t.get("depends_on", [])}
                    bad = [f"{k} {v}" for k, v in dep_status.items() if v in ("failed", "blocked", "skipped")]
                    if bad:
                        st.update(status="skipped", reason="dependency " + ", ".join(bad))
                        self.log(f"{t['id']}: skipped ({st['reason']})")
                        self.save()
                        continue
                    if any(v != "done" for v in dep_status.values()):
                        continue
                    if self.args.max_total_usd and self.state["total_cost_usd"] >= self.args.max_total_usd:
                        self.log(f"total budget ${self.args.max_total_usd} reached; stopping")
                        raise StopIteration
                    self.run_task(t)
                    self.herdr_progress(f"{t['id']} {self.state['tasks'][t['id']]['status']}")
                    progressed = True
                    break
                if not progressed:
                    break
        except StopIteration:
            pass
        except KeyboardInterrupt:
            for s in self.state["tasks"].values():
                if s["status"] == "running":
                    s["status"] = "pending"
            self.save()
            self.log("interrupted; state saved, rerun the same command to resume")
            self.herdr.release()
            sys.exit(130)
        self.save()
        summary = self.short_summary()
        self.log(f"finished. {summary}. Report: {self.run_dir / 'REPORT.md'}")
        if self.args.notify_cmd:
            run(self.args.notify_cmd, self.repo, check=False, shell=True, input_text=summary)
        self.finish_in_herdr(summary)

    # ---- herdr
    def resume_argv(self):
        """Command herdr re-runs in this pane after a herdr server restart (no apostrophes allowed)."""
        a = self.args
        argv = ["autopilot", "run", str(self.tasks_path)]
        if a.only:
            argv += ["--only", a.only]
        if a.max_total_usd:
            argv += ["--max-total-usd", str(a.max_total_usd)]
        if a.notify_cmd and "'" not in a.notify_cmd:
            argv += ["--notify-cmd", a.notify_cmd]
        if a.no_hold:
            argv.append("--no-hold")
        if a.claude_bin != "claude" and "/" not in a.claude_bin:
            argv += ["--claude-bin", a.claude_bin]
        if any("'" in x or any(ord(c) < 32 for c in x) for x in argv) or len(" ".join(argv)) > 8000:
            return None
        return argv

    def counts(self):
        c = {}
        for s in self.state["tasks"].values():
            c[s["status"]] = c.get(s["status"], 0) + 1
        return c

    def herdr_progress(self, message):
        if not self.herdr.enabled:
            return
        c, total = self.counts(), len(self.plan["tasks"])
        summary = f"{c.get('done', 0)}/{total} done · ${self.state['total_cost_usd']:.2f}"
        self.herdr.report("working", message, resume_argv=self.resume_argv())
        self.herdr.title(f"autopilot {self.pid} · {summary}", summary=summary)

    def finish_in_herdr(self, summary):
        if not self.herdr.enabled:
            return
        c = self.counts()
        needs_you = c.get("failed", 0) + c.get("blocked", 0)
        if needs_you:
            msg = f"{needs_you} task(s) need you: see {self.rel.as_posix()}/run/REPORT.md"
            self.herdr.report("blocked", msg, resume_argv=self.resume_argv())
            self.herdr.notify(f"autopilot {self.pid} needs you", summary, sound="request")
        else:
            self.herdr.report("idle", "all tasks done", resume_argv=self.resume_argv())
            self.herdr.notify(f"autopilot {self.pid} finished", summary, sound="done")
        self.herdr.title(f"autopilot {self.pid} · {summary.split(': ', 1)[-1]}")
        if self.args.no_hold or not sys.stdin.isatty():
            self.herdr.release()
            return
        # Stay in the pane so the sidebar keeps showing done/blocked until you have looked at it.
        print(f"\nRun finished — {summary}\nReport: {self.run_dir / 'REPORT.md'}\n"
              f"Worktree: {self.wt}\nPress Enter to close this autopilot pane.", flush=True)
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        self.herdr.release()

    # ---- reporting
    def short_summary(self):
        parts = ", ".join(f"{v} {k}" for k, v in sorted(self.counts().items()))
        return f"autopilot {self.pid}: {parts}; ${self.state['total_cost_usd']:.2f}"

    def write_report(self):
        lines = [f"# Autopilot report — {self.pid}", "",
                 f"- Updated: {ts()}", f"- Branch: `{self.branch}`", f"- Worktree: `{self.wt}`",
                 f"- Estimated cost so far: ${self.state['total_cost_usd']:.2f}", "",
                 "| Task | Status | Attempts | Last model / effort | Cost | Note |",
                 "|---|---|---|---|---|---|"]
        for t in self.plan["tasks"]:
            s = self.state["tasks"][t["id"]]
            last = s["history"][-1] if s["history"] else {}
            note = s.get("commit") or s.get("reason") or (s.get("question", "")[:80])
            lines.append(f"| {t['id']} {t['title']} | {s['status']} | {s['attempts']} | "
                         f"{last.get('model', '-')} / {last.get('effort', '-')} | ${s['cost_usd']:.2f} | {note} |")
        blocked = [(t, self.state["tasks"][t["id"]]) for t in self.plan["tasks"]
                   if self.state["tasks"][t["id"]]["status"] in ("blocked", "failed")]
        if blocked:
            lines += ["", "## Needs you"]
            for t, s in blocked:
                lines.append(f"\n### {t['id']} — {t['title']} ({s['status']})")
                if s.get("question"):
                    lines.append(f"- Question: {s['question']}")
                    if s.get("recommended_answer"):
                        lines.append(f"- Worker's proposed answer: {s['recommended_answer']}")
                if s.get("last_feedback"):
                    lines.append("- Last failure:\n\n```\n" + tail(s["last_feedback"], 2000) + "\n```")
        lines += ["", "## Next steps",
                  f"- Review: `git log --oneline {self.branch}` and `git diff <base>...{self.branch}`",
                  "- Answer questions by editing SPEC.md / tasks.json in your main checkout, then "
                  f"`autopilot run {self.tasks_path} --retry-failed`",
                  f"- When happy: merge or open a PR from `{self.branch}`; remove the worktree with "
                  f"`git worktree remove {self.wt}`"]
        (self.run_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- cli

def add_run_options(p):
    p.add_argument("tasks")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--retry-failed", action="store_true", help="reset failed/blocked/skipped tasks to pending")
    p.add_argument("--only", help="comma-separated task ids")
    p.add_argument("--max-total-usd", type=float, default=0.0)
    p.add_argument("--notify-cmd", help="shell command; receives a one-line summary on stdin when the run ends")
    p.add_argument("--force-clean", action="store_true", help="discard uncommitted changes in the worktree")
    p.add_argument("--no-hold", action="store_true", help="inside herdr: exit when done instead of waiting for Enter")
    p.add_argument("--claude-bin", default="claude")


def passthrough(args):
    out = []
    for flag in ("retry_failed", "force_clean", "no_hold", "dry_run"):
        if getattr(args, flag):
            out.append("--" + flag.replace("_", "-"))
    for flag in ("only", "notify_cmd"):
        if getattr(args, flag):
            out += ["--" + flag.replace("_", "-"), getattr(args, flag)]
    if args.max_total_usd:
        out += ["--max-total-usd", str(args.max_total_usd)]
    if args.claude_bin != "claude":
        out += ["--claude-bin", args.claude_bin]
    return out


def launch_in_herdr(args, tasks_path: Path):
    """Create a herdr workspace for this plan and start `autopilot run` in its root pane."""
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr")
    if not herdr:
        raise SystemExit("herdr not found. Install it from https://herdr.dev (brew install herdr).")
    plan, _, errors, _ = load_plan(tasks_path)
    if errors:
        raise SystemExit("Plan is invalid:\n  " + "\n  ".join(errors))
    repo = Path(git(tasks_path.parent, "rev-parse", "--show-toplevel"))
    me = ["autopilot"] if shutil.which("autopilot") else [sys.executable, str(Path(__file__).resolve())]
    cmd = shlex.join(me + ["run", str(tasks_path)] + passthrough(args))

    created = subprocess.run([herdr, "workspace", "create", "--cwd", str(repo),
                              "--label", f"autopilot {plan['plan_id']}", "--no-focus"],
                             capture_output=True, text=True)
    if created.returncode != 0:
        raise SystemExit("Could not create a herdr workspace. Is the herdr server running? "
                         "Start it with `herdr` (or `herdr server` for a headless one).\n"
                         + created.stdout + created.stderr)
    try:
        pane = json.loads(created.stdout)["result"]["root_pane"]["pane_id"]
    except Exception:  # noqa: BLE001
        raise SystemExit("Unexpected response from `herdr workspace create`:\n" + created.stdout)
    started = subprocess.run([herdr, "pane", "run", pane, cmd], capture_output=True, text=True)
    if started.returncode != 0:
        raise SystemExit("`herdr pane run` failed:\n" + started.stdout + started.stderr)
    print(f"Started in herdr workspace 'autopilot {plan['plan_id']}' (pane {pane}).\n"
          f"  watch:   open herdr and select that workspace\n"
          f"  peek:    herdr pane read {pane} --source recent --lines 40\n"
          f"  report:  {tasks_path.parent / 'run' / 'REPORT.md'}\n"
          "You can close this terminal; the run lives in the herdr server.")


def main():
    ap = argparse.ArgumentParser(prog="autopilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("tasks")
    add_run_options(sub.add_parser("run", help="run the plan in this terminal"))
    add_run_options(sub.add_parser("herdr", help="start the run in a new herdr workspace and return"))
    s = sub.add_parser("status")
    s.add_argument("tasks")
    args = ap.parse_args()

    tasks_path = Path(args.tasks).resolve()
    if args.cmd == "validate":
        _, _, errors, warnings = load_plan(tasks_path)
        for w in warnings:
            print("warning:", w)
        for e in errors:
            print("error:", e)
        print("OK" if not errors else "INVALID")
        sys.exit(1 if errors else 0)
    if args.cmd == "status":
        state_path = tasks_path.parent / "run" / "state.json"
        if not state_path.exists():
            print("not started")
            return
        st = json.loads(state_path.read_text(encoding="utf-8"))
        for tid, s in st["tasks"].items():
            print(f"{tid:<8} {s['status']:<8} attempts={s['attempts']} ${s['cost_usd']:.2f}")
        print(f"total ${st['total_cost_usd']:.2f}")
        return
    if args.cmd == "herdr":
        launch_in_herdr(args, tasks_path)
        return
    if args.cmd == "run":
        if not args.dry_run and not shutil.which(args.claude_bin) and not Path(args.claude_bin).exists():
            raise SystemExit(f"'{args.claude_bin}' not found on PATH")
        Runner(tasks_path, args).run_all()


if __name__ == "__main__":
    main()
