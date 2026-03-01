# ZeroClaw v2.0 Release Report Generator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a markdown report generator that produces OpenClaw-style release messaging (short highlights + long thematic breakdown + source appendix) from a git range.

**Architecture:** Implement a standalone Python CLI under `scripts/release/` that collects and normalizes git changes, groups them using rule-based taxonomy, ranks critical items, and renders deterministic markdown output plus optional source-map JSON. Keep phase-1 dependency-free (stdlib only), with optional override input for human tuning.

**Tech Stack:** Python 3 (argparse/json/subprocess/pathlib/re), git CLI, unittest (`scripts/ci/tests`), markdown output in `artifacts/`.

---

Related implementation skills to apply during execution: `@test-driven-development`, `@systematic-debugging`, `@verification-before-completion`, `@requesting-code-review`.

### Task 1: Scaffold CLI and validate input range

**Files:**
- Create: `scripts/release/generate_release_report.py`
- Test: `scripts/ci/tests/test_release_report_generator.py`

**Step 1: Write the failing test**

```python
class ReleaseReportGeneratorTest(unittest.TestCase):
    def test_missing_from_tag_returns_exit_2(self) -> None:
        proc = run_cmd(
            [
                "python3",
                str(RELEASE_SCRIPTS_DIR / "generate_release_report.py"),
                "--from",
                "v9.9.9",
                "--to",
                "HEAD",
                "--out",
                str(self.tmp / "out.md"),
            ],
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("from tag not found", proc.stderr.lower())
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because `generate_release_report.py` does not exist yet.

**Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path


def git_ref_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release report markdown")
    parser.add_argument("--from", dest="from_ref", required=True)
    parser.add_argument("--to", dest="to_ref", default="HEAD")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if not git_ref_exists(args.from_ref):
        print(f"from tag not found: {args.from_ref}", file=sys.stderr)
        return 2

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("# placeholder\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS for `test_missing_from_tag_returns_exit_2`.

**Step 5: Commit**

```bash
git add scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py
git commit -m "test(release): scaffold report generator input validation"
```

### Task 2: Collect and normalize change records from git

**Files:**
- Modify: `scripts/release/generate_release_report.py`
- Modify: `scripts/ci/tests/test_release_report_generator.py`

**Step 1: Write the failing test**

```python
def test_collect_changes_from_tag_range(self) -> None:
    repo = init_temp_git_repo(self.tmp / "repo")
    make_commit(repo, "feat(security): add promptguard hardening (#1001)")
    make_commit(repo, "fix(channel): improve telegram polling recovery (#1002)")

    proc = run_cmd(
        [
            "python3",
            str(RELEASE_SCRIPTS_DIR / "generate_release_report.py"),
            "--from",
            "HEAD~1",
            "--to",
            "HEAD",
            "--out",
            str(repo / "artifacts" / "report.md"),
            "--sources-json",
            str(repo / "artifacts" / "report.sources.json"),
        ],
        cwd=repo,
    )
    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
    data = json.loads((repo / "artifacts" / "report.sources.json").read_text(encoding="utf-8"))
    self.assertGreaterEqual(len(data["changes"]), 1)
    self.assertIn("type", data["changes"][0])
    self.assertIn("title", data["changes"][0])
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because `--sources-json` and normalization are not implemented.

**Step 3: Write minimal implementation**

