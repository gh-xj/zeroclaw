# GitHub Release Report Readability Refinement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor release report generation so the main GitHub note is intro + area sections with simple commit-derived bullets, and add a first-time-contributor special-thanks section that excludes core dev identities.

**Architecture:** Keep data collection and appendix/source outputs intact, but replace main-note rendering with a readability-first structure. Add deterministic section ordering and sentence-cleanup helpers for bullet extraction from commit subjects. Add a first-time contributor detector based on normalized identity and earliest repository commit, then render that list in a dedicated section.

**Tech Stack:** Python 3 CLI (`argparse`, `subprocess`, `re`, `json`), git log plumbing, `unittest` test suite under `scripts/ci/tests`.

---

Reference skills: `@test-driven-development`, `@verification-before-completion`.

### Task 1: Lock New Main-Report Shape with Failing Tests

**Files:**
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `scripts/release/generate_release_report.py`

**Step 1: Write the failing test**

Add a new test asserting the main report:
- has no top report title header line like `# ZeroClaw ...`
- starts with an intro paragraph after metadata lines
- uses `## <Area>` sections followed by simple one-sentence bullets
- does not include verbose template bullets like `**Scope:**`.

```python
def test_main_report_uses_intro_plus_simple_area_bullets(self) -> None:
    body = module.render_markdown(...)
    self.assertNotIn("# ZeroClaw", body)
    self.assertIn("## Security", body)
    self.assertNotIn("**Scope:**", body)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.ci.tests.test_release_report_generator.ReleaseReportGeneratorTest.test_main_report_uses_intro_plus_simple_area_bullets -v`
Expected: FAIL (current renderer still contains title/templated bullets).

**Step 3: Write minimal implementation**

In `render_markdown`, remove title block and switch per-area bullets to simple sentence output.

```python
# remove title/header lines
lines.append(intro_paragraph)
...
lines.append(f"- {simple_sentence}")
```

**Step 4: Run test to verify it passes**

Run: same command as Step 2
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/ci/tests/test_release_report_generator.py scripts/release/generate_release_report.py
git commit -m "test(release): lock intro-plus-area report structure"
```

### Task 2: Add Commit-Subject Sentence Cleanup Rules (TDD)

**Files:**
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `scripts/release/generate_release_report.py`

**Step 1: Write the failing test**

Add tests for a helper that converts commit subjects into simple sentence bullets.

```python
def test_clean_commit_sentence_strips_prefixes_and_keeps_terms(self) -> None:
    self.assertEqual(
        module.clean_commit_sentence("feat(security): add context-aware command allow rules"),
        "Add context-aware command allow rules."
    )
