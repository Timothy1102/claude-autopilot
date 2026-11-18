"""Smoke tests for the autopilot CLI. Standard library only: python3 -m unittest discover -s tests"""
import datetime as dt
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "autopilot.py"
EXAMPLES = ROOT / "examples"

spec = importlib.util.spec_from_file_location("autopilot", SCRIPT)
autopilot = importlib.util.module_from_spec(spec)
sys.modules["autopilot"] = autopilot
spec.loader.exec_module(autopilot)


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


class Helpers(unittest.TestCase):
    def test_fmt_dur(self):
        self.assertEqual(autopilot.fmt_dur(5 * 60), "5m")
        self.assertEqual(autopilot.fmt_dur(65 * 60), "1h05m")

    def test_fmt_secs(self):
        self.assertEqual(autopilot.fmt_secs(4.25), "4.2s")
        self.assertEqual(autopilot.fmt_secs(120), "2m")

    def test_tail_truncates(self):
        self.assertEqual(autopilot.tail("abc", 10), "abc")
        self.assertTrue(autopilot.tail("x" * 100, 10).endswith("x" * 10))

    def test_parse_limit_wait_relative(self):
        self.assertEqual(autopilot.parse_limit_wait("Usage limit reached. Try again in 25 minutes."), 26)

    def test_parse_limit_wait_unknown(self):
        self.assertIsNone(autopilot.parse_limit_wait("something unrelated"))

    def test_parse_limit_wait_capped(self):
        self.assertEqual(autopilot.parse_limit_wait("try again in 48 hours", dt.datetime.now()), 360)


class Cli(unittest.TestCase):
    def test_version_matches_module(self):
        r = run_cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn(autopilot.__version__, r.stdout + r.stderr)

    def test_help(self):
        r = run_cli("--help")
        self.assertEqual(r.returncode, 0)
        for cmd in ("validate", "run", "herdr", "status"):
            self.assertIn(cmd, r.stdout)

    def test_example_plan_validates_and_dry_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(EXAMPLES, repo / "examples")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            plan = repo / "examples" / "tasks.example.json"
            r = run_cli("validate", str(plan), cwd=repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)
            r = run_cli("run", str(plan), "--dry-run", cwd=repo)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("T01", r.stdout)

    def test_invalid_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "tasks.json"
            plan.write_text(json.dumps({"plan_id": "x", "tasks": []}))
            r = run_cli("validate", str(plan), cwd=tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("INVALID", r.stdout + r.stderr)


class Repo(unittest.TestCase):
    def test_example_plan_ids_are_unique_and_deps_exist(self):
        plan = json.loads((EXAMPLES / "tasks.example.json").read_text())
        ids = [t["id"] for t in plan["tasks"]]
        self.assertEqual(len(ids), len(set(ids)))
        for t in plan["tasks"]:
            for d in t.get("depends_on", []):
                self.assertIn(d, ids)

    def test_changelog_mentions_unreleased(self):
        self.assertIn("## Unreleased", (ROOT / "CHANGELOG.md").read_text())


if __name__ == "__main__":
    unittest.main()