```python
import json
import re

TYPE_PATTERNS = [
    ("security", re.compile(r"\bsecurity\b|prompt injection|leak", re.IGNORECASE)),
    ("feat", re.compile(r"^feat\b", re.IGNORECASE)),
    ("fix", re.compile(r"^fix\b", re.IGNORECASE)),
    ("ci", re.compile(r"^ci\b|^chore\(ci\)", re.IGNORECASE)),
]


def infer_type(title: str) -> str:
    for kind, pattern in TYPE_PATTERNS:
        if pattern.search(title):
            return kind
    return "misc"


def collect_changes(from_ref: str, to_ref: str) -> list[dict[str, object]]:
    fmt = "%H%x1f%s%x1e"
    proc = subprocess.run(
        ["git", "log", "--no-merges", f"{from_ref}..{to_ref}", f"--pretty=format:{fmt}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())

    rows: list[dict[str, object]] = []
    for rec in [r for r in proc.stdout.split("\x1e") if r.strip()]:
        sha, title = rec.strip().split("\x1f", 1)
        rows.append(
            {
                "sha": sha,
                "title": title,
                "type": infer_type(title),
                "security": infer_type(title) == "security",
                "breaking": "breaking" in title.lower(),
            }
        )
    return rows
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS for normalization + source JSON emission assertions.

**Step 5: Commit**

```bash
git add scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py
git commit -m "feat(release): collect and normalize report changes from git range"
```

### Task 3: Add taxonomy rules and thematic grouping

**Files:**
- Create: `scripts/release/report_taxonomy.json`
- Modify: `scripts/release/generate_release_report.py`
- Modify: `scripts/ci/tests/test_release_report_generator.py`

**Step 1: Write the failing test**

```python
def test_groups_security_and_channels_with_priority(self) -> None:
    changes = [
        {"title": "feat(security): prompt injection defense", "type": "security", "security": True, "breaking": False},
        {"title": "fix(telegram): stabilize topic routing", "type": "fix", "security": False, "breaking": False},
    ]
    grouped = group_changes(changes, load_taxonomy(DEFAULT_TAXONOMY_PATH))
    self.assertIn("Security", grouped)
    self.assertIn("Channels & UX", grouped)
    self.assertEqual(grouped["Security"][0]["title"], "feat(security): prompt injection defense")
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because taxonomy loader/grouping do not exist.

**Step 3: Write minimal implementation**

```json
{
  "sections": [
    {"name": "Security", "keywords": ["security", "prompt", "leak", "guard", "hardening"]},
    {"name": "Provider/Model stack", "keywords": ["provider", "model", "openai", "gemini", "novita", "bedrock"]},
    {"name": "Channels & UX", "keywords": ["telegram", "whatsapp", "wati", "lark", "feishu", "channel", "conversation"]},
    {"name": "Memory & Scheduling", "keywords": ["memory", "qdrant", "cron", "scheduler"]},
    {"name": "Tools & Agent behavior", "keywords": ["tool", "agent", "web_fetch", "browser_open", "time"]},
    {"name": "CI/Release", "keywords": ["ci", "release", "android", "artifact", "pipeline"]}
  ],
  "fallback_section": "Misc"
}
```

```python
def group_changes(changes: list[dict[str, object]], taxonomy: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for section in taxonomy["sections"]:
        grouped[section["name"]] = []
    grouped[taxonomy["fallback_section"]] = []

    for change in changes:
        title = str(change["title"]).lower()
        assigned = False
        for section in taxonomy["sections"]:
            if any(k.lower() in title for k in section["keywords"]):
                grouped[section["name"]].append(change)
                assigned = True
                break
        if not assigned:
            grouped[taxonomy["fallback_section"]].append(change)

    if "Security" in grouped:
        grouped["Security"].sort(key=lambda c: (not bool(c.get("security")), not bool(c.get("breaking"))))
    return {k: v for k, v in grouped.items() if v}
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS for section assignment and security-first ordering.

**Step 5: Commit**

```bash
git add scripts/release/report_taxonomy.json scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py
git commit -m "feat(release): add taxonomy-based thematic grouping"
```

### Task 4: Render OpenClaw-style markdown structure

**Files:**
- Modify: `scripts/release/generate_release_report.py`
- Modify: `scripts/ci/tests/test_release_report_generator.py`

**Step 1: Write the failing test**

```python
def test_markdown_contains_required_sections(self) -> None:
    proc = run_cmd(
        [
            "python3",
            str(RELEASE_SCRIPTS_DIR / "generate_release_report.py"),
            "--from",
            "HEAD~1",
            "--to",
            "HEAD",
            "--out",
            str(self.tmp / "report.md"),
        ],
        cwd=ROOT,
    )
    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
    body = (self.tmp / "report.md").read_text(encoding="utf-8")
    self.assertIn("## Highlights", body)
    self.assertIn("## Detailed Breakdown", body)
    self.assertIn("## Source Appendix", body)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because output is still placeholder markdown.

**Step 3: Write minimal implementation**

