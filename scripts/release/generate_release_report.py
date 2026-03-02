#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


EXIT_MISSING_REF = 2
EXIT_GIT_FAILURE = 3
DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("report_taxonomy.json")
DEFAULT_CORE_DEVS = ("theonlyhennygod", "chumyin", "gh-xj")

CORE_TEAM_ALIASES = {
    "theonlyhennygod": {"argenis", "argenisdelarosa", "argenis de la rosa", "theonlyhennygod"},
    "chumyin": {"chumyin", "chummy", "chum yin", "chumyin0912"},
    "gh-xj": {"gh-xj", "ghxj", "xj", "xiangjun"},
}

CONVENTIONAL_TYPE_PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?:!)?:\s*(?P<summary>.+)$")
SECURITY_PATTERN = re.compile(
    r"\bsecurity\b|prompt\s*guard|prompt\s*injection|harden(?:ing)?|vuln|cve|leak",
    re.IGNORECASE,
)
BREAKING_CHANGE_PATTERN = re.compile(
    r"^[a-z]+(?:\([^)]+\))?!:|\bbreaking\s+changes?\b",
    re.IGNORECASE,
)
NEGATED_BREAKING_PATTERN = re.compile(r"\bnon[-\s]+breaking\b", re.IGNORECASE)
PR_SUFFIX_PATTERN = re.compile(r"\s+\(#(?P<number>\d+)\)\s*$")
GITHUB_URL_PATTERN = re.compile(r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$")

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
CHANGE_TYPE_PRIORITY = {
    "security": 0,
    "feat": 1,
    "fix": 2,
    "perf": 3,
    "refactor": 4,
    "ci": 5,
    "test": 6,
    "docs": 7,
    "chore": 8,
    "misc": 9,
}
CHANGE_IMPACT_WEIGHTS = {
    "security": 8,
    "feat": 4,
    "fix": 3,
    "perf": 3,
    "refactor": 2,
    "ci": 2,
    "test": 1,
    "docs": 1,
    "chore": 1,
    "misc": 1,
}
TYPE_WHY_MAP = {
    "security": "reduces abuse and data-exposure risk in runtime paths.",
    "feat": "adds user-visible capability for day-to-day workflows.",
    "fix": "improves reliability on production code paths.",
    "perf": "reduces operational latency and runtime overhead.",
    "refactor": "improves maintainability for follow-on work.",
    "ci": "lowers release risk by hardening automation checks.",
    "test": "improves confidence by increasing coverage.",
    "docs": "makes onboarding and operator workflows clearer.",
    "chore": "keeps core tooling and dependencies healthy.",
    "misc": "improves overall product quality in this area.",
}
SECTION_THEME_HINTS = {
    "Security": "tightened policy and boundary controls",
    "Provider/Model stack": "improved model/provider coverage and fallbacks",
    "Channels & UX": "improved multi-channel experience and interaction quality",
    "Memory & Scheduling": "stabilized memory lifecycle and scheduler behavior",
    "Tools & Agent behavior": "strengthened tool execution and agent loop behavior",
    "CI/Release": "hardened build, release, and compliance workflows",
}
MAIN_REPORT_SECTION_ORDER = (
    "Security",
    "Channels & UX",
    "Provider/Model stack",
    "Tools & Agent behavior",
    "Memory & Scheduling",
    "CI/Release",
    "Platform & Maintenance",
)
SCOPE_LABEL_OVERRIDES = {
    "android": "Android",
    "gateway": "Gateway",
    "web": "Web",
    "webchat": "Webchat",
    "plugins": "Plugins",
    "plugin": "Plugins",
    "config": "Configuration",
    "docs": "Documentation",
    "onboard": "Onboarding",
    "integrations": "Integrations",
    "integration": "Integrations",
}
MISC_KEYWORD_LABELS = (
    ("android", "Android"),
    ("gateway", "Gateway"),
    ("websocket", "Gateway"),
    ("webchat", "Webchat"),
    ("plugin", "Plugins"),
    ("skills", "Skills"),
    ("onboard", "Onboarding"),
    ("docs", "Documentation"),
    ("release", "Release Process"),
    ("config", "Configuration"),
)
MISC_TYPE_LABELS = {
    "docs": "Documentation",
    "test": "Testing",
    "ci": "Release Process",
    "feat": "Feature Work",
    "fix": "Reliability Fixes",
    "chore": "Maintenance",
    "refactor": "Refactoring",
}
STRATEGIC_HIGHLIGHT_KEYWORDS = (
    "stepfun",
    "step fun",
    "agent swarm",
    "sub-agent",
    "orchestration",
    "multi-agent",
)
SEMANTIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "to",
    "for",
    "with",
    "of",
    "in",
    "on",
    "by",
    "from",
    "into",
    "across",
}


