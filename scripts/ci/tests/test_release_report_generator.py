#!/usr/bin/env python3
"""Tests for release report generator CLI behavior."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from types import ModuleType
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


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


def make_commit(
    repo: Path,
    message: str,
    filename: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
) -> str:
    (repo / filename).write_text(f"{message}\n", encoding="utf-8")
    add_proc = run_cmd(["git", "add", filename], cwd=repo)
    assert add_proc.returncode == 0, add_proc.stderr
    if (author_name is None) ^ (author_email is None):
        raise ValueError("author_name and author_email must be provided together")

    commit_cmd = ["git", "commit", "-m", message]
    if author_name and author_email:
        commit_cmd = [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        ]

    commit_proc = run_cmd(commit_cmd, cwd=repo)
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

    def test_group_changes_avoids_substring_false_positives_for_short_keywords(self) -> None:
        module = load_release_report_module()
        taxonomy = module.load_taxonomy(module.DEFAULT_TAXONOMY_PATH)

        changes = [
            {
                "title": "fix: improve precision math",
                "type": "fix",
                "security": False,
                "breaking": False,
            },
            {
                "title": "fix: linux packaging issue",
                "type": "fix",
                "security": False,
                "breaking": False,
            },
            {
                "title": "fix(ci): tighten release pipeline checks",
                "type": "fix",
                "security": False,
                "breaking": False,
            },
            {
                "title": "feat: improve ux handoff for channel setup",
                "type": "feat",
                "security": False,
                "breaking": False,
            },
        ]

        grouped = module.group_changes(changes, taxonomy)

        ci_titles = [change["title"] for change in grouped.get("CI/Release", [])]
        channels_titles = [change["title"] for change in grouped.get("Channels & UX", [])]
        misc_titles = [change["title"] for change in grouped.get("Misc", [])]

        self.assertIn("fix(ci): tighten release pipeline checks", ci_titles)
        self.assertIn("feat: improve ux handoff for channel setup", channels_titles)
        self.assertNotIn("fix: improve precision math", ci_titles)
        self.assertNotIn("fix: linux packaging issue", channels_titles)
        self.assertIn("fix: improve precision math", misc_titles)
        self.assertIn("fix: linux packaging issue", misc_titles)

    def test_clean_commit_sentence_strips_prefixes_and_pr_suffix(self) -> None:
        module = load_release_report_module()

        self.assertEqual(
            module.clean_commit_sentence("feat(auth): improve OAuth login for CI with Qdrant (#1234)"),
            "Improve OAuth login for CI with Qdrant.",
        )
        self.assertEqual(
            module.clean_commit_sentence("fix(ci): stabilize CI retries"),
            "Stabilize CI retries.",
        )
        self.assertEqual(
            module.clean_commit_sentence("chore(memory): tune Qdrant segment optimizer (#77)"),
            "Tune Qdrant segment optimizer.",
        )
        self.assertEqual(
            module.clean_commit_sentence("release: cut v1.2.3"),
            "Release: cut v1.2.3.",
        )

    def test_render_markdown_cleans_commit_subjects_in_area_bullets(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "deadbeef1",
                    "title": "feat(auth): improve OAuth login for CI with Qdrant (#1234)",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                },
                {
                    "sha": "deadbeef2",
                    "title": "fix(security): harden prompt guard defaults (#88)",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                },
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[grouped_changes["Security"][0]],
        )

        self.assertIn("## Security", body)
        security_section = body.split("## Security", 1)[1].split("## Community Contributors", 1)[0]
        self.assertIn("- Improve OAuth login for CI with Qdrant", security_section)
        self.assertNotIn("feat(auth): improve OAuth login for CI with Qdrant", security_section)
        self.assertNotIn("(#1234)", security_section)
        self.assertIn("- Harden prompt guard defaults", security_section)
        self.assertNotIn("fix(security): harden prompt guard defaults", security_section)
        self.assertNotIn("(#88)", security_section)

    def test_select_main_area_bullets_elevates_strategic_topics(self) -> None:
        module = load_release_report_module()

        section_changes = [
            {
                "sha": f"deadbeef{i}",
                "title": f"fix(ci): routine stabilization item {i}",
                "type": "fix",
                "security": False,
                "breaking": False,
            }
            for i in range(8)
        ]
        section_changes.append(
            {
                "sha": "feedface1",
                "title": "feat(providers): add StepFun provider with onboarding and docs parity",
                "type": "feat",
                "security": False,
                "breaking": False,
            }
        )
        section_changes.append(
            {
                "sha": "feedface2",
                "title": "feat(tools): add sub-agent orchestration (spawn, list, manage)",
                "type": "feat",
                "security": False,
                "breaking": False,
            }
        )

        selected = module.select_main_area_bullets(section_changes, max_items=6)
        selected_titles = [str(item["title"]) for item in selected]
        self.assertLessEqual(len(selected_titles), 6)
        self.assertTrue(
            any("stepfun" in title.lower() for title in selected_titles),
            msg=f"expected StepFun mention in selected bullets: {selected_titles}",
        )
        self.assertTrue(
            any("orchestration" in title.lower() or "swarm" in title.lower() for title in selected_titles),
            msg=f"expected orchestration/swarm mention in selected bullets: {selected_titles}",
        )

    def test_render_markdown_compounds_semantic_duplicates_with_refs_and_authors(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "aaaa1111",
                    "title": "feat(security): add role-policy and otp challenge foundations",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                    "author_name": "Alice",
                    "author_email": "alice@example.com",
                },
                {
                    "sha": "bbbb2222",
                    "title": "feat(security): add role policy and OTP challenge foundation",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                    "author_name": "Bob",
                    "author_email": "bob@example.com",
                },
                {
                    "sha": "cccc3333",
                    "title": "fix(security): tighten prompt-guard thresholds",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                    "author_name": "Carol",
                    "author_email": "carol@example.com",
                },
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[],
        )

        security_section = body.split("## Security", 1)[1].split("## Community Contributors", 1)[0]
        bullets = [line.strip() for line in security_section.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(len(bullets), 2)

        combined_role_bullet = next(
            (
                line
                for line in bullets
                if "role policy" in line.lower()
                and "otp challenge" in line.lower()
                and "foundation" in line.lower()
            ),
            "",
        )
        self.assertTrue(combined_role_bullet)
        self.assertIn("aaaa1111", combined_role_bullet)
        self.assertIn("alice", combined_role_bullet)
        self.assertIn("bbbb2222", combined_role_bullet)
        self.assertIn("bob", combined_role_bullet)

    def test_render_markdown_pr_only_mode_omits_commit_refs(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "aaaa1111",
                    "title": "feat(security): add role-policy and otp challenge foundations",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                    "author_name": "Alice",
                    "author_email": "alice@example.com",
                    "pr_number": None,
                },
                {
                    "sha": "bbbb2222",
                    "title": "fix(security): tighten prompt-guard thresholds (#88)",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                    "author_name": "Bob",
                    "author_email": "bob@example.com",
                    "pr_number": 88,
                },
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[],
            repo_slug="zeroclaw-labs/zeroclaw",
            main_ref_mode="pr_only",
        )

        security_section = body.split("## Security", 1)[1].split("## Community Contributors", 1)[0]
        self.assertIn("[#88](https://github.com/zeroclaw-labs/zeroclaw/pull/88)", security_section)
        self.assertNotIn("commit aaaa1111", security_section)
        self.assertNotIn("commit bbbb2222", security_section)

    def test_render_markdown_uses_single_area_description_and_sentence_bullets(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": f"deadbeef{i}",
                    "title": f"fix(security): harden boundary {i}",
                    "type": "security",
                    "security": True,
                    "breaking": i == 0,
                }
                for i in range(6)
            ]
        }
        highlights = grouped_changes["Security"][:2]

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=highlights,
        )

        self.assertIn("## Security", body)
        self.assertNotIn("## Summary", body)
        self.assertNotIn("## Highlights", body)
        self.assertNotIn("## Narrative Overview", body)
        self.assertNotIn("## Impact & Risk Snapshot", body)
        self.assertNotIn("## Key Changes by Area", body)
        self.assertNotIn("## Full Changelog", body)
        intro_section = body.split("## Security", 1)[0]
        self.assertIn("This release brings", intro_section)
        self.assertNotIn("\n- ", intro_section)
        section = body.split("## Security", 1)[1].split("## Community Contributors", 1)[0]

        content_lines = [line.strip() for line in section.splitlines() if line.strip()]
        self.assertGreater(len(content_lines), 2)
        self.assertFalse(content_lines[0].startswith("- "))

        key_change_bullets = [line.strip() for line in section.splitlines() if line.strip().startswith("- ")]
        self.assertGreaterEqual(len(key_change_bullets), 6)
        self.assertTrue(all("**Scope:**" not in line for line in key_change_bullets))
        self.assertTrue(all("|" not in line for line in key_change_bullets))
        self.assertTrue(all(", which " not in line.lower() for line in key_change_bullets))

        def _is_valid_ref_part(value: str) -> bool:
            return bool(
                re.fullmatch(
                    r"(?:#\d+|PR #\d+|commit [0-9a-f]{8}|\[(?:#\d+|PR #\d+|commit [0-9a-f]{8})\]\([^)]+\))(?: by (?:[^;()]+|\[[^\]]+\]\([^)]+\)))?",
                    value,
                )
            )

        def _is_sentence_bullet(line: str) -> bool:
            outer_match = re.fullmatch(r"- .+ \((.+)\)\.", line)
            if not outer_match:
                return False
            ref_payload = outer_match.group(1)
            ref_parts = [part.strip() for part in ref_payload.split("; ") if part.strip()]
            if not ref_parts:
                return False
            return all(_is_valid_ref_part(part) for part in ref_parts)

        self.assertTrue(
            all(_is_sentence_bullet(line) for line in key_change_bullets),
            msg=f"expected sentence bullets, got: {key_change_bullets}",
        )

    def test_render_markdown_renders_contributor_sections_with_first_time_subset(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "deadbeef1",
                    "title": "fix(security): harden prompt guard defaults",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                }
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[],
            external_contributors=[
                {"display": "@alice", "commits": 3},
                {"display": "@bob", "commits": 1},
                {"display": "Allen Huang", "commits": 1},
            ],
            first_time_contributors=[
                {"display": "@bob", "commits": 1},
            ],
        )

        self.assertIn("## Community Contributors", body)
        self.assertIn("## Special Thanks: First-Time Contributors", body)

        community = body.split("## Community Contributors", 1)[1].split(
            "## Special Thanks: First-Time Contributors",
            1,
        )[0]
        self.assertIn("- [@alice](https://github.com/alice) contributed 3 commits in this release.", community)
        self.assertIn("- [@bob](https://github.com/bob) contributed 1 commit in this release.", community)
        self.assertIn("- Allen Huang contributed 1 commit in this release.", community)

        special_thanks = body.split("## Special Thanks: First-Time Contributors", 1)[1].split(
            "## ",
            1,
        )[0]
        self.assertIn(
            "- [@bob](https://github.com/bob) made their first contribution in this release (1 commit).",
            special_thanks,
        )
        self.assertNotIn("@alice made their first contribution", special_thanks)

    def test_render_markdown_contributor_sections_show_fallbacks_when_empty(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "deadbeef1",
                    "title": "fix(security): harden prompt guard defaults",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                }
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[],
            external_contributors=[],
            first_time_contributors=[],
        )

        community = body.split("## Community Contributors", 1)[1].split(
            "## Special Thanks: First-Time Contributors",
            1,
        )[0]
        special_thanks = body.split("## Special Thanks: First-Time Contributors", 1)[1]

        self.assertIn("- No external contributors were detected in this range after core-team filtering.", community)
        self.assertIn("- No first-time contributors were identified in this range.", special_thanks)

    def test_order_main_report_areas_prefers_readability_sequence_then_other_size(self) -> None:
        module = load_release_report_module()

        def build_changes(prefix: str, count: int) -> list[dict[str, object]]:
            return [
                {
                    "sha": f"{prefix}-{i}",
                    "title": f"fix({prefix}): change {i}",
                    "type": "fix",
                    "security": False,
                    "breaking": False,
                }
                for i in range(count)
            ]

        grouped_changes = {
            "Other: Docs": build_changes("other-docs", 2),
            "Provider/Model stack": build_changes("provider", 6),
            "CI/Release": build_changes("ci", 5),
            "Platform & Maintenance": build_changes("platform", 4),
            "Tools & Agent behavior": build_changes("tools", 3),
            "Memory & Scheduling": build_changes("memory", 2),
            "Channels & UX": build_changes("channels", 1),
            "Security": build_changes("security", 1),
            "Other: Integrations": build_changes("other-integrations", 4),
        }

        self.assertEqual(
            module.order_main_report_sections(grouped_changes),
            [
                "Security",
                "Channels & UX",
                "Provider/Model stack",
                "Tools & Agent behavior",
                "Memory & Scheduling",
                "CI/Release",
                "Platform & Maintenance",
                "Other: Integrations",
                "Other: Docs",
            ],
        )

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=[],
        )
        rendered_order = re.findall(r"^## (.+)$", body, re.MULTILINE)
        self.assertEqual(
            rendered_order,
            [
                "Security",
                "Channels & UX",
                "Provider/Model stack",
                "Tools & Agent behavior",
                "Memory & Scheduling",
                "CI/Release",
                "Platform & Maintenance",
                "Other: Integrations",
                "Other: Docs",
                "Community Contributors",
                "Special Thanks: First-Time Contributors",
            ],
        )

    def test_render_markdown_omits_top_level_title_and_template_highlight_bullets(self) -> None:
        module = load_release_report_module()

        grouped_changes = {
            "Security": [
                {
                    "sha": "deadbeef0",
                    "title": "feat(security): harden prompt guard defaults",
                    "type": "security",
                    "security": True,
                    "breaking": False,
                }
            ]
        }

        body = module.render_markdown(
            version="v1.0.0..v2.0.0",
            generated_at="2026-03-02",
            grouped_changes=grouped_changes,
            highlights=grouped_changes["Security"],
        )

        non_empty_lines = [line for line in body.splitlines() if line.strip()]
        self.assertTrue(non_empty_lines)
        self.assertIn("Release range: `v1.0.0..v2.0.0`.", non_empty_lines[0])
        self.assertIn("Generated: `2026-03-02`.", non_empty_lines[0])
        self.assertNotRegex(body, re.compile(r"^#\s+ZeroClaw", re.MULTILINE))
        self.assertNotIn("## Highlights", body)
        self.assertNotIn("## Narrative Overview", body)
        self.assertNotIn("## Impact & Risk Snapshot", body)
        self.assertNotIn("## Key Changes by Area", body)
        self.assertNotIn("## Full Changelog", body)

        security = body.split("## Security", 1)[1].split("## Community Contributors", 1)[0]
        security_bullets = [line.strip() for line in security.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(len(security_bullets), 1)
        self.assertNotIn("**Scope:**", security_bullets[0])
        self.assertNotIn("|", security_bullets[0])

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

    def test_generated_markdown_contains_required_sections(self) -> None:
        repo = init_temp_git_repo(self.tmp / "markdown-structure")
        make_commit(repo, "feat(security): harden prompt guard", "security.txt")

        out_path = repo / "artifacts" / "report.md"
        proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~1",
                "--to",
                "HEAD",
                "--out",
                str(out_path),
            ],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        body = out_path.read_text(encoding="utf-8")
        self.assertNotRegex(body, re.compile(r"^#\s+ZeroClaw", re.MULTILINE))
        non_empty_lines = [line for line in body.splitlines() if line.strip()]
        self.assertTrue(non_empty_lines)
        self.assertRegex(
            non_empty_lines[0],
            r"^Release range: `HEAD~1\.\.HEAD`\..* Generated: `\d{4}-\d{2}-\d{2}`\.$",
        )
        self.assertIn("## Security", body)
        self.assertNotIn("## Summary", body)
        self.assertIn("This release brings", body)
        self.assertIn("## Community Contributors", body)
        self.assertIn("## Special Thanks: First-Time Contributors", body)
        self.assertNotIn("## Community Thanks", body)
        self.assertNotIn("## Highlights", body)
        self.assertNotIn("## Narrative Overview", body)
        self.assertNotIn("## Impact & Risk Snapshot", body)
        self.assertNotIn("## Key Changes by Area", body)
        self.assertNotIn("## Full Changelog", body)
        self.assertNotIn("**Scope:**", body)

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
        self.assertIn("first_time_contributors", payload)
        self.assertIn("earliest_external_commit_by_identity", payload)
        self.assertEqual(payload["first_time_contributors"], [])
        self.assertGreaterEqual(len(payload["earliest_external_commit_by_identity"]), 1)

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

    def test_main_includes_first_time_contributors_in_report_and_sources_json(self) -> None:
        module = load_release_report_module()
        repo = init_temp_git_repo(self.tmp / "first-time-integration")
        alice_old_sha = make_commit(
            repo,
            "docs: contributor setup notes",
            "alice-old.txt",
            author_name="Alice",
            author_email="101+alice@users.noreply.github.com",
        )
        bob_first_sha = make_commit(
            repo,
            "fix(channel): improve first contribution handling",
            "bob-first.txt",
            author_name="Bob",
            author_email="202+bob@users.noreply.github.com",
        )
        make_commit(
            repo,
            "fix(provider): improve retry handling",
            "alice-new.txt",
            author_name="Alice",
            author_email="101+alice@users.noreply.github.com",
        )

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

        body = out_path.read_text(encoding="utf-8")
        self.assertIn("## Community Contributors", body)
        self.assertIn("## Special Thanks: First-Time Contributors", body)
        self.assertNotIn("## Community Thanks", body)

        special_thanks = body.split("## Special Thanks: First-Time Contributors", 1)[1]
        self.assertIn(
            "- [@bob](https://github.com/bob) made their first contribution in this release (1 commit).",
            special_thanks,
        )
        self.assertNotIn("@alice made their first contribution", special_thanks)

        payload = json.loads(sources_path.read_text(encoding="utf-8"))
        first_time = payload.get("first_time_contributors", [])
        self.assertEqual(first_time, [{"commits": 1, "display": "@bob"}])

        earliest_by_identity = payload.get("earliest_external_commit_by_identity", {})
        alice_key = module.build_contributor_identity_key("Alice", "101+alice@users.noreply.github.com")
        bob_key = module.build_contributor_identity_key("Bob", "202+bob@users.noreply.github.com")
        self.assertEqual(earliest_by_identity[alice_key], alice_old_sha)
        self.assertEqual(earliest_by_identity[bob_key], bob_first_sha)

    def test_override_json_forces_must_include_title_into_highlights_payload(self) -> None:
        repo = init_temp_git_repo(self.tmp / "override-highlights")
        must_include_title = "style: update maintainer onboarding wording"
        make_commit(repo, must_include_title, "misc.txt")
        make_commit(repo, "chore(ci): tighten release pipeline checks", "ci.txt")
        make_commit(repo, "feat(tool): improve web_fetch timeout behavior", "tools.txt")
        make_commit(repo, "fix(memory): qdrant scheduler drift handling", "memory.txt")
        make_commit(repo, "fix(channel): stabilize telegram conversation routing", "channel.txt")
        make_commit(repo, "fix(provider): tune openai retry behavior", "provider.txt")
        make_commit(repo, "feat(security): harden prompt guard defaults", "security.txt")

        baseline_out = repo / "artifacts" / "report.baseline.md"
        baseline_sources = repo / "artifacts" / "report.baseline.sources.json"
        baseline_proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~7",
                "--to",
                "HEAD",
                "--out",
                str(baseline_out),
                "--sources-json",
                str(baseline_sources),
            ],
            cwd=repo,
        )
        self.assertEqual(baseline_proc.returncode, 0, msg=baseline_proc.stderr)
        baseline_payload = json.loads(baseline_sources.read_text(encoding="utf-8"))
        baseline_highlight_titles = [str(item.get("title", "")) for item in baseline_payload.get("highlights", [])]
        self.assertNotIn(must_include_title, baseline_highlight_titles)

        override_path = repo / "override.json"
        override_path.write_text(
            json.dumps({"must_include": [must_include_title]}),
            encoding="utf-8",
        )

        override_out = repo / "artifacts" / "report.override.md"
        override_sources = repo / "artifacts" / "report.override.sources.json"
        override_proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~7",
                "--to",
                "HEAD",
                "--out",
                str(override_out),
                "--sources-json",
                str(override_sources),
                "--override-json",
                str(override_path),
            ],
            cwd=repo,
        )
        self.assertEqual(override_proc.returncode, 0, msg=override_proc.stderr)
        override_body = override_out.read_text(encoding="utf-8")
        self.assertNotIn("## Highlights", override_body)

        payload = json.loads(override_sources.read_text(encoding="utf-8"))
        highlight_titles = [str(item.get("title", "")) for item in payload.get("highlights", [])]
        self.assertIn(must_include_title, highlight_titles)
        self.assertIn("changes", payload)
        self.assertIn("grouped", payload)

    def test_override_json_must_promote_forces_topic_into_main_area_bullets(self) -> None:
        repo = init_temp_git_repo(self.tmp / "override-must-promote")
        make_commit(repo, "feat(provider): alpha routing improvements", "p1.txt")
        make_commit(repo, "feat(provider): beta fallback tuning", "p2.txt")
        make_commit(repo, "feat(provider): gamma retry controls", "p3.txt")
        make_commit(repo, "feat(provider): delta health checks", "p4.txt")
        make_commit(repo, "feat(provider): epsilon catalog refresh", "p5.txt")
        make_commit(repo, "feat(provider): zeta onboarding flow", "p6.txt")
        make_commit(repo, "docs(provider): add nebula support guide", "p7.txt")

        baseline_out = repo / "artifacts" / "report.baseline.md"
        baseline_proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~7",
                "--to",
                "HEAD",
                "--out",
                str(baseline_out),
            ],
            cwd=repo,
        )
        self.assertEqual(baseline_proc.returncode, 0, msg=baseline_proc.stderr)
        baseline_body = baseline_out.read_text(encoding="utf-8")
        provider_baseline = baseline_body.split("## Provider/Model stack", 1)[1].split(
            "## ",
            1,
        )[0]
        baseline_bullets = [
            line.strip()
            for line in provider_baseline.splitlines()
            if line.strip().startswith("- ")
        ]
        baseline_nebula_index = next(
            (idx for idx, line in enumerate(baseline_bullets) if "Add nebula support guide" in line),
            -1,
        )
        self.assertGreaterEqual(baseline_nebula_index, 0)

        override_path = repo / "override.must-promote.json"
        override_path.write_text(
            json.dumps({"must_promote": ["nebula"]}),
            encoding="utf-8",
        )

        override_out = repo / "artifacts" / "report.override.md"
        override_proc = run_cmd(
            [
                "python3",
                self._script(),
                "--from",
                "HEAD~7",
                "--to",
                "HEAD",
                "--out",
                str(override_out),
                "--override-json",
                str(override_path),
            ],
            cwd=repo,
        )
        self.assertEqual(override_proc.returncode, 0, msg=override_proc.stderr)
        override_body = override_out.read_text(encoding="utf-8")
        provider_override = override_body.split("## Provider/Model stack", 1)[1].split(
            "## ",
            1,
        )[0]
        override_bullets = [
            line.strip()
            for line in provider_override.splitlines()
            if line.strip().startswith("- ")
        ]
        override_nebula_index = next(
            (idx for idx, line in enumerate(override_bullets) if "Add nebula support guide" in line),
            -1,
        )
        self.assertGreaterEqual(override_nebula_index, 0)
        self.assertLess(
            override_nebula_index,
            baseline_nebula_index,
            msg=(
                "must_promote should elevate matched bullets earlier in the section; "
                f"baseline index={baseline_nebula_index}, override index={override_nebula_index}"
            ),
        )

    def test_writes_appendix_and_twitter_outputs_when_requested(self) -> None:
        repo = init_temp_git_repo(self.tmp / "multi-output")
        make_commit(repo, "feat(security): harden prompt guard defaults", "security.txt")
        make_commit(repo, "feat(channel): improve telegram approvals", "channel.txt")
        make_commit(repo, "fix(ci): stabilize release workflow", "ci.txt")

        out_path = repo / "artifacts" / "report.md"
        appendix_path = repo / "artifacts" / "report.appendix.md"
        twitter_path = repo / "artifacts" / "report.twitter.md"
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
                "--appendix-out",
                str(appendix_path),
                "--twitter-out",
                str(twitter_path),
                "--release-version",
                "0.2.0",
            ],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(appendix_path.exists())
        self.assertTrue(twitter_path.exists())

        appendix_body = appendix_path.read_text(encoding="utf-8")
        self.assertIn("# ZeroClaw v0.2.0 Release Source Appendix", appendix_body)
        self.assertIn("## Source-Mapped Changelog", appendix_body)

        twitter_body = twitter_path.read_text(encoding="utf-8")
        self.assertIn("# ZeroClaw v0.2.0 Twitter Draft", twitter_body)
        self.assertIn("## Single Post", twitter_body)
        self.assertIn("## Thread Option", twitter_body)

    def test_writes_workflow_stage_artifacts_when_requested(self) -> None:
        repo = init_temp_git_repo(self.tmp / "workflow-stages")
        make_commit(repo, "feat(channel): recover telegram polling", "channel.txt")
        make_commit(repo, "feat(security): add role policy and OTP challenge foundation", "security_a.txt")
        make_commit(repo, "feat(security): add role-policy and otp challenge foundations", "security_b.txt")

        out_path = repo / "artifacts" / "report.md"
        stage_dir = repo / "artifacts" / "workflow-stages"
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
                "--stage-dir",
                str(stage_dir),
            ],
            cwd=repo,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        pre_compound_path = stage_dir / "01-pre-compound.json"
        pre_synthesis_path = stage_dir / "02-pre-synthesis.json"
        self.assertTrue(pre_compound_path.exists())
        self.assertTrue(pre_synthesis_path.exists())

        pre_compound = json.loads(pre_compound_path.read_text(encoding="utf-8"))
        self.assertEqual(pre_compound.get("stage"), "pre-compound")
        pre_compound_sections = {str(item.get("section", "")): item for item in pre_compound.get("sections", [])}
        self.assertIn("Security", pre_compound_sections)
        security_selected = pre_compound_sections["Security"].get("selected_changes", [])
        self.assertGreaterEqual(len(security_selected), 2)

        pre_synthesis = json.loads(pre_synthesis_path.read_text(encoding="utf-8"))
        self.assertEqual(pre_synthesis.get("stage"), "pre-synthesis")
        pre_synthesis_sections = {str(item.get("section", "")): item for item in pre_synthesis.get("sections", [])}
        self.assertIn("Security", pre_synthesis_sections)
        security_groups = pre_synthesis_sections["Security"].get("compound_groups", [])
        self.assertTrue(any(len(group) >= 2 for group in security_groups))
        security_bullets = pre_synthesis_sections["Security"].get("compound_bullets", [])
        self.assertTrue(any("role policy" in str(line).lower() for line in security_bullets))

    def test_external_contributor_filter_excludes_core_team_aliases(self) -> None:
        module = load_release_report_module()

        changes = [
            {
                "author_name": "xj",
                "author_email": "gh-xj@users.noreply.github.com",
            },
            {
                "author_name": "Argenis",
                "author_email": "theonlyhennygod@gmail.com",
            },
            {
                "author_name": "Chummy",
                "author_email": "chumyin0912@gmail.com",
            },
            {
                "author_name": "Vernon Stinebaker",
                "author_email": "vernonstinebaker@users.noreply.github.com",
            },
        ]

        contributors = module.build_external_contributors(
            changes,
            core_devs=["theonlyhennygod", "chumyin", "gh-xj"],
        )
        displays = [item["display"] for item in contributors]
        self.assertEqual(displays, ["@vernonstinebaker"])

    def test_format_author_ref_uses_core_alias_handle_for_clickable_link(self) -> None:
        module = load_release_report_module()
        rendered = module.format_author_ref(
            {
                "author_name": "argenis de la rosa",
                "author_email": "theonlyhennygod@gmail.com",
            }
        )
        self.assertEqual(rendered, "[@theonlyhennygod](https://github.com/theonlyhennygod)")

    def test_format_author_ref_falls_back_to_pr_author_login(self) -> None:
        module = load_release_report_module()
        rendered = module.format_author_ref(
            {
                "author_name": "Argenis De La Rosa",
                "author_email": "argenis@example.com",
                "pr_author_login": "theonlyhennygod",
            }
        )
        self.assertEqual(rendered, "[@theonlyhennygod](https://github.com/theonlyhennygod)")

    def test_enrich_changes_with_associated_prs_uses_cache_between_runs(self) -> None:
        module = load_release_report_module()
        cache_path = self.tmp / "pr-cache.json"
        changes = [
            {
                "sha": "sha-111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "title": "feat(security): add role-policy and otp challenge foundations",
            },
            {
                "sha": "sha-111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "title": "feat(security): add role-policy and otp challenge foundations",
            },
        ]

        call_count = 0

        def gh_api_success(*_, **__) -> object:
            nonlocal call_count
            call_count += 1
            return SimpleNamespace(
                returncode=0,
                stdout='[{"number": 2202, "user": {"login": "alice"}}]',
                stderr="",
            )

        with patch.object(module.subprocess, "run", side_effect=gh_api_success):
            module.enrich_changes_with_associated_prs(
                changes,
                repo_slug="zeroclaw-labs/zeroclaw",
                cache_path=cache_path,
            )

        self.assertEqual(call_count, 1)
        self.assertEqual(changes[0]["pr_number"], 2202)
        self.assertEqual(changes[1]["pr_number"], 2202)
        self.assertEqual(changes[0]["pr_author_login"], "alice")
        self.assertTrue(cache_path.exists())

        second_changes = [
            {
                "sha": "sha-111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
            },
        ]

        with patch.object(module.subprocess, "run", side_effect=RuntimeError("should not call gh")):
            module.enrich_changes_with_associated_prs(
                second_changes,
                repo_slug="zeroclaw-labs/zeroclaw",
                cache_path=cache_path,
            )

        self.assertEqual(call_count, 1)
        self.assertEqual(second_changes[0]["pr_number"], 2202)

    def test_compute_earliest_commit_by_identity_ignores_core_aliases_and_bots(self) -> None:
        module = load_release_report_module()

        history_changes = [
            {
                "sha": "core-old",
                "author_name": "Chummy",
                "author_email": "chumyin0912@gmail.com",
            },
            {
                "sha": "bot-old",
                "author_name": "dependabot[bot]",
                "author_email": "49699333+dependabot[bot]@users.noreply.github.com",
            },
            {
                "sha": "alice-old",
                "author_name": "Alice",
                "author_email": "101+alice@users.noreply.github.com",
            },
            {
                "sha": "alice-new",
                "author_name": "Alice",
                "author_email": "101+alice@users.noreply.github.com",
            },
            {
                "sha": "bob-old",
                "author_name": "Bob",
                "author_email": "202+bob@users.noreply.github.com",
            },
        ]

        earliest_by_identity = module.compute_earliest_commit_by_identity(
            history_changes,
            core_devs=["theonlyhennygod", "chumyin", "gh-xj"],
        )

        alice_key = module.build_contributor_identity_key("Alice", "101+alice@users.noreply.github.com")
        bob_key = module.build_contributor_identity_key("Bob", "202+bob@users.noreply.github.com")
        core_key = module.build_contributor_identity_key("Chummy", "chumyin0912@gmail.com")
        bot_key = module.build_contributor_identity_key(
            "dependabot[bot]",
            "49699333+dependabot[bot]@users.noreply.github.com",
        )

        self.assertEqual(earliest_by_identity[alice_key], "alice-old")
        self.assertEqual(earliest_by_identity[bob_key], "bob-old")
        self.assertNotIn(core_key, earliest_by_identity)
        self.assertNotIn(bot_key, earliest_by_identity)

    def test_select_first_time_contributors_includes_only_external_first_commits_in_range(self) -> None:
        module = load_release_report_module()

        current_changes = [
            {
                "sha": "alice-new",
                "author_name": "Alice",
                "author_email": "101+alice@users.noreply.github.com",
            },
            {
                "sha": "bob-old",
                "author_name": "Bob",
                "author_email": "202+bob@users.noreply.github.com",
            },
            {
                "sha": "core-new",
                "author_name": "xj",
                "author_email": "gh-xj@users.noreply.github.com",
            },
            {
                "sha": "bot-new",
                "author_name": "github-actions[bot]",
                "author_email": "41898282+github-actions[bot]@users.noreply.github.com",
            },
        ]
        earliest_by_identity = {
            module.build_contributor_identity_key("Alice", "101+alice@users.noreply.github.com"): "alice-old",
            module.build_contributor_identity_key("Bob", "202+bob@users.noreply.github.com"): "bob-old",
            module.build_contributor_identity_key("xj", "gh-xj@users.noreply.github.com"): "core-new",
            module.build_contributor_identity_key(
                "github-actions[bot]",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ): "bot-new",
        }

        first_time = module.select_first_time_contributors(
            current_changes,
            earliest_by_identity=earliest_by_identity,
            core_devs=["theonlyhennygod", "chumyin", "gh-xj"],
        )

        displays = [item["display"] for item in first_time]
        self.assertEqual(displays, ["@bob"])

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
