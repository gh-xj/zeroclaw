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

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("# placeholder\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
