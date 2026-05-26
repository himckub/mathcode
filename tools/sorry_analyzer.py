#!/usr/bin/env python3
# ---
# name: sorry-analyzer
# description: Analyze sorry/admit placeholders in Lean 4 files with location and context
# input:
#   path:
#     type: string
#     description: Path to a .lean file or directory to scan
#     required: true
# output: json
# ---
"""Analyze sorry placeholders in a Lean 4 file.

Scans a .lean file for all `sorry` and `admit` tokens, reports their
locations, surrounding context, and the theorem they belong to. Useful
for understanding what remains to be proved before attempting a proof.

Usage:
    python3 tools/sorry_analyzer.py LeanFormalizations/problem_foo.lean
    python3 tools/sorry_analyzer.py LeanFormalizations/  # scan entire directory

Output format (JSON):
    {
      "file": "problem_foo.lean",
      "sorry_count": 2,
      "admit_count": 0,
      "locations": [
        {
          "line": 5,
          "column": 2,
          "token": "sorry",
          "theorem": "problem_foo",
          "context": "theorem problem_foo : True := by\\n  sorry"
        }
      ]
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from _lean_masking import mask_lean_comments_and_strings


_ATTR_FRAGMENT = r"(?:@\[(?:[^\]\[]|\[[^\]]*\])*\]\s*)*"
_DECL_NAME = r"([^\s:({\[]+)"
_SORRY_RE = re.compile(r"(?<![\w'])(sorry|admit)(?![\w'])")
_THEOREM_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    rf"(?:(?:theorem|lemma|def|instance)\s+{_DECL_NAME}|(example)\b)",
    re.MULTILINE,
)


def iter_lean_files(directory: Path):
    """Yield Lean files recursively, matching the suffix case-insensitively."""
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".lean":
            yield path


def analyze_file(path: Path) -> dict:
    """Analyze a single .lean file for sorry/admit tokens."""
    text = path.read_text(encoding="utf-8")
    searchable_text = mask_lean_comments_and_strings(text)
    lines = text.splitlines()

    # Find all theorem/def declarations and their line numbers
    decl_ranges: list[tuple[int, str]] = []
    for match in _THEOREM_RE.finditer(searchable_text):
        line_no = searchable_text[:match.start()].count("\n") + 1
        decl_ranges.append((line_no, match.group(1) or match.group(2)))

    locations: list[dict] = []
    sorry_count = 0
    admit_count = 0

    for match in _SORRY_RE.finditer(searchable_text):
        line_no = searchable_text[:match.start()].count("\n") + 1
        col = match.start() - searchable_text.rfind("\n", 0, match.start()) - 1
        token = match.group(1)

        if token == "sorry":
            sorry_count += 1
        else:
            admit_count += 1

        # Find which theorem this sorry belongs to
        theorem_name = "(top-level)"
        for decl_line, decl_name in reversed(decl_ranges):
            if decl_line <= line_no:
                theorem_name = decl_name
                break

        # Extract 2 lines of context around the sorry
        start = max(0, line_no - 2)
        end = min(len(lines), line_no + 1)
        context = "\n".join(lines[start:end])

        locations.append({
            "line": line_no,
            "column": col,
            "token": token,
            "theorem": theorem_name,
            "context": context,
        })

    return {
        "file": str(path),
        "sorry_count": sorry_count,
        "admit_count": admit_count,
        "total_placeholders": sorry_count + admit_count,
        "locations": locations,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 tools/sorry_analyzer.py <file_or_dir>", file=sys.stderr)
        return 1

    target = Path(sys.argv[1])

    if target.is_file():
        if target.suffix.lower() != ".lean":
            print(f"Error: {target} is not a .lean file", file=sys.stderr)
            return 1
        try:
            result = analyze_file(target)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: failed to read {target}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
    elif target.is_dir():
        results = []
        total_sorry = 0
        total_admit = 0
        for lean_file in sorted(iter_lean_files(target)):
            try:
                r = analyze_file(lean_file)
            except (OSError, UnicodeDecodeError) as exc:
                print(f"Error: failed to read {lean_file}: {exc}", file=sys.stderr)
                return 1
            if r["total_placeholders"] > 0:
                results.append(r)
                total_sorry += r["sorry_count"]
                total_admit += r["admit_count"]
        summary = {
            "directory": str(target),
            "files_with_placeholders": len(results),
            "total_sorry": total_sorry,
            "total_admit": total_admit,
            "files": results,
        }
        print(json.dumps(summary, indent=2))
    else:
        print(f"Error: {target} not found", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
