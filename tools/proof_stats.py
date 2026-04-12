#!/usr/bin/env python3
# ---
# name: proof-stats
# description: Report statistics about a Lean 4 file including theorem count, tactics, and complexity
# input:
#   path:
#     type: string
#     description: Path to a .lean file to analyze
#     required: true
# output: json
# ---
"""Report statistics about a proven Lean 4 file.

Shows theorem count, proof length, tactics used, imports, and complexity
metrics. Useful for understanding a proof before golfing or reviewing.

Usage:
    python3 Tools/proof_stats.py LeanFormalizations/problem_foo_proven.lean
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


_THEOREM_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?(?:protected\s+)?(?:private\s+)?"
    r"(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
    re.MULTILINE,
)
_DEF_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?(?:def|instance)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$", re.MULTILINE)
_TACTIC_RE = re.compile(
    r"\b(simp|rfl|ring|omega|linarith|nlinarith|norm_num|aesop|decide|"
    r"exact|apply|rw|intro|constructor|cases|rcases|obtain|have|let|"
    r"suffices|calc|induction|ext|funext|congr|convert|refine|use|"
    r"trivial|tauto|contradiction|exfalso|push_neg|by_contra|"
    r"field_simp|ring_nf|norm_cast|push_cast|simpa|rwa)\b"
)


def analyze_file(path: Path) -> dict:
    """Compute statistics for a Lean file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    theorems = [m.group(1) for m in _THEOREM_RE.finditer(text)]
    defs = [m.group(1) for m in _DEF_RE.finditer(text)]
    imports = [m.group(1).strip() for m in _IMPORT_RE.finditer(text)]

    # Tactic frequency
    tactic_counts: dict[str, int] = {}
    for match in _TACTIC_RE.finditer(text):
        tac = match.group(1)
        tactic_counts[tac] = tactic_counts.get(tac, 0) + 1

    # Sort by frequency
    sorted_tactics = sorted(tactic_counts.items(), key=lambda x: -x[1])

    has_sorry = bool(re.search(r"\bsorry\b", text))
    has_admit = bool(re.search(r"\badmit\b", text))

    return {
        "file": str(path),
        "lines": len(lines),
        "non_blank_lines": len([l for l in lines if l.strip()]),
        "theorems": theorems,
        "theorem_count": len(theorems),
        "definitions": defs,
        "imports": imports,
        "has_sorry": has_sorry,
        "has_admit": has_admit,
        "status": "unproven" if (has_sorry or has_admit) else "proven",
        "tactic_frequency": dict(sorted_tactics),
        "unique_tactics": len(tactic_counts),
        "total_tactic_calls": sum(tactic_counts.values()),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 Tools/proof_stats.py <lean_file>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1

    result = analyze_file(path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