```

Include cases for:
- conventional commit prefix removal
- PR suffix removal (`(#1234)`)
- preserving technical terms (`OAuth`, `CI`, `Qdrant`)

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.ci.tests.test_release_report_generator.ReleaseReportGeneratorTest.test_clean_commit_sentence_strips_prefixes_and_keeps_terms -v`
Expected: FAIL (`clean_commit_sentence` not implemented).

**Step 3: Write minimal implementation**

Implement `clean_commit_sentence(title: str) -> str` and use it in area bullet rendering.

```python
def clean_commit_sentence(title: str) -> str:
    # strip conventional prefix + PR suffix
    # normalize spacing
    # ensure trailing period
```

**Step 4: Run test to verify it passes**

Run: same command as Step 2
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/ci/tests/test_release_report_generator.py scripts/release/generate_release_report.py
git commit -m "feat(release): render simple sentence bullets from commit subjects"
```

### Task 3: Deterministic Area Ordering + Section Rendering

**Files:**
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `scripts/release/generate_release_report.py`

**Step 1: Write the failing test**

Add a test asserting area order:
- Security, Channels & UX, Provider/Model stack, Tools & Agent behavior, Memory & Scheduling, CI/Release, Platform & Maintenance, then `Other:*`.

```python
def test_main_report_area_order_is_readability_first(self) -> None:
    body = module.render_markdown(...)
    self.assertLess(body.index("## Security"), body.index("## Channels & UX"))
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.ci.tests.test_release_report_generator.ReleaseReportGeneratorTest.test_main_report_area_order_is_readability_first -v`
Expected: FAIL (current order is impact-ranked).

**Step 3: Write minimal implementation**

Introduce a section ordering helper and apply it only to main report rendering.

```python
def ordered_main_sections(grouped_changes):
    # fixed preferred order + append sorted Other:*
```

**Step 4: Run test to verify it passes**

Run: same command as Step 2
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/ci/tests/test_release_report_generator.py scripts/release/generate_release_report.py
git commit -m "feat(release): enforce readability-first area ordering"
```

### Task 4: First-Time Contributor Detection (TDD)

**Files:**
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `scripts/release/generate_release_report.py`

**Step 1: Write the failing test**

Add tests for first-time contributor identification:
- contributor appears in current range and earliest repo commit is inside range -> included
- contributor appears in current range but has earlier historical commit -> excluded
- core dev aliases excluded.

```python
def test_first_time_contributors_excludes_core_and_prior_contributors(self) -> None:
    first_timers = module.select_first_time_contributors(...)
    self.assertEqual(first_timers, ["@newperson"])
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.ci.tests.test_release_report_generator.ReleaseReportGeneratorTest.test_first_time_contributors_excludes_core_and_prior_contributors -v`
Expected: FAIL (helper missing).

**Step 3: Write minimal implementation**

Add helpers:
- identity-key normalization reuse
- earliest commit map for identities (`git log --all --reverse` parse)
- first-time selector for current range contributors.

```python
def select_first_time_contributors(changes, earliest_by_identity, core_devs):
    ...
```

**Step 4: Run test to verify it passes**

Run: same command as Step 2
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/ci/tests/test_release_report_generator.py scripts/release/generate_release_report.py
git commit -m "feat(release): add first-time contributor special thanks detection"
```

### Task 5: Render Community Sections in Main Report

**Files:**
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `scripts/release/generate_release_report.py`

**Step 1: Write the failing test**

Add test asserting both end sections exist:
- `## Community Contributors`
- `## Special Thanks: First-Time Contributors`

And ensure special-thanks list only contains first-time contributors.

```python
def test_report_renders_first_time_special_thanks_section(self) -> None:
    body = module.render_markdown(...)
    self.assertIn("## Special Thanks: First-Time Contributors", body)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest scripts.ci.tests.test_release_report_generator.ReleaseReportGeneratorTest.test_report_renders_first_time_special_thanks_section -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Update `render_markdown` to render two contributor sections with simple bullets.

```python
lines.append("## Community Contributors")
...
lines.append("## Special Thanks: First-Time Contributors")
```

**Step 4: Run test to verify it passes**

Run: same command as Step 2
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/ci/tests/test_release_report_generator.py scripts/release/generate_release_report.py
git commit -m "feat(release): split contributor thanks and first-time special thanks"
```

### Task 6: CLI Plumbing + Output Validation

**Files:**
- Modify: `scripts/release/generate_release_report.py`
- Modify: `scripts/ci/tests/test_release_report_generator.py`
- Modify: `docs/release-process.md`

**Step 1: Write failing integration test updates**

Adjust integration tests to assert new body shape and contributor sections on full CLI runs.

**Step 2: Run tests to verify failures**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: FAIL on outdated assertions.

**Step 3: Implement minimal CLI/data wiring**

Ensure first-time detection executes during CLI flow and is passed to renderer and sources payload.

```python
earliest = collect_earliest_commits_by_identity()
first_timers = select_first_time_contributors(...)
```

**Step 4: Run full test suite**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/release/generate_release_report.py scripts/ci/tests/test_release_report_generator.py docs/release-process.md
git commit -m "feat(release): finalize readable github report format and first-time thanks"
```

### Task 7: Regenerate Artifacts + Final Verification

**Files:**
- Generate: `artifacts/v0.2.0-release-report.md`
- Generate: `artifacts/v0.2.0-release-report.appendix.md`
- Generate: `artifacts/v0.2.0-release-report.sources.json`

**Step 1: Regenerate release artifacts**

Run:
```bash
python3 scripts/release/generate_release_report.py \
  --from v0.1.7 \
  --to main \
  --out artifacts/v0.2.0-release-report.md \
  --appendix-out artifacts/v0.2.0-release-report.appendix.md \
  --release-version 0.2.0 \
  --core-dev theonlyhennygod \
  --core-dev chumyin \
  --core-dev gh-xj \
  --sources-json artifacts/v0.2.0-release-report.sources.json
```

Expected: command exits 0 and all 3 files are updated.

**Step 2: Validate output shape quickly**

Run:
```bash
sed -n '1,220p' artifacts/v0.2.0-release-report.md
```

Expected checks:
- no top report header
- intro paragraph present
- area sections + simple bullets
- `Community Contributors` and `Special Thanks: First-Time Contributors` present

**Step 3: Run full verification**

Run: `python3 -m unittest scripts/ci/tests/test_release_report_generator.py -v`
Expected: PASS

**Step 4: Commit final artifacts/docs if intended for branch**

```bash
git add artifacts/v0.2.0-release-report.md artifacts/v0.2.0-release-report.appendix.md artifacts/v0.2.0-release-report.sources.json
git commit -m "docs(release): regenerate v0.2.0 readable github release report"
```