def normalize_identity(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


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
            # Boundary-aware keyword matching avoids false positives like "ci" in "precision".
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


def parse_subject(title: str) -> tuple[str, str, str | None]:
    stripped = title.strip()
    pr_match = PR_SUFFIX_PATTERN.search(stripped)
    pr_number: str | None = None
    if pr_match:
        pr_number = pr_match.group("number")
        stripped = PR_SUFFIX_PATTERN.sub("", stripped).strip()

    match = CONVENTIONAL_TYPE_PATTERN.match(stripped)
    if not match:
        return "", "", pr_number

    commit_type = match.group("type") or ""
    scope = (match.group("scope") or "").strip()
    return commit_type.lower(), scope.lower(), pr_number


def clean_commit_sentence(title: str) -> str:
    text = str(title).strip()
    if not text:
        return "(untitled change)."

    text = PR_SUFFIX_PATTERN.sub("", text).strip()

    conventional = CONVENTIONAL_TYPE_PATTERN.match(text)
    if conventional:
        commit_type = str(conventional.group("type") or "").lower()
        summary = str(conventional.group("summary") or "").strip()
        if commit_type in KNOWN_TYPES and summary:
            text = summary

    if not text:
        return "(untitled change)."

    first = text[0]
    if first.isalpha() and first.islower():
        text = first.upper() + text[1:]

    text = re.sub(r"[.!?\s]+$", "", text)
    if not text:
        return "(untitled change)."

    return f"{text}."


def infer_change_type(title: str) -> str:
    conventional = CONVENTIONAL_TYPE_PATTERN.match(title)
    if conventional:
        commit_type = conventional.group("type").lower()
        if commit_type in KNOWN_TYPES:
            if SECURITY_PATTERN.search(title) and (
                str(conventional.group("scope") or "").strip().lower() in {"security", "sec"}
            ):
                return "security"
            return commit_type

    if SECURITY_PATTERN.search(title):
        return "security"

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
    fmt = "%H%x1f%an%x1f%ae%x1f%s%x1e"
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

        parts = record.split("\x1f", 3)
        if len(parts) != 4:
            continue

        sha, author_name, author_email, raw_title = parts
        trimmed_raw_title = raw_title.strip()
        commit_type, scope, pr_number = parse_subject(trimmed_raw_title)
        normalized_title = PR_SUFFIX_PATTERN.sub("", trimmed_raw_title).strip()
        inferred_type = infer_change_type(normalized_title)

        changes.append(
            {
                "sha": sha,
                "raw_title": trimmed_raw_title,
                "title": normalized_title,
                "type": inferred_type if inferred_type else (commit_type or "misc"),
                "conventional_type": commit_type or None,
                "scope": scope,
                "security": is_security_change(normalized_title),
                "breaking": is_breaking_change(normalized_title),
                "pr_number": int(pr_number) if pr_number else None,
                "author_name": author_name.strip(),
                "author_email": author_email.strip(),
            }
        )

    return changes


def iter_grouped_changes(
    grouped_changes: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for section_changes in grouped_changes.values():
        flattened.extend(section_changes)
    return flattened


def rank_change(change: dict[str, object]) -> tuple[int, int, int, str]:
    change_type = str(change.get("type", "misc")).lower()
    return (
        0 if bool(change.get("security")) else 1,
        0 if bool(change.get("breaking")) else 1,
        CHANGE_TYPE_PRIORITY.get(change_type, 99),
        str(change.get("title", "")).lower(),
    )


def rank_changes(changes: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(changes, key=rank_change)


def dedupe_changes_by_title(changes: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen_titles: set[str] = set()
    for change in changes:
        title = str(change.get("title", "")).strip()
        if not title or title in seen_titles:
            continue
        deduped.append(change)
        seen_titles.add(title)
    return deduped


def load_override(path: str | None) -> dict[str, object]:
    if not path:
        return {}

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("override schema is invalid")

    normalized: dict[str, object] = {}
    if "must_include" in raw:
        must_include = raw.get("must_include")
        if not isinstance(must_include, list):
            raise ValueError("override must_include must be a list")
        normalized["must_include"] = [
            str(item).strip()
            for item in must_include
            if str(item).strip()
        ]

    if "must_promote" in raw:
        must_promote = raw.get("must_promote")
        if not isinstance(must_promote, list):
            raise ValueError("override must_promote must be a list")
        normalized["must_promote"] = [
            str(item).strip()
            for item in must_promote
            if str(item).strip()
        ]
    return normalized


def change_weight(change: dict[str, object]) -> int:
    change_type = str(change.get("type", "misc")).lower()
    weight = CHANGE_IMPACT_WEIGHTS.get(change_type, 1)
    if bool(change.get("security")):
        weight += 4
    if bool(change.get("breaking")):
        weight += 3
    return weight


def build_section_stats(
    grouped_changes: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    stats: list[dict[str, object]] = []
    for section_name, section_changes in grouped_changes.items():
        by_type = Counter(str(change.get("type", "misc")) for change in section_changes)
        security_count = sum(1 for change in section_changes if bool(change.get("security")))
        breaking_count = sum(1 for change in section_changes if bool(change.get("breaking")))
        impact_score = sum(change_weight(change) for change in section_changes)
        risk = "low"
        if breaking_count > 0 or security_count >= 8:
            risk = "high"
        elif security_count > 0 or by_type.get("fix", 0) >= 5:
            risk = "medium"

        stats.append(
            {
                "section": section_name,
                "count": len(section_changes),
                "security": security_count,
                "breaking": breaking_count,
                "impact": impact_score,
                "risk": risk,
                "type_counts": dict(by_type),
            }
        )

    return sorted(stats, key=lambda item: (-int(item["impact"]), -int(item["count"]), str(item["section"]).lower()))


def order_main_report_sections(
    grouped_changes: dict[str, list[dict[str, object]]],
) -> list[str]:
    ordered: list[str] = []
    for section_name in MAIN_REPORT_SECTION_ORDER:
        if grouped_changes.get(section_name):
            ordered.append(section_name)

    ordered_set = set(ordered)
    other_sections = [
        (section_name, section_changes)
        for section_name, section_changes in grouped_changes.items()
        if section_name.startswith("Other:") and section_name not in ordered_set and section_changes
    ]
    other_sections.sort(key=lambda item: (-len(item[1]), item[0].lower()))
    ordered.extend(section_name for section_name, _ in other_sections)
    ordered_set.update(section_name for section_name, _ in other_sections)

    remaining_sections = [
        (section_name, section_changes)
        for section_name, section_changes in grouped_changes.items()
        if section_name not in ordered_set and section_changes
    ]
    remaining_sections.sort(key=lambda item: (-len(item[1]), item[0].lower()))
    ordered.extend(section_name for section_name, _ in remaining_sections)
    return ordered


def select_highlights(
    *,
    grouped_changes: dict[str, list[dict[str, object]]],
    changes: list[dict[str, object]],
    override: dict[str, object],
    highlight_limit: int = 6,
    max_per_section: int = 2,
) -> list[dict[str, object]]:
    ranked_changes = dedupe_changes_by_title(rank_changes(changes))
    raw_must_include = override.get("must_include", [])
    must_include_titles = [str(item).strip() for item in raw_must_include if str(item).strip()]

    title_to_change: dict[str, dict[str, object]] = {}
    for change in ranked_changes:
        title = str(change.get("title", "")).strip()
        if title and title not in title_to_change:
            title_to_change[title] = change

    title_to_section: dict[str, str] = {}
    for section_name, section_items in grouped_changes.items():
        for change in section_items:
            title = str(change.get("title", "")).strip()
            if title and title not in title_to_section:
                title_to_section[title] = section_name

    section_stats = build_section_stats(grouped_changes)
    primary_sections = [
        str(entry["section"])
        for entry in section_stats
        if str(entry["section"]) != "Platform & Maintenance"
        and not str(entry["section"]).startswith("Other:")
    ]
    secondary_sections = [
        str(entry["section"])
        for entry in section_stats
        if str(entry["section"]) == "Platform & Maintenance"
        or str(entry["section"]).startswith("Other:")
    ]
    section_order = primary_sections + secondary_sections

    selected: list[dict[str, object]] = []
    selected_titles: set[str] = set()
    section_counts: Counter[str] = Counter()

    for title in must_include_titles:
        matched = title_to_change.get(title)
        if not matched:
            continue
        selected.append(matched)
        selected_titles.add(title)
        section_name = title_to_section.get(title, "Misc")
        section_counts[section_name] += 1
        if len(selected) >= highlight_limit:
            return selected[:highlight_limit]

    for section_name in section_order:
        ranked_section = rank_changes(grouped_changes.get(section_name, []))
        for change in ranked_section:
            title = str(change.get("title", "")).strip()
            if not title or title in selected_titles:
                continue
            if section_counts[section_name] >= max_per_section:
                continue
            selected.append(change)
            selected_titles.add(title)
            section_counts[section_name] += 1
            break
        if len(selected) >= highlight_limit:
            return selected[:highlight_limit]

    for change in ranked_changes:
        title = str(change.get("title", "")).strip()
        if not title or title in selected_titles:
            continue
        section_name = title_to_section.get(title, "Misc")
        if section_counts[section_name] >= max_per_section:
            continue
        selected.append(change)
        selected_titles.add(title)
        section_counts[section_name] += 1
        if len(selected) >= highlight_limit:
            return selected[:highlight_limit]

    for change in ranked_changes:
        title = str(change.get("title", "")).strip()
        if not title or title in selected_titles:
            continue
        selected.append(change)
        selected_titles.add(title)
        if len(selected) >= highlight_limit:
            break

    return selected[:highlight_limit]


def select_key_changes_for_section(
    section_changes: list[dict[str, object]],
    *,
    max_items: int = 3,
) -> list[dict[str, object]]:
    return dedupe_changes_by_title(rank_changes(section_changes))[:max_items]


def is_strategic_highlight(change: dict[str, object]) -> bool:
    title = str(change.get("title", "")).lower()
    return any(keyword in title for keyword in STRATEGIC_HIGHLIGHT_KEYWORDS)


def is_override_promoted(change: dict[str, object], must_promote_terms: list[str]) -> bool:
    title = str(change.get("title", "")).strip().lower()
    if not title:
        return False

    for term in must_promote_terms:
        lowered_term = str(term).strip().lower()
        if not lowered_term:
            continue
        if lowered_term in title:
            return True
    return False


def select_main_area_bullets(
    section_changes: list[dict[str, object]],
    *,
    max_items: int | None = None,
    must_promote_terms: list[str] | None = None,
) -> list[dict[str, object]]:
    ranked = rank_changes(section_changes)
    if not ranked:
        return []

    must_promote_terms = must_promote_terms or []
    selected: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str]] = set()

    # Explicit release-level override terms have highest precedence.
    for change in ranked:
        title = str(change.get("title", "")).strip()
        sha = str(change.get("sha", "")).strip()
        key = (sha, title)
        if not title or key in seen_keys:
            continue
        if not is_override_promoted(change, must_promote_terms):
            continue
        selected.append(change)
        seen_keys.add(key)
        if max_items is not None and len(selected) >= max_items:
            return selected

    # Always elevate strategic commits (for example StepFun / swarm-orchestration)
    # so key product narratives remain visible in the concise report.
    for change in ranked:
        title = str(change.get("title", "")).strip()
        sha = str(change.get("sha", "")).strip()
        key = (sha, title)
        if not title or key in seen_keys:
            continue
        if not is_strategic_highlight(change):
            continue
        selected.append(change)
        seen_keys.add(key)
        if max_items is not None and len(selected) >= max_items:
            return selected

    for change in ranked:
        title = str(change.get("title", "")).strip()
        sha = str(change.get("sha", "")).strip()
        key = (sha, title)
        if not title or key in seen_keys:
            continue
        selected.append(change)
        seen_keys.add(key)
        if max_items is not None and len(selected) >= max_items:
            break

    return selected


def semantic_tokens_for_change(title: str) -> list[str]:
    sentence = clean_commit_sentence(title).lower()
    sentence = re.sub(r"[^a-z0-9]+", " ", sentence).strip()
    if not sentence:
        return []

    tokens: list[str] = []
    for token in sentence.split():
        if token in SEMANTIC_STOPWORDS:
            continue
        if len(token) <= 2:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        tokens.append(token)
    return tokens


def are_semantically_similar_titles(left_title: str, right_title: str) -> bool:
    left_numbers = set(re.findall(r"\b\d+\b", left_title))
    right_numbers = set(re.findall(r"\b\d+\b", right_title))
    if left_numbers or right_numbers:
        if left_numbers != right_numbers:
            return False

    left_tokens = semantic_tokens_for_change(left_title)
    right_tokens = semantic_tokens_for_change(right_title)
    if not left_tokens or not right_tokens:
        return False

    left_set = set(left_tokens)
    right_set = set(right_tokens)
    intersection = left_set & right_set
    if not intersection:
        return False

    overlap = len(intersection) / max(1, min(len(left_set), len(right_set)))
    jaccard = len(intersection) / max(1, len(left_set | right_set))
    return overlap >= 0.80 or jaccard >= 0.67


def cluster_semantic_changes(changes: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    clusters: list[list[dict[str, object]]] = []
    lead_titles: list[str] = []
    for change in changes:
        title = str(change.get("title", "")).strip()
        if not title:
            continue

        assigned = False
        for idx, lead_title in enumerate(lead_titles):
            if are_semantically_similar_titles(title, lead_title):
                clusters[idx].append(change)
                assigned = True
                break

        if not assigned:
            lead_titles.append(title)
            clusters.append([change])

    return clusters


def short_change_ref(change: dict[str, object], repo_slug: str | None = None) -> str:
    return format_change_ref(change, repo_slug)


def format_main_report_ref(
    change: dict[str, object],
    *,
    repo_slug: str | None = None,
    ref_mode: str = "hybrid",
) -> str:
    if ref_mode == "pr_only":
        pr_number = change.get("pr_number")
        if not isinstance(pr_number, int):
            return ""
        pr_url = build_pr_url(repo_slug, pr_number)
        if pr_url:
            return f"[#{pr_number}]({pr_url})"
        return f"#{pr_number}"
    return short_change_ref(change, repo_slug)


def infer_core_handle(author_name: str, author_email: str) -> str | None:
    tokens = {
        normalize_identity(author_name),
        normalize_identity(author_email),
        normalize_identity(author_email.split("@", 1)[0]),
    }
    for canonical, aliases in CORE_TEAM_ALIASES.items():
        canonical_token = normalize_identity(canonical)
        if canonical_token in tokens:
            return canonical
        for alias in aliases:
            alias_token = normalize_identity(alias)
            if alias_token and alias_token in tokens:
                return canonical
    return None


def resolve_author_handle(change: dict[str, object]) -> str | None:
    author_name = str(change.get("author_name", "")).strip()
    author_email = str(change.get("author_email", "")).strip()
    core_handle = infer_core_handle(author_name, author_email)
    if core_handle:
        return core_handle

    handle = extract_github_handle(author_name, author_email)
    if handle:
        return handle

    pr_author_login = str(change.get("pr_author_login", "")).strip()
    if pr_author_login:
        return pr_author_login
    return None


def format_author_ref(change: dict[str, object]) -> str:
    author_name = str(change.get("author_name", "")).strip()
    handle = resolve_author_handle(change)
    if handle:
        return f"[@{handle}](https://github.com/{quote(handle, safe='')})"
    return author_name


def ref_with_author(
    change: dict[str, object],
    repo_slug: str | None = None,
    *,
    ref_mode: str = "hybrid",
) -> str:
    ref = format_main_report_ref(change, repo_slug=repo_slug, ref_mode=ref_mode)
    author = str(change.get("author_name", "")).strip()
    author_ref = format_author_ref(change) or author
    if ref and author:
        return f"{ref} by {author_ref}"
    if ref:
        return ref
    if author_ref:
        return f"by {author_ref}"
    return ""


def render_compound_change_sentence(
    compound_changes: list[dict[str, object]],
    *,
    repo_slug: str | None = None,
    ref_mode: str = "hybrid",
) -> str:
    lead_change = compound_changes[0]
    sentence = clean_commit_sentence(str(lead_change.get("title", "")).strip())
    sentence_without_period = sentence[:-1] if sentence.endswith(".") else sentence

    refs: list[str] = []
    seen_refs: set[str] = set()
    for change in compound_changes:
        part = ref_with_author(change, repo_slug, ref_mode=ref_mode)
        if not part or part in seen_refs:
            continue
        refs.append(part)
        seen_refs.add(part)

    if refs:
        return f"{sentence_without_period} ({'; '.join(refs)})."
    return sentence


def build_main_report_area_flow(
    grouped_changes: dict[str, list[dict[str, object]]],
    *,
    must_promote_terms: list[str] | None = None,
) -> list[dict[str, object]]:
    flow: list[dict[str, object]] = []
    normalized_terms = must_promote_terms or []
    for section_name in order_main_report_sections(grouped_changes):
        section_changes = grouped_changes.get(str(section_name), [])
        if not section_changes:
            continue
        selected_changes = select_main_area_bullets(
            section_changes,
            must_promote_terms=normalized_terms,
        )
        flow.append(
            {
                "section": section_name,
                "section_changes": section_changes,
                "selected_changes": selected_changes,
                "compound_groups": cluster_semantic_changes(selected_changes),
            }
        )
    return flow


def build_workflow_stage_payloads(
    *,
    version: str,
    generated_at: str,
    must_promote_terms: list[str],
    area_flow: list[dict[str, object]],
    repo_slug: str | None = None,
    main_ref_mode: str = "hybrid",
) -> tuple[dict[str, object], dict[str, object]]:
    pre_compound_sections: list[dict[str, object]] = []
    pre_synthesis_sections: list[dict[str, object]] = []
    for item in area_flow:
        section = str(item.get("section", "")).strip()
        section_changes = item.get("section_changes", [])
        selected_changes = item.get("selected_changes", [])
        compound_groups = item.get("compound_groups", [])
        if not section:
            continue
        pre_compound_sections.append(
            {
                "section": section,
                "area_commit_count": len(section_changes) if isinstance(section_changes, list) else 0,
                "selected_changes": selected_changes if isinstance(selected_changes, list) else [],
            }
        )
        compound_groups_list = compound_groups if isinstance(compound_groups, list) else []
        pre_synthesis_sections.append(
            {
                "section": section,
                "area_commit_count": len(section_changes) if isinstance(section_changes, list) else 0,
                "compound_groups": compound_groups_list,
                "compound_bullets": [
                    render_compound_change_sentence(
                        group,
                        repo_slug=repo_slug,
                        ref_mode=main_ref_mode,
                    )
                    for group in compound_groups_list
                    if isinstance(group, list) and group
                ],
            }
        )

    meta = {
        "version_range": version,
        "generated_at": generated_at,
        "must_promote_terms": must_promote_terms,
    }
    pre_compound_payload = {
        "stage": "pre-compound",
        "meta": meta,
        "sections": pre_compound_sections,
    }
    pre_synthesis_payload = {
        "stage": "pre-synthesis",
        "meta": meta,
        "sections": pre_synthesis_sections,
    }
    return pre_compound_payload, pre_synthesis_payload


def write_workflow_stage_artifacts(
    *,
    stage_dir: Path,
    version: str,
    generated_at: str,
    must_promote_terms: list[str],
    area_flow: list[dict[str, object]],
    repo_slug: str | None = None,
    main_ref_mode: str = "hybrid",
) -> None:
    pre_compound_payload, pre_synthesis_payload = build_workflow_stage_payloads(
        version=version,
        generated_at=generated_at,
        must_promote_terms=must_promote_terms,
        area_flow=area_flow,
        repo_slug=repo_slug,
        main_ref_mode=main_ref_mode,
    )
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "01-pre-compound.json").write_text(
        json.dumps(pre_compound_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (stage_dir / "02-pre-synthesis.json").write_text(
        json.dumps(pre_synthesis_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def format_scope_label(scope: str) -> str:
    trimmed = scope.strip().lower()
    if not trimmed:
        return "General"
    if trimmed in SCOPE_LABEL_OVERRIDES:
        return SCOPE_LABEL_OVERRIDES[trimmed]

    compact = re.sub(r"[^a-z0-9]+", " ", trimmed).strip()
    if not compact:
        return "General"

    return " ".join(part.capitalize() for part in compact.split())


def infer_misc_domain_label(change: dict[str, object]) -> str:
    scope = str(change.get("scope", "")).strip()
    if not scope:
        maybe_scope_match = CONVENTIONAL_TYPE_PATTERN.match(str(change.get("title", "")).strip())
        scope = (maybe_scope_match.group("scope") if maybe_scope_match else "") or ""
    if scope:
        return format_scope_label(scope)

    title_lower = str(change.get("title", "")).lower()
    for keyword, label in MISC_KEYWORD_LABELS:
        if keyword in title_lower:
            return label

    conventional_type = str(change.get("conventional_type", "")).lower()
    if conventional_type in MISC_TYPE_LABELS:
        return MISC_TYPE_LABELS[conventional_type]

    change_type = str(change.get("type", "")).lower()
    if change_type in MISC_TYPE_LABELS:
        return MISC_TYPE_LABELS[change_type]

    return "General"


def expand_misc_domains(
    grouped_changes: dict[str, list[dict[str, object]]],
    *,
    misc_name: str = "Misc",
    min_domain_size: int = 2,
    max_domains: int = 12,
) -> dict[str, list[dict[str, object]]]:
    misc_changes = grouped_changes.get(misc_name)
    if not misc_changes:
        return grouped_changes

    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for change in misc_changes:
        label = infer_misc_domain_label(change)
        buckets[label].append(change)

    promotable = [
        (label, items)
        for label, items in buckets.items()
        if label != "General" and len(items) >= min_domain_size
    ]
    if not promotable:
        return grouped_changes

    promotable.sort(key=lambda item: (-len(item[1]), item[0].lower()))
    promoted = promotable[:max_domains]
    promoted_labels = {label for label, _ in promoted}

    remainder: list[dict[str, object]] = []
    for label, items in buckets.items():
        if label in promoted_labels:
            continue
        remainder.extend(items)

    transformed: dict[str, list[dict[str, object]]] = {}
    for section_name, section_changes in grouped_changes.items():
        if section_name == misc_name:
            continue
        transformed[section_name] = section_changes

    for label, items in promoted:
        transformed[f"Other: {label}"] = items

    if remainder:
        transformed["Platform & Maintenance"] = remainder

    return transformed


def detect_repo_slug() -> str | None:
    for remote in ("upstream", "origin"):
        proc = subprocess.run(
            ["git", "remote", "get-url", remote],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            continue

        raw_url = (proc.stdout or "").strip()
        match = GITHUB_URL_PATTERN.search(raw_url)
        if not match:
            continue

        owner = match.group("owner")
        repo = match.group("repo")
        if owner and repo:
            return f"{owner}/{repo}"

    return None


def build_compare_url(repo_slug: str | None, from_ref: str, to_ref: str) -> str | None:
    if not repo_slug:
        return None
    return (
        f"https://github.com/{repo_slug}/compare/"
        f"{quote(from_ref, safe='')}...{quote(to_ref, safe='')}"
    )


def build_commit_url(repo_slug: str | None, sha: str) -> str | None:
    if not repo_slug or not sha:
        return None
    return f"https://github.com/{repo_slug}/commit/{sha}"


def build_pr_url(repo_slug: str | None, pr_number: int | None) -> str | None:
    if not repo_slug or pr_number is None:
        return None
    return f"https://github.com/{repo_slug}/pull/{pr_number}"


def format_change_ref(change: dict[str, object], repo_slug: str | None) -> str:
    pr_number = change.get("pr_number")
    if isinstance(pr_number, int):
        pr_url = build_pr_url(repo_slug, pr_number)
        if pr_url:
            return f"[#{pr_number}]({pr_url})"
        return f"#{pr_number}"

    short_sha = str(change.get("sha", ""))[:8] or "unknown"
    commit_url = build_commit_url(repo_slug, str(change.get("sha", "")))
    if commit_url:
        return f"[commit {short_sha}]({commit_url})"
    return f"commit {short_sha}"


def build_change_why(change: dict[str, object], section_name: str) -> str:
    change_type = str(change.get("type", "misc")).lower()
    if change_type == "security" and section_name != "Security":
        conventional_type = str(change.get("conventional_type", "")).lower()
        if conventional_type in TYPE_WHY_MAP:
            change_type = conventional_type

    type_reason = TYPE_WHY_MAP.get(change_type, TYPE_WHY_MAP["misc"])
    if section_name.startswith("Other:"):
        return f"extends coverage in the {section_name.split(':', 1)[1].strip()} surface and {type_reason}"
    return type_reason


def build_section_narrative(
    *,
    section_name: str,
    section_changes: list[dict[str, object]],
) -> str:
    count = len(section_changes)
    type_counts = Counter(str(change.get("type", "misc")) for change in section_changes)
    security_count = sum(1 for change in section_changes if bool(change.get("security")))

    composition_bits: list[str] = []
    if type_counts.get("feat", 0):
        composition_bits.append(f"{type_counts['feat']} feature updates")
    if type_counts.get("fix", 0):
        composition_bits.append(f"{type_counts['fix']} reliability fixes")
    if security_count:
        composition_bits.append(f"{security_count} security-related changes")

    composition = ", ".join(composition_bits) if composition_bits else "mixed maintenance work"
    theme = SECTION_THEME_HINTS.get(section_name, "iterative product and platform improvement")

    return (
        f"{section_name} had {count} commits in this range, focused on {theme}. "
        f"The work mix included {composition}."
    )


def extract_github_handle(author_name: str, author_email: str) -> str | None:
    lowered_email = author_email.strip().lower()
    if lowered_email.endswith("@users.noreply.github.com"):
        local = lowered_email.split("@", 1)[0]
        if "+" in local:
            return local.split("+", 1)[1]
        return local

    normalized_name = author_name.strip()
    if re.fullmatch(r"[A-Za-z0-9-]{2,39}", normalized_name):
        return normalized_name.lower()

    return None


def build_contributor_identity_key(
    author_name: str,
    author_email: str,
    handle: str | None = None,
) -> str | None:
    resolved_handle = (handle or extract_github_handle(author_name, author_email) or "").strip()
    normalized_handle = normalize_identity(resolved_handle)
    if normalized_handle:
        return f"gh:{normalized_handle}"

    normalized_email = normalize_identity(author_email)
    if normalized_email:
        return f"mail:{normalized_email}"

    normalized_name = normalize_identity(author_name)
    if normalized_name:
        return f"name:{normalized_name}"

    return None


def is_bot_author(author_name: str, author_email: str) -> bool:
    lowered_name = author_name.strip().lower()
    lowered_email = author_email.strip().lower()

    if "[bot]" in lowered_name or "[bot]" in lowered_email:
        return True

    bot_markers = (
        "zeroclaw bot",
        "zeroclaw runner",
        "dependabot",
        "github-actions",
    )
    return any(marker in lowered_name or marker in lowered_email for marker in bot_markers)


def identity_tokens(author_name: str, author_email: str, handle: str | None) -> set[str]:
    tokens = {
        normalize_identity(author_name),
        normalize_identity(author_email),
        normalize_identity(author_email.split("@", 1)[0]),
    }
    if handle:
        tokens.add(normalize_identity(handle))

    lower_name = author_name.lower().strip()
    if lower_name:
        tokens.add(normalize_identity(lower_name.replace(" ", "")))

    return {token for token in tokens if token}


def build_core_tokens(core_devs: list[str]) -> set[str]:
    tokens: set[str] = set()
    for raw_identity in core_devs:
        normalized = normalize_identity(raw_identity)
        if not normalized:
            continue

        tokens.add(normalized)
        aliases = CORE_TEAM_ALIASES.get(raw_identity.lower())
        if aliases:
            for alias in aliases:
                alias_token = normalize_identity(alias)
                if alias_token:
                    tokens.add(alias_token)

    return tokens


def resolve_external_contributor(
    change: dict[str, object],
    *,
    core_tokens: set[str],
) -> dict[str, str] | None:
    author_name = str(change.get("author_name", "")).strip() or "Unknown"
    author_email = str(change.get("author_email", "")).strip()
    if is_bot_author(author_name, author_email):
        return None

    handle = extract_github_handle(author_name, author_email)
    if not handle:
        handle = str(change.get("pr_author_login", "")).strip()
    tokens = identity_tokens(author_name, author_email, handle)
    if tokens & core_tokens:
        return None

    identity_key = build_contributor_identity_key(author_name, author_email, handle)
    if not identity_key:
        return None

    return {
        "key": identity_key,
        "display": f"@{handle}" if handle else author_name,
    }


def collect_repo_history_author_changes() -> list[dict[str, object]]:
    fmt = "%H%x1f%an%x1f%ae%x1e"
    proc = subprocess.run(
        ["git", "log", "--no-merges", "--reverse", f"--pretty=format:{fmt}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or "git log failed without stderr")

    history_changes: list[dict[str, object]] = []
    for raw_record in proc.stdout.split("\x1e"):
        record = raw_record.strip()
        if not record:
            continue

        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue

        sha, author_name, author_email = parts
        history_changes.append(
            {
                "sha": sha,
                "author_name": author_name.strip(),
                "author_email": author_email.strip(),
            }
        )
    return history_changes


def compute_earliest_commit_by_identity(
    changes: list[dict[str, object]],
    *,
    core_devs: list[str],
) -> dict[str, str]:
    core_tokens = build_core_tokens(core_devs)
    earliest_by_identity: dict[str, str] = {}

    for change in changes:
        resolved = resolve_external_contributor(change, core_tokens=core_tokens)
        if not resolved:
            continue

        identity_key = resolved["key"]
        sha = str(change.get("sha", "")).strip()
        if not sha:
            continue

        if identity_key not in earliest_by_identity:
            earliest_by_identity[identity_key] = sha

    return earliest_by_identity


def collect_earliest_commit_by_identity(*, core_devs: list[str]) -> dict[str, str]:
    history_changes = collect_repo_history_author_changes()
    return compute_earliest_commit_by_identity(history_changes, core_devs=core_devs)


def build_external_contributors(
    changes: list[dict[str, object]],
    *,
    core_devs: list[str],
) -> list[dict[str, object]]:
    core_tokens = build_core_tokens(core_devs)
    aggregate: dict[str, dict[str, object]] = {}

    for change in changes:
        resolved = resolve_external_contributor(change, core_tokens=core_tokens)
        if not resolved:
            continue

        key = resolved["key"]
        display = resolved["display"]

        entry = aggregate.get(key)
        if entry is None:
            aggregate[key] = {
                "display": display,
                "commits": 1,
            }
        else:
            entry["commits"] = int(entry["commits"]) + 1

    contributors = list(aggregate.values())
    contributors.sort(key=lambda item: (-int(item["commits"]), str(item["display"]).lower()))
    return contributors


def select_first_time_contributors(
    changes: list[dict[str, object]],
    *,
    earliest_by_identity: dict[str, str],
    core_devs: list[str],
) -> list[dict[str, object]]:
    core_tokens = build_core_tokens(core_devs)
    aggregate: dict[str, dict[str, object]] = {}

    for change in changes:
        resolved = resolve_external_contributor(change, core_tokens=core_tokens)
        if not resolved:
            continue

        key = resolved["key"]
        sha = str(change.get("sha", "")).strip()
        earliest_sha = str(earliest_by_identity.get(key, "")).strip()
        if not sha or not earliest_sha or sha != earliest_sha:
            continue

        entry = aggregate.get(key)
        if entry is None:
            aggregate[key] = {
                "display": resolved["display"],
                "commits": 1,
            }
        else:
            entry["commits"] = int(entry["commits"]) + 1

    contributors = list(aggregate.values())
    contributors.sort(key=lambda item: (-int(item["commits"]), str(item["display"]).lower()))
    return contributors


def fetch_associated_pr_for_commit(repo_slug: str, sha: str) -> dict[str, object] | None:
    proc = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repo_slug}/commits/{sha}/pulls",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None

    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload:
        return None

    candidates = [item for item in payload if isinstance(item, dict)]
    if not candidates:
        return None

    preferred = next((item for item in candidates if item.get("merged_at")), candidates[0])
    number = preferred.get("number")
    user = preferred.get("user")
    if not isinstance(number, int):
        return None

    author_login = ""
    if isinstance(user, dict):
        author_login = str(user.get("login", "")).strip()
    return {
        "number": number,
        "author_login": author_login,
    }


def load_pr_cache(path: Path | None) -> dict[str, dict[str, object] | None]:
    if not path:
        return {}
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    normalized: dict[str, dict[str, object] | None] = {}
    for sha, value in data.items():
        if not isinstance(sha, str):
            continue
        if value is None:
            normalized[sha] = None
            continue
        if isinstance(value, dict):
            number = value.get("number")
            if isinstance(number, int):
                normalized[sha] = {
                    "number": number,
                    "author_login": str(value.get("author_login", "")).strip(),
                }
    return normalized


def save_pr_cache(path: Path | None, cache: dict[str, dict[str, object] | None]) -> None:
    if not path:
        return

    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    payload = {sha: value for sha, value in cache.items()}
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def default_pr_cache_path() -> Path:
    cache_base = os.environ.get("XDG_CACHE_HOME")
    if not cache_base:
        cache_base = str(Path.home() / ".cache")
    return Path(cache_base) / "zeroclaw-release-report" / "pr-cache.json"


def enrich_changes_with_associated_prs(
    changes: list[dict[str, object]],
    *,
    repo_slug: str | None,
    cache_path: Path | None = None,
) -> None:
    if not repo_slug or not changes:
        return
    if shutil.which("gh") is None:
        return

    cache = load_pr_cache(cache_path)
    cache_changed = False

    for change in changes:
        if isinstance(change.get("pr_number"), int):
            continue

        sha = str(change.get("sha", "")).strip()
        if not sha:
            continue

        if sha not in cache:
            cache[sha] = fetch_associated_pr_for_commit(repo_slug, sha)
            cache_changed = True
        associated = cache[sha]
        if not associated:
            continue

        number = associated.get("number")
        if isinstance(number, int):
            change["pr_number"] = number
        author_login = str(associated.get("author_login", "")).strip()
        if author_login:
            change["pr_author_login"] = author_login
    if cache_changed:
        save_pr_cache(cache_path, cache)


def summarize_external_contributors(contributors: list[dict[str, object]], *, limit: int = 12) -> str:
    if not contributors:
        return "Community contributions were present in this range, but no external contributor identities were resolved from commit metadata."

    visible = contributors[:limit]
    rendered = [f"{item['display']} ({item['commits']})" for item in visible]
    overflow = len(contributors) - len(visible)
    if overflow > 0:
        rendered.append(f"and {overflow} more")
    return ", ".join(rendered)


def render_markdown(
    *,
    version: str,
    generated_at: str,
    grouped_changes: dict[str, list[dict[str, object]]],
    highlights: list[dict[str, object]] | None = None,
    section_stats: list[dict[str, object]] | None = None,
    release_version: str | None = None,
    repo_slug: str | None = None,
    compare_url: str | None = None,
    external_contributors: list[dict[str, object]] | None = None,
    first_time_contributors: list[dict[str, object]] | None = None,
    must_promote_terms: list[str] | None = None,
    main_area_flow: list[dict[str, object]] | None = None,
    main_ref_mode: str = "hybrid",
    appendix_hint: str | None = None,
    highlight_limit: int = 6,
) -> str:
    lines: list[str] = []

    def build_area_summary(section_changes: list[dict[str, object]]) -> str:
        count = len(section_changes)
        type_counts = Counter(str(change.get("type", "misc")) for change in section_changes)
        security_count = sum(1 for change in section_changes if bool(change.get("security")))

        focus_bits: list[str] = []
        if type_counts.get("feat", 0):
            focus_bits.append(f"{type_counts['feat']} features")
        if type_counts.get("fix", 0):
            focus_bits.append(f"{type_counts['fix']} fixes")
        if security_count:
            focus_bits.append(f"{security_count} security-related updates")

        if focus_bits:
            return f"This area includes {count} commits focused on {', '.join(focus_bits[:2])}."
        return f"This area includes {count} commits."

    def commit_count_label(item: dict[str, object]) -> str:
        raw_commits = item.get("commits", 1)
        try:
            commits = int(raw_commits)
        except (TypeError, ValueError):
            commits = 1
        commits = max(1, commits)
        commit_word = "commit" if commits == 1 else "commits"
        return f"{commits} {commit_word}"

    def contributor_display_ref(display: str) -> str:
        trimmed = display.strip()
        if not trimmed:
            return "Unknown contributor"
        if trimmed.startswith("["):
            return trimmed
        if not trimmed.startswith("@"):
            return trimmed

        handle = trimmed[1:].strip()
        if not handle:
            return trimmed
        return f"[{trimmed}](https://github.com/{quote(handle, safe='')})"

    intro_parts = [
        f"Release range: `{version}`.",
        f"Generated: `{generated_at}`.",
    ]
    if compare_url:
        intro_parts.append(f"Compare: [{version}]({compare_url}).")
    if appendix_hint:
        intro_parts.append(f"Appendix: `{appendix_hint}`.")
    lines.append(" ".join(intro_parts))
    lines.append("")

    if section_stats is None:
        section_stats = build_section_stats(grouped_changes)

    total_changes = len(iter_grouped_changes(grouped_changes))
    total_security = sum(1 for change in iter_grouped_changes(grouped_changes) if bool(change.get("security")))
    total_breaking = sum(1 for change in iter_grouped_changes(grouped_changes) if bool(change.get("breaking")))
    top_areas = sorted(grouped_changes.items(), key=lambda item: (-len(item[1]), item[0].lower()))[:3]
    top_area_text = ", ".join(f"{name} ({len(items)})" for name, items in top_areas) if top_areas else "none"
    external_count = len(external_contributors or [])
    first_time_count = len(first_time_contributors or [])

    lines.append(
        f"This release brings {total_changes} non-merge commits across {len(grouped_changes)} areas, led by "
        f"{top_area_text}, with {total_security} security-related updates and {total_breaking} breaking changes; "
        f"community delivery includes {external_count} external contributors, including {first_time_count} first-time contributors."
    )
    lines.append("")

    if grouped_changes:
        resolved_area_flow = main_area_flow or build_main_report_area_flow(
            grouped_changes,
            must_promote_terms=must_promote_terms or [],
        )
        for flow_item in resolved_area_flow:
            section_name = str(flow_item.get("section", "")).strip()
            section_changes = flow_item.get("section_changes", [])
            compound_changes = flow_item.get("compound_groups", [])
            if not section_name or not isinstance(section_changes, list):
                continue
            lines.append(f"## {section_name}")
            lines.append("")
            lines.append(build_area_summary(section_changes))
            lines.append("")
            if isinstance(compound_changes, list):
                for compound in compound_changes:
                    if isinstance(compound, list) and compound:
                        lines.append(
                            f"- {render_compound_change_sentence(compound, repo_slug=repo_slug, ref_mode=main_ref_mode)}"
                        )
            lines.append("")

    lines.append("## Community Contributors")
    lines.append("")
    if external_contributors:
        for item in external_contributors:
            display = str(item.get("display", "")).strip() or "Unknown contributor"
            lines.append(f"- {contributor_display_ref(display)} contributed {commit_count_label(item)} in this release.")
    else:
        lines.append("- No external contributors were detected in this range after core-team filtering.")
    lines.append("")

    lines.append("## Special Thanks: First-Time Contributors")
    lines.append("")
    if first_time_contributors:
        for item in first_time_contributors:
            display = str(item.get("display", "")).strip() or "Unknown contributor"
            lines.append(
                f"- {contributor_display_ref(display)} made their first contribution in this release ({commit_count_label(item)})."
            )
    else:
        lines.append("- No first-time contributors were identified in this range.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_appendix_markdown(
    *,
    version: str,
    generated_at: str,
    grouped_changes: dict[str, list[dict[str, object]]],
    section_stats: list[dict[str, object]],
    release_version: str | None,
    repo_slug: str | None,
    compare_url: str | None,
) -> str:
    lines: list[str] = []
    normalized_release = (release_version or "").strip()
    if normalized_release and not normalized_release.startswith("v"):
        normalized_release = f"v{normalized_release}"

    title = "# ZeroClaw Release Source Appendix"
    if normalized_release:
        title = f"# ZeroClaw {normalized_release} Release Source Appendix"

    lines.append(title)
    lines.append("")
    lines.append(f"Version Range: `{version}`")
    lines.append(f"Generated: `{generated_at}`")
    if compare_url:
        lines.append(f"Compare: [{version}]({compare_url})")
    lines.append("")

    total_changes = len(iter_grouped_changes(grouped_changes))
    lines.append("## Coverage Summary")
    lines.append("")
    lines.append(f"- Total non-merge commits: {total_changes}")
    lines.append(f"- Total sections: {len(grouped_changes)}")
    lines.append("")

    if section_stats:
        lines.append("| Section | Commits | Security | Breaking | Impact |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for entry in section_stats:
            lines.append(
                f"| {entry['section']} | {entry['count']} | {entry['security']} | {entry['breaking']} | {entry['impact']} |"
            )
        lines.append("")

    lines.append("## Source-Mapped Changelog")
    lines.append("")
    for section_name, section_changes in grouped_changes.items():
        lines.append(f"### {section_name}")
        lines.append("")
        for change in section_changes:
            author_name = str(change.get("author_name", "")).strip() or "Unknown"
            ref = format_change_ref(change, repo_slug)
            author_ref = format_author_ref(change)
            author_display = author_ref if author_ref else author_name
            lines.append(f"- {change['title']} ({ref}; by {author_display})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _clip_tweet(text: str, *, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def render_twitter_markdown(
    *,
    release_version: str | None,
    version: str,
    highlights: list[dict[str, object]],
    section_stats: list[dict[str, object]],
    compare_url: str | None,
    external_contributors: list[dict[str, object]],
) -> str:
    normalized_release = (release_version or "").strip()
    if normalized_release and not normalized_release.startswith("v"):
        normalized_release = f"v{normalized_release}"

    headline = normalized_release or "latest"
    preferred_sections = [
        str(entry["section"])
        for entry in section_stats
        if str(entry["section"]) not in {"Platform & Maintenance", "Misc"}
        and not str(entry["section"]).startswith("Other:")
    ]
    top_sections = preferred_sections[:3]
    sections_text = ", ".join(top_sections) if top_sections else "security, channel UX, and release reliability"

    thanks_people = [str(item.get("display", "")).strip() for item in external_contributors[:4] if str(item.get("display", "")).strip()]

    single_post = _clip_tweet(
        f"ZeroClaw {headline} is out. This release sharpens security defaults, upgrades multi-channel UX, "
        f"and hardens release reliability across {sections_text}. "
        f"Full notes: {compare_url or version}."
    )

    thread_1 = _clip_tweet(
        f"1/4 ZeroClaw {headline} is live. This cycle focused on safer defaults, smoother channel workflows, and stronger release quality."
    )
    thread_2 = _clip_tweet(
        f"2/4 Security and trust upgrades landed across guardrails, auth boundaries, and policy enforcement."
    )
    thread_3 = _clip_tweet(
        f"3/4 We improved provider/model reliability plus channel UX, while tightening CI and release gates for safer upgrades."
    )
    thread_4 = _clip_tweet(
        f"4/4 Thanks to contributors outside core dev for helping ship {headline}. "
        f"{('Notable: ' + ', '.join(thanks_people) + '. ') if thanks_people else ''}"
        f"Details: {compare_url or version}"
    )

    lines = [
        f"# ZeroClaw {headline} Twitter Draft" if normalized_release else "# ZeroClaw Twitter Draft",
        "",
        "## Single Post",
        "",
        single_post,
        "",
        "## Thread Option",
        "",
        thread_1,
        "",
        thread_2,
        "",
        thread_3,
        "",
        thread_4,
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def infer_release_version(from_ref: str, to_ref: str, explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()

    if re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", to_ref):
        return to_ref
    if re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", from_ref):
        return to_ref
    return None


def relative_path_hint(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base.parent))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release report markdown")
    parser.add_argument("--from", dest="from_ref", required=True)
    parser.add_argument("--to", dest="to_ref", default="HEAD")
    parser.add_argument("--out", required=True)
    parser.add_argument("--appendix-out", dest="appendix_out")
    parser.add_argument("--twitter-out", dest="twitter_out")
    parser.add_argument("--sources-json", dest="sources_json")
    parser.add_argument("--stage-dir", dest="stage_dir")
    parser.add_argument(
        "--pr-cache",
        dest="pr_cache",
        help="Optional commit-to-PR cache file path. Defaults to --stage-dir/pr-cache.json, or "
             f"{default_pr_cache_path()} if stage-dir is not set.",
    )
    parser.add_argument("--override-json", dest="override_json")
    parser.add_argument("--release-version", dest="release_version")
    parser.add_argument("--repo-slug", dest="repo_slug")
    parser.add_argument(
        "--main-ref-mode",
        dest="main_ref_mode",
        choices=("hybrid", "pr_only"),
        default="hybrid",
    )
    parser.add_argument("--core-dev", action="append", dest="core_devs", default=[])
    parser.add_argument("--thanks-limit", dest="thanks_limit", type=int, default=12)
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

    repo_slug = args.repo_slug or detect_repo_slug()
    cache_path = Path(args.pr_cache) if args.pr_cache else None
    if cache_path is None and args.stage_dir:
        cache_path = Path(args.stage_dir) / "pr-cache.json"
    if cache_path is None:
        cache_path = default_pr_cache_path()
    enrich_changes_with_associated_prs(changes, repo_slug=repo_slug, cache_path=cache_path)

    try:
        taxonomy = load_taxonomy(DEFAULT_TAXONOMY_PATH)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"failed to load taxonomy: {error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    try:
        override = load_override(args.override_json)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"failed to load override: {error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    grouped_changes_raw = group_changes(changes, taxonomy)
    grouped_changes = expand_misc_domains(grouped_changes_raw)
    highlights = select_highlights(
        grouped_changes=grouped_changes,
        changes=changes,
        override=override,
    )

    section_stats = build_section_stats(grouped_changes)
    compare_url = build_compare_url(repo_slug, args.from_ref, args.to_ref)

    core_devs = [dev.strip() for dev in args.core_devs if dev.strip()] or list(DEFAULT_CORE_DEVS)
    external_contributors = build_external_contributors(changes, core_devs=core_devs)
    try:
        earliest_by_identity = collect_earliest_commit_by_identity(core_devs=core_devs)
    except RuntimeError as error:
        print(f"git failed while collecting repository history: {error}", file=sys.stderr)
        return EXIT_GIT_FAILURE

    first_time_contributors = select_first_time_contributors(
        changes,
        earliest_by_identity=earliest_by_identity,
        core_devs=core_devs,
    )

    release_version = infer_release_version(args.from_ref, args.to_ref, args.release_version)
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    must_promote_terms = [
        str(item).strip()
        for item in override.get("must_promote", [])
        if str(item).strip()
    ]
    main_area_flow = build_main_report_area_flow(
        grouped_changes,
        must_promote_terms=must_promote_terms,
    )

    out_path = Path(args.out)
    appendix_hint: str | None = None
    if args.appendix_out:
        appendix_hint = relative_path_hint(Path(args.appendix_out), out_path)

    rendered_markdown = render_markdown(
        version=f"{args.from_ref}..{args.to_ref}",
        generated_at=generated_at,
        grouped_changes=grouped_changes,
        highlights=highlights,
        section_stats=section_stats,
        release_version=release_version,
        repo_slug=repo_slug,
        compare_url=compare_url,
        external_contributors=external_contributors[: max(1, args.thanks_limit)],
        first_time_contributors=first_time_contributors[: max(1, args.thanks_limit)],
        must_promote_terms=must_promote_terms,
        main_area_flow=main_area_flow,
        main_ref_mode=args.main_ref_mode,
        appendix_hint=appendix_hint,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered_markdown, encoding="utf-8")

    if args.appendix_out:
        appendix_path = Path(args.appendix_out)
        appendix_path.parent.mkdir(parents=True, exist_ok=True)
        appendix_path.write_text(
            render_appendix_markdown(
                version=f"{args.from_ref}..{args.to_ref}",
                generated_at=generated_at,
                grouped_changes=grouped_changes,
                section_stats=section_stats,
                release_version=release_version,
                repo_slug=repo_slug,
                compare_url=compare_url,
            ),
            encoding="utf-8",
        )

    if args.twitter_out:
        twitter_path = Path(args.twitter_out)
        twitter_path.parent.mkdir(parents=True, exist_ok=True)
        twitter_path.write_text(
            render_twitter_markdown(
                release_version=release_version,
                version=f"{args.from_ref}..{args.to_ref}",
                highlights=highlights,
                section_stats=section_stats,
                compare_url=compare_url,
                external_contributors=external_contributors[: max(1, args.thanks_limit)],
            ),
            encoding="utf-8",
        )

    if args.stage_dir:
        write_workflow_stage_artifacts(
            stage_dir=Path(args.stage_dir),
            version=f"{args.from_ref}..{args.to_ref}",
            generated_at=generated_at,
            must_promote_terms=must_promote_terms,
            area_flow=main_area_flow,
            repo_slug=repo_slug,
            main_ref_mode=args.main_ref_mode,
        )

    if args.sources_json:
        sources_path = Path(args.sources_json)
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": {
                "from": args.from_ref,
                "to": args.to_ref,
                "release": release_version,
                "generated_at": generated_at,
                "main_ref_mode": args.main_ref_mode,
            },
            "repo": {
                "slug": repo_slug,
                "compare_url": compare_url,
            },
            "changes": changes,
            "grouped": grouped_changes,
            "highlights": highlights,
            "section_stats": section_stats,
            "external_contributors": external_contributors,
            "first_time_contributors": first_time_contributors,
            "earliest_external_commit_by_identity": earliest_by_identity,
            "core_devs": core_devs,
            "main_area_flow": [
                {
                    "section": str(item.get("section", "")),
                    "area_commit_count": len(item.get("section_changes", []))
                    if isinstance(item.get("section_changes", []), list)
                    else 0,
                    "selected_changes": item.get("selected_changes", [])
                    if isinstance(item.get("selected_changes", []), list)
                    else [],
                    "compound_groups": item.get("compound_groups", [])
                    if isinstance(item.get("compound_groups", []), list)
                    else [],
                }
                for item in main_area_flow
            ],
        }
        sources_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
