#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


EXIT_MISSING_REF = 2
EXIT_GIT_FAILURE = 3


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate release report markdown")
    parser.add_argument("--from", dest="from_ref", required=True)
    parser.add_argument("--to", dest="to_ref", default="HEAD")
    parser.add_argument("--out", required=True)
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

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("# placeholder\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
