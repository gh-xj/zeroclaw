#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


EXIT_MISSING_REF = 2
EXIT_GIT_FAILURE = 3
DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("report_taxonomy.json")

CONVENTIONAL_TYPE_PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?:!)?:")
SECURITY_PATTERN = re.compile(
    r"\bsecurity\b|prompt\s*guard|prompt\s*injection|harden(?:ing)?|vuln|cve|leak",
    re.IGNORECASE,
)
BREAKING_CHANGE_PATTERN = re.compile(
    r"^[a-z]+(?:\([^)]+\))?!:|\bbreaking\s+changes?\b",
    re.IGNORECASE,
)
NEGATED_BREAKING_PATTERN = re.compile(r"\bnon[-\s]+breaking\b", re.IGNORECASE)
KNOWN_TYPES = {
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
}


def load_taxonomy(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sections = raw.get("sections")
    fallback_section = raw.get("fallback_section")

    if not isinstance(sections, list) or not isinstance(fallback_section, str):
        raise ValueError("taxonomy schema is invalid")

    normalized_sections: list[dict[str, object]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue

        name = section.get("name")
        keywords = section.get("keywords")
        if not isinstance(name, str) or not isinstance(keywords, list):
            continue

        normalized_sections.append(
            {
                "name": name,
                "keywords": [str(keyword) for keyword in keywords],
            }
        )

    if not normalized_sections:
        raise ValueError("taxonomy contains no valid sections")

    return {
        "sections": normalized_sections,
        "fallback_section": fallback_section,
    }


def group_changes(
    changes: list[dict[str, object]],
    taxonomy: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    sections = taxonomy.get("sections", [])
    fallback_section = str(taxonomy.get("fallback_section", "Misc"))
    section_patterns: list[tuple[str, list[re.Pattern[str]]]] = []

    for section in sections:
        section_name = str(section.get("name"))
        grouped[section_name] = []
        patterns: list[re.Pattern[str]] = []
        for raw_keyword in section.get("keywords", []):
            keyword = str(raw_keyword).strip().lower()
            if not keyword:
                continue
            escaped_keyword = re.escape(keyword)
            # Match keyword boundaries against alphanumeric tokens to avoid
            # inside-word false positives like "ci" in "precision".
            patterns.append(re.compile(rf"(?<![a-z0-9]){escaped_keyword}(?![a-z0-9])"))
        section_patterns.append((section_name, patterns))
    grouped[fallback_section] = []

    for change in changes:
        lowered_title = str(change.get("title", "")).lower()
        assigned = False
        for section_name, patterns in section_patterns:
            if any(pattern.search(lowered_title) for pattern in patterns):
                grouped[section_name].append(change)
                assigned = True
                break

        if not assigned:
            grouped[fallback_section].append(change)

    security_section = grouped.get("Security")
    if security_section is not None:
        security_section.sort(
            key=lambda item: (
                not bool(item.get("security")),
                not bool(item.get("breaking")),
            )
        )

    return {name: section_changes for name, section_changes in grouped.items() if section_changes}


def is_missing_ref_error(stderr: str) -> bool:
    normalized = stderr.lower()
    markers = (
        "needed a single revision",
        "unknown revision or path not in the working tree",
        "not a valid object name",
        "ambiguous argument",
    )
    return any(marker in normalized for marker in markers)


def validate_git_ref(ref: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, ""

    stderr = (proc.stderr or "").strip()
    if is_missing_ref_error(stderr):
        return False, "missing"
    return False, stderr or "git rev-parse failed without stderr"


def infer_change_type(title: str) -> str:
    if SECURITY_PATTERN.search(title):
        return "security"

    conventional = CONVENTIONAL_TYPE_PATTERN.match(title)
    if conventional:
        commit_type = conventional.group("type").lower()
        if commit_type in KNOWN_TYPES:
            return commit_type

    lowered = title.lower()
    if lowered.startswith("fix"):
        return "fix"
    if lowered.startswith("feat"):
        return "feat"
    if lowered.startswith("ci") or "pipeline" in lowered:
        return "ci"
    return "misc"


def is_security_change(title: str) -> bool:
    return bool(SECURITY_PATTERN.search(title))


def is_breaking_change(title: str) -> bool:
    sanitized_title = NEGATED_BREAKING_PATTERN.sub("", title)
    return bool(BREAKING_CHANGE_PATTERN.search(sanitized_title))


def collect_changes(from_ref: str, to_ref: str) -> list[dict[str, object]]:
    fmt = "%H%x1f%s%x1e"
    proc = subprocess.run(
        ["git", "log", "--no-merges", f"{from_ref}..{to_ref}", f"--pretty=format:{fmt}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or "git log failed without stderr")

    changes: list[dict[str, object]] = []
    for raw_record in proc.stdout.split("\x1e"):
        record = raw_record.strip()
        if not record:
            continue

        parts = record.split("\x1f", 1)
        if len(parts) != 2:
            continue

        sha, title = parts
        normalized_title = title.strip()
        changes.append(
            {
                "sha": sha,
                "title": normalized_title,
                "type": infer_change_type(normalized_title),
                "security": is_security_change(normalized_title),
                "breaking": is_breaking_change(normalized_title),
            }
        )

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release report markdown")
    parser.add_argument("--from", dest="from_ref", required=True)
    parser.add_argument("--to", dest="to_ref", default="HEAD")
    parser.add_argument("--out", required=True)
    parser.add_argument("--sources-json", dest="sources_json")
    args = parser.parse_args()

    from_ok, from_error = validate_git_ref(args.from_ref)
    if not from_ok:
        if from_error == "missing":
            print(f"from tag not found: {args.from_ref}", file=sys.stderr)
            return EXIT_MISSING_REF
        print(f"git failed while validating --from: {from_error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    to_ok, to_error = validate_git_ref(args.to_ref)
    if not to_ok:
        if to_error == "missing":
            print(f"to ref not found: {args.to_ref}", file=sys.stderr)
            return EXIT_MISSING_REF
        print(f"git failed while validating --to: {to_error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    try:
        changes = collect_changes(args.from_ref, args.to_ref)
    except RuntimeError as error:
        print(f"git failed while collecting changes: {error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    try:
        taxonomy = load_taxonomy(DEFAULT_TAXONOMY_PATH)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"failed to load taxonomy: {error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    grouped_changes = group_changes(changes, taxonomy)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("# placeholder\n", encoding="utf-8")

    if args.sources_json:
        sources_path = Path(args.sources_json)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "changes": changes,
            "grouped": grouped_changes,
        }
        sources_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
