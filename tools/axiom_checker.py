#!/usr/bin/env python3
# ---
# name: axiom-checker
# description: Check Lean 4 files for forbidden axiom/constant/postulate declarations
# input:
#   path:
#     type: string
#     description: Path to a .lean file or directory to check
#     required: true
# output: json
# ---
"""Check a Lean 4 file for forbidden axiom/constant/postulate declarations.

Ensures a proof doesn't secretly introduce global axioms to close goals.
Also checks for `sorry` and `admit` that might have survived.

Usage:
    python3 tools/axiom_checker.py LeanFormalizations/problem_foo_proven.lean
    python3 tools/axiom_checker.py LeanFormalizations/  # scan directory
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from _lean_masking import mask_lean_comments_and_strings


_ATTR_FRAGMENT = r"(?:@\[(?:[^\]\[]|\[[^\]]*\])*\]\s*)*"
_DECL_NAME = r"([^\s:({\[]+)"
_PLACEHOLDER_RE = r"(?<![\w'])(sorry|admit)(?![\w'])"
_FORBIDDEN_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    rf"(?:axiom|constant|postulate)\s+{_DECL_NAME}",
    re.MULTILINE,
)
_SORRY_RE = re.compile(_PLACEHOLDER_RE)
_NONCOMPUTABLE_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|local|unsafe|partial)\s+)*"
    r"noncomputable\s+"
    rf"{_ATTR_FRAGMENT}(?:(?:private|protected|local|unsafe|partial)\s+)*"
    rf"(?:def|instance)\s+{_DECL_NAME}",
    re.MULTILINE,
)


def iter_lean_files(directory: Path):
    """Yield Lean files recursively, matching the suffix case-insensitively."""
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".lean":
            yield path


def check_file(path: Path) -> dict:
    """Check a single file for forbidden content."""
    text = mask_lean_comments_and_strings(path.read_text(encoding="utf-8"))

    # Pre-calculate line offsets for O(n) line-number lookup
    # instead of O(n²) from repeated text[:pos].count("\n").
    _line_starts: list[int] = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            _line_starts.append(i + 1)

    def _line_of(pos: int) -> int:
        lo, hi = 0, len(_line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    issues: list[dict] = []

    for match in _FORBIDDEN_RE.finditer(text):
        line_no = _line_of(match.start())
        issues.append({
            "line": line_no,
            "severity": "critical",
            "type": "forbidden_declaration",
            "name": match.group(1),
            "message": f"Forbidden `{match.group(0).strip()}` — proof must not introduce axioms",
        })

    for match in _SORRY_RE.finditer(text):
        line_no = _line_of(match.start())
        issues.append({
            "line": line_no,
            "severity": "critical",
            "type": "placeholder",
            "name": match.group(1),
            "message": f"Proof placeholder `{match.group(1)}` still present",
        })

    for match in _NONCOMPUTABLE_RE.finditer(text):
        line_no = _line_of(match.start())
        issues.append({
            "line": line_no,
            "severity": "info",
            "type": "noncomputable",
            "name": match.group(1),
            "message": f"Noncomputable declaration `{match.group(1)}` — check if intentional",
        })

    return {
        "file": str(path),
        "clean": len([i for i in issues if i["severity"] == "critical"]) == 0,
        "issue_count": len(issues),
        "issues": issues,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 tools/axiom_checker.py <file_or_dir>", file=sys.stderr)
        return 1

    target = Path(sys.argv[1])

    if target.is_file():
        if target.suffix.lower() != ".lean":
            print(f"Error: {target} is not a .lean file", file=sys.stderr)
            return 1
        try:
            result = check_file(target)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: failed to read {target}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result["clean"] else 1
    elif target.is_dir():
        dirty = 0
        for f in sorted(iter_lean_files(target)):
            try:
                r = check_file(f)
            except (OSError, UnicodeDecodeError) as exc:
                print(f"Error: failed to read {f}: {exc}", file=sys.stderr)
                return 1
            if not r["clean"]:
                print(json.dumps(r, indent=2))
                dirty += 1
        if dirty == 0:
            print(json.dumps({"status": "all_clean", "directory": str(target)}))
        return 0 if dirty == 0 else 1
    else:
        print(f"Error: {target} not found", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
