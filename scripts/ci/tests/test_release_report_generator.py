#!/usr/bin/env python3
"""Tests for release report generator CLI behavior."""

from __future__ import annotations

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
