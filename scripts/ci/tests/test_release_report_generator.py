#!/usr/bin/env python3
"""Tests for release report generator CLI behavior."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from types import ModuleType
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


def load_release_report_module() -> ModuleType:
    script_path = RELEASE_SCRIPTS_DIR / "generate_release_report.py"
    spec = importlib.util.spec_from_file_location("generate_release_report", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def init_temp_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)

    init_proc = run_cmd(["git", "init"], cwd=path)
    assert init_proc.returncode == 0, init_proc.stderr

    email_proc = run_cmd(["git", "config", "user.email", "release-report@example.com"], cwd=path)
    assert email_proc.returncode == 0, email_proc.stderr
    name_proc = run_cmd(["git", "config", "user.name", "Release Report Tests"], cwd=path)
    assert name_proc.returncode == 0, name_proc.stderr
    gpgsign_proc = run_cmd(["git", "config", "commit.gpgsign", "false"], cwd=path)
    assert gpgsign_proc.returncode == 0, gpgsign_proc.stderr

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

    def test_load_taxonomy_sections_from_default_file(self) -> None:
        module = load_release_report_module()

        taxonomy = module.load_taxonomy(module.DEFAULT_TAXONOMY_PATH)
        section_names = [section["name"] for section in taxonomy["sections"]]

        self.assertEqual(
            section_names,
            [
                "Security",
                "Provider/Model stack",
                "Channels & UX",
                "Memory & Scheduling",
                "Tools & Agent behavior",
                "CI/Release",
            ],
        )
        self.assertEqual(taxonomy["fallback_section"], "Misc")

    def test_group_changes_applies_thematic_rules_with_security_priority(self) -> None:
        module = load_release_report_module()
        taxonomy = module.load_taxonomy(module.DEFAULT_TAXONOMY_PATH)

        changes = [
            {
                "title": "feat(security)!: tighten prompt guard defaults",
                "type": "security",
                "security": True,
                "breaking": True,
            },
            {
                "title": "feat(prompt): block leak vectors in tool outputs",
                "type": "feat",
                "security": True,
                "breaking": False,
            },
            {
                "title": "fix(openai): upgrade provider retries",
                "type": "fix",
                "security": False,
                "breaking": False,
            },
            {
                "title": "fix(telegram): stabilize topic routing",
                "type": "fix",
                "security": False,
                "breaking": False,
            },
            {
                "title": "chore: update internal wording",
                "type": "chore",
                "security": False,
                "breaking": False,
            },
        ]

        grouped = module.group_changes(changes, taxonomy)

        self.assertIn("Security", grouped)
        self.assertIn("Provider/Model stack", grouped)
        self.assertIn("Channels & UX", grouped)
        self.assertIn("Misc", grouped)
        self.assertEqual(
            [change["title"] for change in grouped["Security"]],
            [
                "feat(security)!: tighten prompt guard defaults",
                "feat(prompt): block leak vectors in tool outputs",
            ],
        )

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

    def test_breaking_detection_handles_negated_and_positive_phrases(self) -> None:
        repo = init_temp_git_repo(self.tmp / "breaking-boundary")
        non_hyphen_sha = make_commit(
            repo,
            "docs: clarify non-breaking changes to config defaults",
            "non_hyphen.txt",
        )
        non_space_sha = make_commit(
            repo,
            "docs: clarify non breaking changes to config defaults",
            "non_space.txt",
        )
        positive_sha = make_commit(
            repo,
            "docs: breaking change in CLI flags",
            "positive.txt",
        )

        out_path = repo / "artifacts" / "report.md"
        sources_path = repo / "artifacts" / "report.sources.json"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~3",
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

        payload = json.loads(sources_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["changes"]), 3)

        latest = payload["changes"][0]
        self.assertEqual(latest["sha"], positive_sha)
        self.assertEqual(latest["title"], "docs: breaking change in CLI flags")
        self.assertTrue(latest["breaking"])

        middle = payload["changes"][1]
        self.assertEqual(middle["sha"], non_space_sha)
        self.assertEqual(middle["title"], "docs: clarify non breaking changes to config defaults")
        self.assertFalse(middle["breaking"])

        oldest = payload["changes"][2]
        self.assertEqual(oldest["sha"], non_hyphen_sha)
        self.assertEqual(oldest["title"], "docs: clarify non-breaking changes to config defaults")
        self.assertFalse(oldest["breaking"])

    def test_init_temp_git_repo_disables_commit_gpg_signing(self) -> None:
        repo = init_temp_git_repo(self.tmp / "gpgsign")
        config_proc = run_cmd(["git", "config", "--local", "--get", "commit.gpgsign"], cwd=repo)
        self.assertEqual(config_proc.returncode, 0, msg=config_proc.stderr)
        self.assertEqual(config_proc.stdout.strip(), "false")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
