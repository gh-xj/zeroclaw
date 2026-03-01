# ZeroClaw v2.0 Release Report Generator Design

Date: 2026-03-01
Owner: release engineering
Status: Approved for implementation

## 1. Goal

Generate a markdown release report for ZeroClaw v2.0 that is stylistically similar to OpenClaw-style announcements:

- a short highlight summary block (social-post style)
- a longer thematic breakdown block (Telegram-style numbered sections)
- a source appendix mapping report claims to commits/PRs

This is an experiment-first workflow: run generator, review output quality, iterate.

## 2. Scope

In scope:

- markdown output only
- CLI-driven generation from a release range
- structure and tone optimized for similar presentation style
- traceability appendix for reviewability

Out of scope (phase 1):

- direct publishing to X/Telegram/GitHub
- multilingual generation
- fully autonomous prose generation without curated taxonomy and ranking rules

## 3. Candidate approaches

### A. Template-only generator

Input is fully curated by maintainers; generator only renders markdown.

Pros:

- fastest implementation
- predictable output quality

Cons:

- heavy manual authoring each release
- low reuse value

### B. Fully automatic from git/PR metadata

Generator ingests commits/PR metadata and fully authors report.

Pros:

- minimal manual effort per release
- scalable for frequent releases

Cons:

- higher noise risk
- harder to control narrative quality without mature rules

### C. Hybrid generator (recommended)

Generator ingests tag range plus optional override file for must-include highlights and wording controls.

Pros:

- balances automation and quality control
- easy to iterate from experiment to production
- reusable for future releases

Cons:

- slightly more moving parts than template-only

Decision: adopt C for v2.0 experiment.

## 4. Output contract (fixed markdown shape)

The generated markdown must contain:

1. Title + version + date
2. Short highlights section (concise bullets)
3. Long thematic breakdown (numbered sections, narrative paragraphs)
4. Source appendix mapping each section to supporting commits/PRs

The generator should fail the run if required output blocks are missing.

## 5. Workflow design

### 5.1 Collect

Inputs:

- required: `--from <tag>` and `--to <ref>` (default `HEAD`)
- optional: override file (e.g. `docs/release/overrides/v2.0.yaml`)

Data sources:

- local git commit history for the range
- GitHub PR metadata when available

### 5.2 Normalize

Convert each raw record into a normalized change item:

- `title`
- `body`
- `type` (`feat`/`fix`/`chore`/`security`/...)
- `scope`
- `pr`
- `author`
- flags: `breaking`, `security`

De-noise rules:

- collapse duplicate merge/sync commits
- down-rank automation-only churn

### 5.3 Group

Rule-based thematic bucketing:

- Security
- Provider/Model stack
- Channels & UX
- Memory & Scheduling
- Tools & Agent behavior
- CI/Release
- Misc (fallback)

Priority rule:

- `security` and `breaking` items rank first and appear near the top sections

### 5.4 Render

Render deterministic markdown with:

- concise highlight bullets
- numbered thematic sections with narrative summaries
- appendix with explicit source mapping

Optional sidecar JSON:

- machine-readable source map for audit and later automation

## 6. Components and files

Primary component:

- `scripts/release/generate_release_report.py` (main CLI)

Supporting artifacts:

- `scripts/release/report_taxonomy.yaml` (classification rules)
- optional renderer module (`scripts/release/report_renderer.py`) if needed
- optional override file per release (`docs/release/overrides/v2.0.yaml`)

Example run:

```bash
python3 scripts/release/generate_release_report.py \
  --from v0.1.7 \
  --to HEAD \
  --out artifacts/v2.0-release-report.md
```

## 7. Error handling

- Missing `--from` tag: fail with actionable tag discovery hint
- Empty change range: emit minimal valid report with explicit no-material-change note
- GitHub metadata unavailable: continue in git-only fallback mode
- Unclassified changes: route to `Misc` and include in appendix

## 8. Quality gates

- Output contains all required sections
- Security/breaking changes are surfaced at top priority
- Every narrative section includes at least one appendix source mapping

## 9. Test strategy

- unit tests for taxonomy/classification behavior
- golden-file snapshot test for markdown stability
- CLI integration tests for range parsing and fallback behavior
- manual dry run for maintainers before release messaging

## 10. Experiment success criteria

A run is considered successful when:

- report is readable with minimal manual editing
- critical changes are correctly prioritized and grouped
- appendix enables quick claim-to-source validation
- maintainers can provide targeted feedback for next iteration

## 11. Next step

Create an implementation plan (writing-plans workflow) and then implement the hybrid generator for the first v2.0 experimental run.
