#!/usr/bin/env python3
"""Tests for release report generator CLI behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RELEASE_SCRIPTS_DIR = ROOT / "scripts" / "release"


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def init_temp_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    init_proc = run_cmd(["git", "init"], cwd=path)
    assert init_proc.returncode == 0, init_proc.stderr

    email_proc = run_cmd(["git", "config", "user.email", "release-report@example.com"], cwd=path)
    assert email_proc.returncode == 0, email_proc.stderr
    name_proc = run_cmd(["git", "config", "user.name", "Release Report Tests"], cwd=path)
    assert name_proc.returncode == 0, name_proc.stderr

    (path / "README.md").write_text("bootstrap\n", encoding="utf-8")
    add_proc = run_cmd(["git", "add", "README.md"], cwd=path)
    assert add_proc.returncode == 0, add_proc.stderr
    commit_proc = run_cmd(["git", "commit", "-m", "chore: bootstrap"], cwd=path)
    assert commit_proc.returncode == 0, commit_proc.stderr
    return path


def make_commit(repo: Path, message: str, filename: str) -> str:
    (repo / filename).write_text(f"{message}\n", encoding="utf-8")
    add_proc = run_cmd(["git", "add", filename], cwd=repo)
    assert add_proc.returncode == 0, add_proc.stderr
    commit_proc = run_cmd(["git", "commit", "-m", message], cwd=repo)
    assert commit_proc.returncode == 0, commit_proc.stderr

    sha_proc = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo)
    assert sha_proc.returncode == 0, sha_proc.stderr
    return sha_proc.stdout.strip()


class ReleaseReportGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zc-release-report-tests-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _script(self) -> str:
        return str(RELEASE_SCRIPTS_DIR / "generate_release_report.py")

    def test_missing_from_tag_returns_exit_2(self) -> None:
        out_path = self.tmp / "missing-from.md"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "__missing_release_report_tag__",
                "--to",
                "HEAD",
                "--out",
                str(out_path),
            ],
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("from tag not found", proc.stderr.lower())
        self.assertFalse(out_path.exists())

    def test_valid_refs_write_placeholder_output(self) -> None:
        out_path = self.tmp / "ok.md"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD",
                "--to",
                "HEAD",
                "--out",
                str(out_path),
            ],
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(out_path.read_text(encoding="utf-8"), "# placeholder\n")

    def test_invalid_to_ref_returns_exit_2_and_no_output(self) -> None:
        out_path = self.tmp / "missing-to.md"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD",
                "--to",
                "__missing_release_report_to__",
                "--out",
                str(out_path),
            ],
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("to ref not found", proc.stderr.lower())
        self.assertFalse(out_path.exists())

    def test_git_validation_failure_returns_distinct_code(self) -> None:
        out_path = self.tmp / "git-fail.md"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD",
                "--to",
                "HEAD",
                "--out",
                str(out_path),
            ],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn("git failed while validating --from", proc.stderr.lower())
        self.assertIn("not a git repository", proc.stderr.lower())
        self.assertFalse(out_path.exists())

    def test_collects_changes_from_range_and_writes_sources_json(self) -> None:
        repo = init_temp_git_repo(self.tmp / "repo")
        security_sha = make_commit(repo, "feat(security)!: harden prompt guard", "security.txt")
        fix_sha = make_commit(repo, "fix(channel): recover telegram polling", "channel.txt")

        out_path = repo / "artifacts" / "report.md"
        sources_path = repo / "artifacts" / "report.sources.json"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~2",
                "--to",
                "HEAD",
                "--out",
                str(out_path),
                "--sources-json",
                str(sources_path),
            ],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(out_path.exists())
        self.assertTrue(sources_path.exists())

        payload = json.loads(sources_path.read_text(encoding="utf-8"))
        self.assertIn("changes", payload)
        self.assertEqual(len(payload["changes"]), 2)

        first = payload["changes"][0]
        self.assertEqual(first["sha"], fix_sha)
        self.assertEqual(first["title"], "fix(channel): recover telegram polling")
        self.assertEqual(first["type"], "fix")
        self.assertFalse(first["security"])
        self.assertFalse(first["breaking"])

        second = payload["changes"][1]
        self.assertEqual(second["sha"], security_sha)
        self.assertEqual(second["title"], "feat(security)!: harden prompt guard")
        self.assertEqual(second["type"], "security")
        self.assertTrue(second["security"])
        self.assertTrue(second["breaking"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
