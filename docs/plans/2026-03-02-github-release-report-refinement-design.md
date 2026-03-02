# GitHub Release Report Refinement Design

Date: 2026-03-02

## Objective

Refine the GitHub release report so it is easier to scan and read, while still preserving evidence links and high coverage. The main report should be concise and reader-friendly, with exhaustive detail moved to the appendix.

## User-Approved Direction

- Prioritize readability over denser technical formatting.
- Remove top-level report header/title section in the generated release note body.
- Start with one short introductory paragraph.
- Then render area sections only.
- Bullets should be simple, one sentence each, extracted/cleaned from commit subjects.
- Keep community recognition, with a dedicated special-thanks area for first-time contributors only.

## Output Structure (Main GitHub Report)

1. Intro paragraph
- One short paragraph that states release scope and includes compare/appendix reference.

2. Area sections only
- `## Security`
- `## Channels & UX`
- `## Provider/Model stack`
- `## Tools & Agent behavior`
- `## Memory & Scheduling`
- `## CI/Release`
- `## Platform & Maintenance`
- `## Other: <Domain>` as needed

For each area:
- One short area description sentence.
- 3-6 simple bullets.
- If more items exist: one line `More changes in appendix.`

3. Community sections at end
- `## Community Contributors` for general non-core contributor thanks.
- `## Special Thanks: First-Time Contributors` for first-time contributors only.

## Bullet Rendering Rules

- Bullet text should be a clean sentence from commit subject.
- Remove noisy wrapper tokens when safe (`feat(...)`, `fix(...)`, `chore(...)`).
- Keep technical keywords and proper nouns unchanged.
- Avoid template-heavy patterns (`Scope/Why/Ref` in each bullet).
- Keep links lightweight:
  - PR link when PR number exists.
  - Commit short link fallback.

## Area Selection and Ordering

- Keep a fixed high-signal area order for readability.
- Expand fallback bucket into meaningful `Other: <Domain>` areas when domain size threshold is met.
- Keep leftover fallback as `Platform & Maintenance`.
- Do not allow fallback area to dominate highlight selection.

## Contributor Rules

### Core team exclusion
Core team identities excluded from community thanks:
- `theonlyhennygod`
- `chumyin`
- `gh-xj`

Alias normalization continues to map known author/email variants.

### Community Contributors
- Include non-core, non-bot contributors.
- Rank by commit count in release range.
- Show concise list in main report (top-N).

### Special Thanks: First-Time Contributors
Only contributors whose first-ever repository commit falls inside the current release range.

Proposed detection method:
- Build normalized contributor identities from current range (`author_name`, `author_email`, GitHub handle when available).
- Compute each identity's earliest commit in repository history (`--all`) once.
- Contributor is first-time if earliest commit SHA is included in current release range.

Notes:
- Identity normalization should reuse existing alias/email/handle normalization.
- Bot/system identities are excluded.

## Appendix and Sources

- Keep full source-mapped changelog in appendix markdown.
- Keep JSON mapping output for validation and review.
- Main report remains concise; appendix remains exhaustive.

## Success Criteria

- Main report is visibly shorter and more readable than previous renderer style.
- Area sections are paragraph + simple bullets only.
- No dense template bullet dumping in the main report.
- Community section excludes core team.
- First-time contributors are recognized in dedicated special-thanks section only when they qualify.
- Existing tests remain green, with added tests for first-time contributor detection/rendering.

## Out of Scope

- Marketing Twitter copy tuning.
- Rewriting taxonomy definitions from scratch.
- Removing appendix/source outputs.
