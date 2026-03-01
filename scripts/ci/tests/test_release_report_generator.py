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

    def test_missing_from_tag_returns_exit_2(self) -> None:
        proc = run_cmd(
            [
                "python3",
                str(RELEASE_SCRIPTS_DIR / "generate_release_report.py"),
                "--from",
                "__missing_release_report_tag__",
                "--to",
                "HEAD",
                "--out",
                str(self.tmp / "out.md"),
            ],
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("from tag not found", proc.stderr.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
