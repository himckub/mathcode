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
    python3 tools/proof_stats.py LeanFormalizations/problem_foo_proven.lean
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
_THEOREM_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    rf"(?:theorem|lemma)\s+{_DECL_NAME}",
    re.MULTILINE,
)
_DEF_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    rf"(?:def|instance)\s+{_DECL_NAME}",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$", re.MULTILINE)
_TACTIC_RE = re.compile(
    r"(?<![\w'.«])(simp|rfl|ring|omega|linarith|nlinarith|norm_num|aesop|decide|"
    r"exact|apply|rw|intro|constructor|cases|rcases|obtain|have|let|"
    r"suffices|calc|induction|ext|funext|congr|convert|refine|use|"
    r"trivial|tauto|contradiction|exfalso|push_neg|by_contra|"
    r"field_simp|ring_nf|norm_cast|push_cast|simpa|rwa)(?![\w'»])",
    re.MULTILINE,
)
_TOP_LEVEL_DECL_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    r"(?:theorem|lemma|example|def|instance|axiom|constant|postulate|inductive|structure|class)\b",
    re.MULTILINE,
)
_THEOREM_START_RE = re.compile(
    rf"^\s*{_ATTR_FRAGMENT}(?:(?:private|protected|noncomputable|local|unsafe|partial)\s+)*"
    r"(?:theorem|lemma)\b"
)
_BY_LINE_RE = re.compile(r"^\s*by\b")
_TERM_LOCAL_BINDING_RE = re.compile(r"(?<![\w'«])(?:let|have|suffices)(?![\w'»])")


def _line_indent_at(text: str, index: int) -> int:
    line_start = text.rfind("\n", 0, index) + 1
    indent = 0
    while line_start + indent < len(text) and text[line_start + indent] in " \t":
        indent += 1
    return indent


def _line_is_blank_at(text: str, index: int) -> bool:
    line_start = text.rfind("\n", 0, index) + 1
    line_end = text.find("\n", index)
    if line_end == -1:
        line_end = len(text)
    return not text[line_start:line_end].strip()


def _next_nonblank_line_indent(text: str, index: int) -> int | None:
    line_start = text.find("\n", index)
    while line_start != -1:
        line_start += 1
        line_end = text.find("\n", line_start)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if line.strip():
            return len(line) - len(line.lstrip(" \t"))
        line_start = text.find("\n", line_end)
    return None


def _find_next_top_level_semicolon_on_line(text: str, start: int) -> int | None:
    depth = 0
    opener_to_closer = {"(": ")", "[": "]", "{": "}"}
    closers = set(opener_to_closer.values())

    for index in range(start, len(text)):
        char = text[index]
        if char == "\n":
            return None
        if char == ";" and depth == 0:
            return index
        if char in opener_to_closer:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1

    return None


def _line_has_code_after(text: str, index: int) -> bool:
    line_end = text.find("\n", index)
    if line_end == -1:
        line_end = len(text)
    return bool(text[index:line_end].strip())


def _find_tactic_proof_start(text: str) -> int | None:
    depth = 0
    local_assignment_pending = False
    skip_until_index: int | None = None
    skip_min_indent: int | None = None
    skip_min_start_index: int | None = None
    opener_to_closer = {"(": ")", "[": "]", "{": "}"}
    closers = set(opener_to_closer.values())

    for index, char in enumerate(text):
        if skip_until_index is not None and index >= skip_until_index:
            skip_until_index = None
        skipping_by_indent = False
        if (
            skip_min_indent is not None
            and skip_min_start_index is not None
            and index >= skip_min_start_index
        ):
            if (
                not _line_is_blank_at(text, index)
                and _line_indent_at(text, index) < skip_min_indent
            ):
                skip_min_indent = None
                skip_min_start_index = None
            else:
                skipping_by_indent = True
        skipping_local_binding_body = skip_until_index is not None or skipping_by_indent

        if text.startswith(":=", index) and depth == 0:
            if skipping_local_binding_body:
                continue
            proof_candidate = text[index + 2 :]
            if local_assignment_pending:
                skip_until_index = _find_next_top_level_semicolon_on_line(
                    text,
                    index + 2,
                )
                by_match = _BY_LINE_RE.match(proof_candidate)
                if by_match:
                    after_by_index = index + 2 + by_match.end()
                    if _line_has_code_after(text, after_by_index):
                        line_end = text.find("\n", after_by_index)
                        if line_end != -1 and skip_until_index is None:
                            skip_until_index = line_end
                    else:
                        skip_min_indent = _next_nonblank_line_indent(
                            text,
                            after_by_index,
                        )
                        next_line_index = text.find("\n", after_by_index)
                        skip_min_start_index = (
                            next_line_index + 1 if next_line_index != -1 else None
                        )
                local_assignment_pending = False
                continue

            by_match = _BY_LINE_RE.match(proof_candidate)
            if by_match:
                return index + 2 + by_match.end()
            return None
        if (
            depth == 0
            and not skipping_local_binding_body
            and _TERM_LOCAL_BINDING_RE.match(text, index)
        ):
            local_assignment_pending = True
        if char in opener_to_closer:
            depth += 1
        elif char in closers and depth > 0:
            depth -= 1

    return None


def _extract_tactic_contexts(searchable_text: str) -> str:
    """Return theorem/lemma proof text that starts at `:= by`."""
    contexts: list[str] = []
    declarations = list(_TOP_LEVEL_DECL_RE.finditer(searchable_text))

    for index, match in enumerate(declarations):
        start = match.start()
        end = (
            declarations[index + 1].start()
            if index + 1 < len(declarations)
            else len(searchable_text)
        )
        declaration = searchable_text[start:end]
        if _THEOREM_START_RE.match(declaration) is None:
            continue

        proof_start = _find_tactic_proof_start(declaration)
        if proof_start is None:
            continue

        contexts.append(declaration[proof_start:])

    return "\n".join(contexts)


def analyze_file(path: Path) -> dict:
    """Compute statistics for a Lean file."""
    text = path.read_text(encoding="utf-8")
    searchable_text = mask_lean_comments_and_strings(text)
    lines = text.splitlines()

    theorems = [m.group(1) for m in _THEOREM_RE.finditer(searchable_text)]
    defs = [m.group(1) for m in _DEF_RE.finditer(searchable_text)]
    imports = [m.group(1).strip() for m in _IMPORT_RE.finditer(searchable_text)]

    # Tactic frequency
    tactic_counts: dict[str, int] = {}
    proof_text = _extract_tactic_contexts(searchable_text)
    for match in _TACTIC_RE.finditer(proof_text):
        tac = match.group(1)
        tactic_counts[tac] = tactic_counts.get(tac, 0) + 1

    # Sort by frequency
    sorted_tactics = sorted(tactic_counts.items(), key=lambda x: -x[1])

    has_sorry = bool(re.search(r"(?<![\w'])sorry(?![\w'])", searchable_text))
    has_admit = bool(re.search(r"(?<![\w'])admit(?![\w'])", searchable_text))

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
        print("Usage: python3 tools/proof_stats.py <lean_file>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1
    if not path.is_file() or path.suffix.lower() != ".lean":
        print(f"Error: {path} is not a .lean file", file=sys.stderr)
        return 1

    try:
        result = analyze_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Error: failed to read {path}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