```python
def render_markdown(*, version: str, generated_at: str, grouped: dict[str, list[dict[str, object]]]) -> str:
    lines: list[str] = []
    lines.append(f"# ZeroClaw {version} Release Report")
    lines.append("")
    lines.append(f"Generated at: `{generated_at}`")
    lines.append("")
    lines.append("## Highlights")
    lines.append("")

    top_items = [item for items in grouped.values() for item in items][:6]
    for item in top_items:
        lines.append(f"- {item['title']}")

    lines.append("")
    lines.append("## Detailed Breakdown")
    lines.append("")

    idx = 1
    for section, items in grouped.items():
        lines.append(f"### {idx}) {section}")
        lines.append("")
        summary = items[0]["title"]
        lines.append(f"This section includes {len(items)} notable updates, led by: {summary}.")
        lines.append("")
        idx += 1

    lines.append("## Source Appendix")
    lines.append("")
    for section, items in grouped.items():
        lines.append(f"### {section}")
        for item in items:
            lines.append(f"- `{item['sha'][:8]}` {item['title']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS and markdown contains all required sections.

**Step 5: Commit**

```bash
git add scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py
git commit -m "feat(release): render structured markdown release report"
```

### Task 5: Add overrides and source-map sidecar for review tuning

**Files:**
- Modify: `scripts/release/generate_release_report.py`
- Modify: `scripts/ci/tests/test_release_report_generator.py`

**Step 1: Write the failing test**

```python
def test_override_injects_must_include_highlight(self) -> None:
    override = self.tmp / "override.json"
    override.write_text(
        json.dumps({"must_include": ["feat(security): prompt injection defense"]}),
        encoding="utf-8",
    )

    out_md = self.tmp / "report.md"
    out_json = self.tmp / "report.sources.json"
    proc = run_cmd(
        [
            "python3",
            str(RELEASE_SCRIPTS_DIR / "generate_release_report.py"),
            "--from",
            "HEAD~5",
            "--to",
            "HEAD",
            "--out",
            str(out_md),
            "--sources-json",
            str(out_json),
            "--override-json",
            str(override),
        ],
        cwd=ROOT,
    )
    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
    self.assertIn("prompt injection defense", out_md.read_text(encoding="utf-8").lower())
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    self.assertIn("grouped", payload)
    self.assertIn("changes", payload)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because override and source-map schema are incomplete.

**Step 3: Write minimal implementation**

```python
def load_override(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def apply_override(highlights: list[dict[str, object]], changes: list[dict[str, object]], override: dict[str, object]) -> list[dict[str, object]]:
    extra = [str(x) for x in override.get("must_include", [])]
    index = {str(c["title"]): c for c in changes}
    for title in extra:
        if title in index and all(h["title"] != title for h in highlights):
            highlights.insert(0, index[title])
    return highlights
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS; override is reflected in markdown and sidecar JSON contains expected keys.

**Step 5: Commit**

```bash
git add scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py
git commit -m "feat(release): support review overrides and source-map sidecar"
```

### Task 6: Final verification and maintainer runbook update

**Files:**
- Modify: `docs/release-process.md`
- Optional modify: `docs/commands-reference.md`

**Step 1: Write the failing test/doc assertion**

```python
def test_release_process_mentions_report_generator_command(self) -> None:
    doc = (ROOT / "docs" / "release-process.md").read_text(encoding="utf-8")
    self.assertIn("generate_release_report.py", doc)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL because runbook does not mention the generator yet.

**Step 3: Write minimal documentation implementation**

```markdown
### Release messaging draft (experimental)

Generate a draft markdown report from release range:

```bash
python3 scripts/release/generate_release_report.py \
  --from v0.1.7 \
  --to HEAD \
  --out artifacts/v2.0-release-report.md \
  --sources-json artifacts/v2.0-release-report.sources.json
```

Use `--override-json` to force must-include highlights during review.
```

**Step 4: Run full verification**

Run: `python3 -m unittest discover -s scripts/ci/tests -p 'test_*.py' -v`
Expected: PASS with new release-report tests included.

Run: `python3 scripts/release/generate_release_report.py --from v0.1.7 --to HEAD --out artifacts/v2.0-release-report.md --sources-json artifacts/v2.0-release-report.sources.json`
Expected: exit code 0 and both output files generated.

**Step 5: Commit**

```bash
git add docs/release-process.md docs/commands-reference.md scripts/release/generate_release_report.py scripts/release/report_taxonomy.json scripts/ci/tests/test_release_report_generator.py
git commit -m "docs(release): add release report generator workflow"
```

## Verification checklist before completion

- `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
- `python3 -m unittest discover -s scripts/ci/tests -p 'test_*.py' -v`
- One real dry run against current range with outputs in `artifacts/`
- Manual review: output contains Highlights, Detailed Breakdown, Source Appendix

## Expected deliverables

- `scripts/release/generate_release_report.py`
- `scripts/release/report_taxonomy.json`
- `scripts/ci/tests/test_release_report_generator.py`
- runbook update documenting command and review loop
- sample output in `artifacts/v2.0-release-report.md`
