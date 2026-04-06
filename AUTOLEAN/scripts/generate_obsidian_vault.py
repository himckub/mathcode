#!/usr/bin/env python3
"""Generate an Obsidian vault from AUTOLEAN formalization and proving artifacts.

Scans formalized .lean files, proven candidates, plan logs, and summary.json
files to produce a set of interlinked Obsidian markdown notes:
  - One note per user theorem (with statement, proof, status, dependencies)
  - Stub notes for referenced Mathlib lemmas
  - An index note linking everything

Usage:
    python generate_obsidian_vault.py \
        --formalizations ./LeanFormalizations \
        --proofs ./ProofOutput \
        --vault ./ObsidianVault

Either --formalizations or --proofs (or both) must be provided.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Lean code parsing — extract theorem info and dependency references
# ---------------------------------------------------------------------------

# Matches: theorem <name> or lemma <name>
_THEOREM_DECL_RE = re.compile(
    r"(?m)^\s*(?:noncomputable\s+)?(?:theorem|lemma)\s+(\S+)"
)
# Matches the theorem statement between name and `:= by`
_STATEMENT_RE = re.compile(
    r"(?ms)(?:theorem|lemma)\s+\S+\s*(.*?)\s*:=\s*by"
)
# Tactic targets: apply/exact/rw/simp/have references to qualified names
_QUALIFIED_NAME_RE = re.compile(
    r"(?:apply|exact|rw\s*\[|simp\s*\[|have\s+\S+\s*:=\s*)"
    r"[^A-Za-z]*"
    r"([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)"
)
# Broader: any qualified dot-name that looks like a Mathlib reference
_DOT_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*){1,})\b"
)
# Tactic names used in a proof body
_TACTIC_RE = re.compile(
    r"\b(simp|ring|norm_num|linarith|omega|nlinarith|positivity|polyrith"
    r"|field_simp|push_neg|contrapose|contradiction|exact|apply|intro|intros"
    r"|constructor|cases|rcases|obtain|induction|refine|rfl|ext|funext|congr"
    r"|rw|rewrite|calc|have|let|suffices|show|specialize|use|existsi"
    r"|norm_cast|push_cast|decide|trivial|tauto|aesop|gcongr|rel"
    r"|continuity|measurability|bound)\b"
)
# Import lines
_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$", re.MULTILINE)
# Namespace
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(\S+)", re.MULTILINE)


@dataclasses.dataclass
class TheoremInfo:
    """Parsed information from a single .lean file."""
    name: str
    statement: str
    full_lean_code: str
    imports: list[str]
    namespace: Optional[str]
    referenced_lemmas: list[str]
    tactics_used: list[str]
    has_sorry: bool
    source_path: str


def _extract_referenced_lemmas(lean_code: str, *, after_by: bool = True) -> list[str]:
    """Extract qualified Mathlib-like names referenced in the proof body."""
    text = lean_code
    if after_by:
        by_match = re.search(r":=\s*by\b", lean_code)
        if by_match:
            text = lean_code[by_match.end():]

    # Collect from tactic arguments (high confidence)
    refs: set[str] = set()
    for m in _QUALIFIED_NAME_RE.finditer(text):
        refs.add(m.group(1))

    # Collect broader dot-names, filtering noise
    for m in _DOT_NAME_RE.finditer(text):
        name = m.group(1)
        # Skip common non-lemma patterns
        if name.startswith("Formalizations."):
            continue
        if name in ("Mathlib", "Lean", "Init"):
            continue
        # Must have at least 2 segments to be a real reference
        parts = name.split(".")
        if len(parts) >= 2:
            refs.add(name)

    # Remove the bulk import name
    refs.discard("Mathlib")
    return sorted(refs)


def _extract_tactics(lean_code: str) -> list[str]:
    """Extract tactic names used in the proof body."""
    by_match = re.search(r":=\s*by\b", lean_code)
    if not by_match:
        return []
    proof_text = lean_code[by_match.end():]
    # Remove end-namespace suffix
    end_match = re.search(r"\bend\s+\w+\s*$", proof_text)
    if end_match:
        proof_text = proof_text[:end_match.start()]
    return sorted(set(_TACTIC_RE.findall(proof_text)))


def parse_lean_file(path: Path) -> Optional[TheoremInfo]:
    """Parse a .lean file and extract theorem information."""
    try:
        code = path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Theorem name
    name_match = _THEOREM_DECL_RE.search(code)
    if not name_match:
        return None
    name = name_match.group(1)

    # Statement (between name and `:= by`)
    stmt_match = _STATEMENT_RE.search(code)
    statement = stmt_match.group(1).strip() if stmt_match else ""

    # Imports
    imports = [m.group(1).strip() for m in _IMPORT_RE.finditer(code)]

    # Namespace
    ns_match = _NAMESPACE_RE.search(code)
    namespace = ns_match.group(1) if ns_match else None

    # References and tactics
    has_sorry = bool(re.search(r"\bsorry\b", code))
    refs = _extract_referenced_lemmas(code)
    tactics = _extract_tactics(code)

    return TheoremInfo(
        name=name,
        statement=statement,
        full_lean_code=code,
        imports=imports,
        namespace=namespace,
        referenced_lemmas=refs,
        tactics_used=tactics,
        has_sorry=has_sorry,
        source_path=str(path),
    )


# ---------------------------------------------------------------------------
# Plan text parsing — extract suggested lemmas from natural language plans
# ---------------------------------------------------------------------------

_PLAN_LEMMA_RE = re.compile(
    r"\b((?:Mathlib|Nat|Int|Real|Complex|Set|Finset|List|Multiset|Filter|Metric|MeasureTheory|Topology|Order|Algebra|RingTheory|FieldTheory|NumberTheory|LinearAlgebra|Analysis|CategoryTheory|Data|Logic|Tactic)"
    r"(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b"
)


def extract_plan_lemmas(plan_text: str) -> list[str]:
    """Extract Mathlib-style qualified names from planner output."""
    return sorted(set(_PLAN_LEMMA_RE.findall(plan_text)))


# ---------------------------------------------------------------------------
# Proof output scanning — read summary.json and plan logs
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProofResult:
    """Summary of a proving attempt for one theorem."""
    theorem_name: str
    passed: bool
    attempts_used: int
    plan_rounds_used: int
    final_lean_path: Optional[str]
    plan_lemmas: list[str]  # lemmas suggested across all plan rounds
    proven_lean_code: Optional[str]  # final successful candidate code


def scan_proof_output(proof_dir: Path, theorem_name: str) -> Optional[ProofResult]:
    """Read proving artifacts for a single theorem from its output directory."""
    # The prove pipeline writes per-theorem dirs named after the .lean stem
    # e.g., proofs_out/<theorem_name>/summary.json
    # Try both with and without .lean suffix in directory name
    candidates = [
        proof_dir / theorem_name,
        proof_dir / f"{theorem_name}.lean",
    ]
    # Also search for any subdir whose name matches
    if proof_dir.is_dir():
        for child in proof_dir.iterdir():
            if child.is_dir() and child.name.replace(".lean", "") == theorem_name:
                if child not in candidates:
                    candidates.append(child)

    problem_dir: Optional[Path] = None
    for c in candidates:
        if c.is_dir() and (c / "summary.json").exists():
            problem_dir = c
            break

    if problem_dir is None:
        return None

    # Read summary
    try:
        summary = json.loads((problem_dir / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    passed = bool(summary.get("passed", False))
    attempts_used = int(summary.get("attempts_used", 0))
    plan_rounds_used = int(summary.get("plan_rounds_used", 0))
    final_lean_path = summary.get("final_lean_path")

    # Collect plan lemmas from all plan round logs
    plan_lemmas: list[str] = []
    for plan_log in sorted(problem_dir.glob("plan_round*.model_stdout.log")):
        try:
            text = plan_log.read_text(encoding="utf-8")
            plan_lemmas.extend(extract_plan_lemmas(text))
        except OSError:
            continue
    plan_lemmas = sorted(set(plan_lemmas))

    # Read the final proven code
    proven_code: Optional[str] = None
    if passed and final_lean_path:
        lean_p = Path(final_lean_path)
        if lean_p.exists():
            try:
                proven_code = lean_p.read_text(encoding="utf-8")
            except OSError:
                pass

    return ProofResult(
        theorem_name=summary.get("theorem_name", theorem_name),
        passed=passed,
        attempts_used=attempts_used,
        plan_rounds_used=plan_rounds_used,
        final_lean_path=final_lean_path,
        plan_lemmas=plan_lemmas,
        proven_lean_code=proven_code,
    )


# ---------------------------------------------------------------------------
# Obsidian markdown generation
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Convert a qualified name to a safe filename (replace dots with dashes)."""
    return re.sub(r"[^\w\-.]", "-", name.replace(".", "-"))


def _obsidian_link(name: str) -> str:
    """Create an Obsidian wiki-link to a note."""
    return f"[[{_safe_filename(name)}|{name}]]"


def generate_theorem_note(
    info: TheoremInfo,
    proof: Optional[ProofResult],
    eval_grade: Optional[str] = None,
) -> str:
    """Generate Obsidian markdown for a user theorem."""
    # Determine proof status
    # A .lean file with no sorry is a complete proof, even without summary.json
    if proof and proof.passed:
        status = "proven"
    elif info.has_sorry:
        status = "formalized (sorry)"
    else:
        status = "proven"

    # Collect all referenced lemmas (from code + plan)
    all_refs = set(info.referenced_lemmas)
    if proof:
        all_refs.update(proof.plan_lemmas)
        # If we have the proven code, also parse it for references
        if proof.proven_lean_code:
            proven_refs = _extract_referenced_lemmas(proof.proven_lean_code)
            all_refs.update(proven_refs)
    all_refs_sorted = sorted(all_refs)

    # Build tactics list from proven code if available
    tactics = info.tactics_used
    if proof and proof.proven_lean_code:
        tactics = _extract_tactics(proof.proven_lean_code)

    # Tags
    tags = [status.replace(" ", "-").replace("(", "").replace(")", "")]
    if eval_grade:
        tags.append(f"grade-{eval_grade}")
    if info.namespace:
        tags.append(info.namespace.lower())

    # Frontmatter
    lines = [
        "---",
        f"title: \"{info.name}\"",
        f"tags: [{', '.join(tags)}]",
        f"proof_status: \"{status}\"",
    ]
    if eval_grade:
        lines.append(f"eval_grade: \"{eval_grade}\"")
    if proof:
        lines.append(f"attempts_used: {proof.attempts_used}")
        lines.append(f"plan_rounds: {proof.plan_rounds_used}")
    lines.append(f"source: \"{info.source_path}\"")
    lines.append(f"dependencies: [{', '.join(f'\"{r}\"' for r in all_refs_sorted)}]")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {info.name}")
    lines.append("")

    # Statement
    lines.append("## Statement")
    lines.append(f"```")
    lines.append(info.statement)
    lines.append("```")
    lines.append("")

    # Lean code (use proven version if available)
    display_code = info.full_lean_code
    if proof and proof.proven_lean_code:
        display_code = proof.proven_lean_code
    lines.append("## Lean Code")
    lines.append("```lean")
    lines.append(display_code.strip())
    lines.append("```")
    lines.append("")

    # Dependencies section with wiki-links
    if all_refs_sorted:
        lines.append("## Dependencies")
        for ref in all_refs_sorted:
            lines.append(f"- {_obsidian_link(ref)}")
        lines.append("")

    # Tactics used
    if tactics:
        lines.append("## Tactics Used")
        lines.append(", ".join(f"`{t}`" for t in tactics))
        lines.append("")

    # Proof status details
    lines.append("## Proof Status")
    lines.append(f"- **Status**: {status}")
    if eval_grade:
        lines.append(f"- **Evaluation Grade**: {eval_grade}")
    if proof:
        lines.append(f"- **Attempts Used**: {proof.attempts_used}")
        lines.append(f"- **Plan Rounds**: {proof.plan_rounds_used}")
        if proof.passed and proof.final_lean_path:
            lines.append(f"- **Final Lean File**: `{proof.final_lean_path}`")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lean type signature querying via `lake env lean`
# ---------------------------------------------------------------------------

# Known descriptions for common Mathlib lemma families.
# Keyed by prefix — longest match wins.
_LEMMA_DESCRIPTIONS: dict[str, str] = {
    "Nat.add_zero": "States that adding zero to a natural number yields the same number.",
    "Nat.zero_add": "States that zero plus a natural number equals that number.",
    "Nat.succ": "Successor-related lemmas for natural numbers.",
    "Nat.lt": "Strict ordering lemmas for natural numbers.",
    "Nat.le": "Non-strict ordering lemmas for natural numbers.",
    "Nat.mul": "Multiplication lemmas for natural numbers.",
    "Int.": "Integer arithmetic lemma.",
    "Real.": "Real number analysis lemma.",
    "Complex.": "Complex number lemma.",
    "Set.": "Set theory lemma.",
    "Finset.": "Finite set combinatorics lemma.",
    "List.": "List manipulation lemma.",
    "Filter.": "Filter/limit lemma from topology.",
    "Metric.": "Metric space lemma.",
    "MeasureTheory.": "Measure theory lemma.",
    "Topology.": "General topology lemma.",
    "Order.": "Order theory lemma.",
    "Algebra.": "Abstract algebra lemma.",
    "RingTheory.": "Ring theory lemma.",
    "FieldTheory.": "Field theory lemma.",
    "NumberTheory.": "Number theory lemma.",
    "LinearAlgebra.": "Linear algebra lemma.",
    "Analysis.": "Mathematical analysis lemma.",
    "CategoryTheory.": "Category theory lemma.",
    "Eq.": "Equality-related lemma from core Lean.",
    "Iff.": "If-and-only-if logical equivalence lemma.",
    "Or.": "Disjunction (logical or) lemma.",
    "And.": "Conjunction (logical and) lemma.",
    "Not.": "Negation lemma.",
    "Exists.": "Existential quantifier lemma.",
}


def _describe_lemma(qualified_name: str) -> str:
    """Return a short natural-language description for a lemma based on its name."""
    # Try longest prefix match
    best_match = ""
    best_desc = ""
    for prefix, desc in _LEMMA_DESCRIPTIONS.items():
        if qualified_name.startswith(prefix) and len(prefix) > len(best_match):
            best_match = prefix
            best_desc = desc

    if best_desc:
        return best_desc

    # Fallback: infer from the name structure
    parts = qualified_name.split(".")
    if len(parts) >= 2:
        domain = parts[0]
        return f"Lemma from `{domain}` namespace in Mathlib."
    return "Mathlib lemma."


def _build_lean_env() -> dict[str, str]:
    """Build an environment dict that includes elan/lake on PATH."""
    env = dict(os.environ)
    # If ELAN_HOME is set, prepend its bin/ to PATH so lake/lean are available
    elan_home = env.get("ELAN_HOME")
    if elan_home:
        elan_bin = os.path.join(elan_home, "bin")
        if os.path.isdir(elan_bin):
            env["PATH"] = os.pathsep.join(filter(None, [elan_bin, env.get("PATH")]))
    return env


def _run_lean_check(
    lean_source: str,
    *,
    lean_project_dir: Path,
    compile_cmd: str,
    timeout_s: int = 120,
) -> Optional[str]:
    """Write a Lean file and run the compiler; return combined output or None."""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".lean", dir=str(lean_project_dir))
        os.write(fd, lean_source.encode("utf-8"))
        os.close(fd)
    except OSError:
        return None

    tmp_lean = Path(tmp_path)
    try:
        cmd_parts = shlex.split(compile_cmd)
        cmd = [p.replace("{file}", str(tmp_lean.resolve())) for p in cmd_parts]
        proc = subprocess.run(
            cmd,
            cwd=str(lean_project_dir),
            env=_build_lean_env(),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_s,
        )
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            tmp_lean.unlink()
        except OSError:
            pass


# Keywords that start a #print output block at column 0
_PRINT_KEYWORDS = (
    "theorem", "def", "lemma", "axiom", "abbrev", "instance", "class",
    "structure", "constructor", "opaque", "inductive", "protected",
    "noncomputable", "@[",
)


def _is_print_block_start(line: str) -> bool:
    """Check if a line starts a new #print definition block."""
    stripped = line.lstrip()
    if not stripped:
        return False
    return any(stripped.startswith(kw) for kw in _PRINT_KEYWORDS)


def _extract_name_from_print_block(block: str) -> Optional[str]:
    """Extract the qualified name from a #print block's first line."""
    # Remove attributes like @[defeq]
    text = re.sub(r"@\[.*?\]\s*", "", block.split("\n")[0])
    # Remove leading keywords
    text = re.sub(
        r"^(?:protected\s+)?(?:noncomputable\s+)?"
        r"(?:theorem|def|lemma|axiom|abbrev|instance|class|structure|constructor|opaque|inductive)\s+",
        "", text.strip(),
    )
    # Name is everything before " : "
    if " : " not in text:
        return None
    name = text.split(" : ", 1)[0].strip().lstrip("@")
    # Remove universe params like .{u_1, u_2}
    name = re.sub(r"\.?\{[^}]*\}", "", name).strip()
    return name if name else None


def _parse_print_output(
    output: str,
    lemma_names: list[str],
) -> dict[str, str]:
    """Parse multi-line `#print` output and extract full definitions.

    #print output looks like:
        theorem Nat.add_zero : ∀ (n : ℕ), n + 0 = n :=
        fun n => rfl

    Returns dict mapping lemma_name -> full definition text (type + body).
    """
    name_set = set(lemma_names)
    result: dict[str, str] = {}

    # Split output into blocks: each block starts with a keyword at column 0
    lines = output.splitlines()
    blocks: list[list[str]] = []
    current_block: list[str] = []

    for line in lines:
        # Error lines from Lean — skip
        if "error" in line.lower() and ("unknown" in line.lower() or "identifier" in line.lower()):
            continue

        # New block starts when a line begins with a definition keyword (not indented)
        if line and not line[0].isspace() and _is_print_block_start(line):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)

    # Match blocks to requested names
    for block_lines in blocks:
        full_def = "\n".join(block_lines).strip()
        name = _extract_name_from_print_block(full_def)
        if not name:
            continue

        if name in name_set:
            result[name] = full_def
        else:
            # Fuzzy match
            for orig in lemma_names:
                if orig not in result and (name.endswith(orig) or orig.endswith(name)):
                    result[orig] = full_def
                    break

    return result


def _batch_check_lean_types(
    lemma_names: list[str],
    *,
    lean_project_dir: Optional[Path],
    compile_cmd: str = "lake env lean {file}",
) -> dict[str, str]:
    """Query Lean for the type signatures of a batch of lemma names.

    Returns a dict mapping lemma name -> type signature string.
    Names that fail to resolve are omitted from the result.
    """
    if not lemma_names or lean_project_dir is None:
        return {}

    if not lean_project_dir.is_dir():
        return {}

    # Build a single Lean file that #print's every name
    # #print gives full definition: type + proof term, much richer than #check
    check_lines = ["import Mathlib", ""]
    for name in lemma_names:
        check_lines.append(f"#print {name}")
    lean_source = "\n".join(check_lines) + "\n"

    output = _run_lean_check(
        lean_source,
        lean_project_dir=lean_project_dir,
        compile_cmd=compile_cmd,
    )
    if output is None:
        return {}

    result = _parse_print_output(output, lemma_names)

    # For any names that failed in the batch, retry individually
    missing = [n for n in lemma_names if n not in result]
    if missing and len(missing) < len(lemma_names):
        for name in missing:
            single_source = f"import Mathlib\n\n#print {name}\n"
            single_output = _run_lean_check(
                single_source,
                lean_project_dir=lean_project_dir,
                compile_cmd=compile_cmd,
                timeout_s=60,
            )
            if single_output:
                single_result = _parse_print_output(single_output, [name])
                result.update(single_result)

    return result


def _extract_type_from_print(full_def: str) -> Optional[str]:
    """Extract just the type signature from a #print output block."""
    # Match: ... Name : <type> :=
    m = re.search(r":\s*(.+?)\s*:=", full_def, re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # Fallback: everything after first " : "
    if " : " in full_def:
        return full_def.split(" : ", 1)[1].strip()
    return None


def generate_lemma_stub(
    qualified_name: str,
    referencing_theorems: list[str],
    lean_def: Optional[str] = None,
) -> str:
    """Generate an Obsidian markdown stub for a Mathlib lemma."""
    parts = qualified_name.split(".")
    module = ".".join(parts[:-1]) if len(parts) > 1 else ""
    short_name = parts[-1] if parts else qualified_name
    description = _describe_lemma(qualified_name)

    # Extract type signature from full definition
    lean_type = _extract_type_from_print(lean_def) if lean_def else None

    tags = ["mathlib", "lemma"]
    if parts:
        tags.append(parts[0].lower())

    lines = [
        "---",
        f"title: \"{qualified_name}\"",
        f"tags: [{', '.join(tags)}]",
        "type: mathlib-lemma",
    ]
    if module:
        lines.append(f"module: \"{module}\"")
    if lean_type:
        lines.append(f"lean_type: \"{lean_type.replace(chr(34), chr(39))}\"")
    lines.append("---")
    lines.append("")
    lines.append(f"# {qualified_name}")
    lines.append("")
    lines.append(description)
    lines.append("")
    if module:
        lines.append(f"**Module**: `{module}`")
    lines.append(f"**Short name**: `{short_name}`")
    lines.append("")

    # Full Lean definition from #print
    lines.append("## Lean Definition")
    if lean_def:
        lines.append("```lean")
        lines.append(lean_def)
        lines.append("```")
    else:
        lines.append("```lean")
        lines.append(f"-- Definition not available (run with --lean-project to query Lean)")
        lines.append(f"#print {qualified_name}")
        lines.append("```")
    lines.append("")

    # Type signature summary (extracted from definition)
    if lean_type:
        lines.append("## Type Signature")
        lines.append(f"```")
        lines.append(f"{qualified_name} : {lean_type}")
        lines.append("```")
        lines.append("")

    lines.append("## Referenced By")
    for thm in sorted(referencing_theorems):
        lines.append(f"- {_obsidian_link(thm)}")
    lines.append("")
    return "\n".join(lines)


def generate_index_note(
    theorems: list[TheoremInfo],
    proof_results: dict[str, ProofResult],
) -> str:
    """Generate an index note that links to all theorems."""
    proven = [t for t in theorems if t.name in proof_results and proof_results[t.name].passed]
    formalized = [t for t in theorems if t not in proven]

    lines = [
        "---",
        "title: \"Theorem Index\"",
        "tags: [index]",
        "---",
        "",
        "# Theorem Index",
        "",
        f"**Total theorems**: {len(theorems)}",
        f"**Proven**: {len(proven)}",
        f"**Formalized (unproven)**: {len(formalized)}",
        "",
    ]

    if proven:
        lines.append("## Proven Theorems")
        for t in sorted(proven, key=lambda x: x.name):
            pr = proof_results[t.name]
            lines.append(f"- {_obsidian_link(t.name)} ({pr.attempts_used} attempts)")
        lines.append("")

    if formalized:
        lines.append("## Formalized Theorems")
        for t in sorted(formalized, key=lambda x: x.name):
            status = "sorry" if t.has_sorry else "complete"
            lines.append(f"- {_obsidian_link(t.name)} ({status})")
        lines.append("")

    # Dependency statistics
    all_refs: dict[str, int] = {}
    for t in theorems:
        for ref in t.referenced_lemmas:
            all_refs[ref] = all_refs.get(ref, 0) + 1
        pr = proof_results.get(t.name)
        if pr:
            for ref in pr.plan_lemmas:
                all_refs[ref] = all_refs.get(ref, 0) + 1

    if all_refs:
        lines.append("## Most Referenced Lemmas")
        top = sorted(all_refs.items(), key=lambda x: -x[1])[:20]
        for name, count in top:
            lines.append(f"- {_obsidian_link(name)} (referenced {count}x)")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evaluation grade loading
# ---------------------------------------------------------------------------

def _load_eval_grade(logs_dir: Path, theorem_name: str) -> Optional[str]:
    """Load the latest evaluation grade from formalization logs."""
    if logs_dir is None or not logs_dir.is_dir():
        return None

    pattern = re.compile(rf"^{re.escape(theorem_name)}\.iter(\d+)\.eval\.json$")
    best_iter = -1
    best_grade: Optional[str] = None

    for eval_path in logs_dir.glob(f"{theorem_name}.iter*.eval.json"):
        m = pattern.match(eval_path.name)
        if m is None:
            continue
        try:
            iter_no = int(m.group(1))
        except ValueError:
            continue
        try:
            payload = json.loads(eval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_grade = payload.get("grade")
        if not isinstance(raw_grade, str):
            continue
        grade = raw_grade.strip().upper()
        if grade in {"A", "B", "C", "D"} and iter_no > best_iter:
            best_iter = iter_no
            best_grade = grade

    return best_grade


# ---------------------------------------------------------------------------
# Main vault generation
# ---------------------------------------------------------------------------

def generate_vault(
    *,
    formalizations_dir: Optional[Path],
    proofs_dir: Optional[Path],
    logs_dir: Optional[Path],
    vault_dir: Path,
    lean_project_dir: Optional[Path] = None,
    compile_cmd: str = "lake env lean {file}",
    manifest_path: Optional[Path] = None,
) -> None:
    """Scan artifacts and generate the full Obsidian vault."""
    vault_dir.mkdir(parents=True, exist_ok=True)
    theorems_dir = vault_dir / "Theorems"
    lemmas_dir = vault_dir / "Lemmas"
    theorems_dir.mkdir(exist_ok=True)
    lemmas_dir.mkdir(exist_ok=True)

    # Load manifest — if present, only process listed files
    manifest_files: Optional[set[str]] = None
    if manifest_path and manifest_path.exists():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest_data, dict) and isinstance(manifest_data.get("files"), list):
                manifest_files = set(manifest_data["files"])
                # Also include _proven.lean variants
                proven = set()
                for f in manifest_files:
                    if f.endswith(".lean"):
                        proven.add(f.removesuffix(".lean") + "_proven.lean")
                manifest_files.update(proven)
                print(f"Manifest: {len(manifest_data['files'])} theorem(s) in this vault.", file=sys.stderr)
        except (OSError, json.JSONDecodeError):
            pass

    # 1. Collect .lean files (filtered by manifest if present)
    lean_files: list[Path] = []
    if formalizations_dir and formalizations_dir.is_dir():
        for lf in sorted(formalizations_dir.rglob("*.lean")):
            if manifest_files is None or lf.name in manifest_files:
                lean_files.append(lf)
    if proofs_dir and proofs_dir.is_dir():
        for candidate in sorted(proofs_dir.rglob("*.candidate.lean")):
            if manifest_files is None or candidate.name in manifest_files:
                lean_files.append(candidate)

    # 2. Parse all theorems (deduplicate by name, prefer proven versions)
    parsed: dict[str, TheoremInfo] = {}
    for lf in lean_files:
        info = parse_lean_file(lf)
        if info is None:
            continue
        existing = parsed.get(info.name)
        # Prefer non-sorry over sorry, and proven candidate over formalization
        if existing is None:
            parsed[info.name] = info
        elif existing.has_sorry and not info.has_sorry:
            parsed[info.name] = info

    if not parsed:
        print("No theorems found to process.", file=sys.stderr)
        return

    print(f"Found {len(parsed)} theorem(s).", file=sys.stderr)

    # 3. Scan proof results
    proof_results: dict[str, ProofResult] = {}
    if proofs_dir and proofs_dir.is_dir():
        for name in parsed:
            pr = scan_proof_output(proofs_dir, name)
            if pr:
                proof_results[name] = pr
                # If proof has proven code, re-parse for better dependency info
                if pr.proven_lean_code:
                    proven_info = TheoremInfo(
                        name=parsed[name].name,
                        statement=parsed[name].statement,
                        full_lean_code=pr.proven_lean_code,
                        imports=parsed[name].imports,
                        namespace=parsed[name].namespace,
                        referenced_lemmas=_extract_referenced_lemmas(pr.proven_lean_code),
                        tactics_used=_extract_tactics(pr.proven_lean_code),
                        has_sorry=False,
                        source_path=pr.final_lean_path or parsed[name].source_path,
                    )
                    parsed[name] = proven_info

    if proof_results:
        proven_count = sum(1 for pr in proof_results.values() if pr.passed)
        print(f"Found {len(proof_results)} proof result(s), {proven_count} passed.", file=sys.stderr)

    # 4. Build reverse index: lemma -> list of theorems that reference it
    lemma_refs: dict[str, list[str]] = {}
    for info in parsed.values():
        all_refs = set(info.referenced_lemmas)
        pr = proof_results.get(info.name)
        if pr:
            all_refs.update(pr.plan_lemmas)
        for ref in all_refs:
            lemma_refs.setdefault(ref, []).append(info.name)

    # 5. Generate theorem notes
    for info in parsed.values():
        pr = proof_results.get(info.name)
        grade = _load_eval_grade(logs_dir, info.name) if logs_dir else None
        content = generate_theorem_note(info, pr, eval_grade=grade)
        filename = _safe_filename(info.name) + ".md"
        (theorems_dir / filename).write_text(content, encoding="utf-8")

    print(f"Generated {len(parsed)} theorem note(s).", file=sys.stderr)

    # 7. Load lemma definitions from cache (written by prove pipeline's LSP session)
    #    then fall back to direct Lean #print query if cache misses remain
    lean_types: dict[str, str] = {}

    # Try cache first (fast, no Lean process needed)
    if formalizations_dir and formalizations_dir.is_dir():
        cache_path = formalizations_dir / "lemma_cache.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    for name in lemma_refs:
                        if name in cached:
                            lean_types[name] = str(cached[name])
                    if lean_types:
                        print(f"Loaded {len(lean_types)} lemma definition(s) from cache.", file=sys.stderr)
            except (OSError, json.JSONDecodeError):
                pass

    # Fall back to direct Lean query for any remaining uncached lemmas
    uncached = [n for n in lemma_refs if n not in lean_types]
    if uncached and lean_project_dir:
        print(f"Querying Lean for {len(uncached)} uncached lemma definition(s)...", file=sys.stderr)
        fresh = _batch_check_lean_types(
            uncached,
            lean_project_dir=lean_project_dir,
            compile_cmd=compile_cmd,
        )
        lean_types.update(fresh)
        if fresh:
            print(f"Resolved {len(fresh)}/{len(uncached)} from Lean.", file=sys.stderr)

    if lean_types:
        print(f"Total: {len(lean_types)}/{len(lemma_refs)} lemma definition(s) available.", file=sys.stderr)

    # 8. Generate lemma stubs (preserve existing definitions if new query failed)
    for lemma_name, referencing in lemma_refs.items():
        new_def = lean_types.get(lemma_name)
        filename = _safe_filename(lemma_name) + ".md"
        target_path = lemmas_dir / filename

        # If we don't have a definition but an existing stub does, reuse its definition
        # but still regenerate the stub so Referenced By links stay current
        if new_def is None and target_path.exists():
            try:
                existing = target_path.read_text(encoding="utf-8")
                if "```lean\n" in existing and "Definition not available" not in existing:
                    # Extract the lean definition block from existing stub
                    header = "## Lean Definition\n```lean\n"
                    lean_start = existing.find(header)
                    if lean_start >= 0:
                        block_start = lean_start + len(header)
                        lean_end = existing.find("\n```\n", block_start)
                        if lean_end >= 0:
                            new_def = existing[block_start:lean_end]
            except OSError:
                pass

        content = generate_lemma_stub(
            lemma_name,
            referencing,
            lean_def=new_def,
        )
        target_path.write_text(content, encoding="utf-8")

    print(f"Generated {len(lemma_refs)} lemma stub(s).", file=sys.stderr)

    # 9. Generate index (clean up old filename if present)
    old_index = vault_dir / "Theorem Index.md"
    if old_index.exists():
        try:
            old_index.unlink()
        except OSError:
            pass
    index_content = generate_index_note(list(parsed.values()), proof_results)
    (vault_dir / "Theorem_Index.md").write_text(index_content, encoding="utf-8")

    print(f"Vault generated at: {vault_dir}", file=sys.stderr)
    print(f"Open this directory in Obsidian to view the theorem graph.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate_obsidian_vault",
        description="Generate an Obsidian vault from AUTOLEAN formalization and proving artifacts.",
    )
    p.add_argument(
        "--formalizations",
        type=Path,
        default=None,
        help="Directory containing formalized .lean files (e.g., LeanFormalizations/).",
    )
    p.add_argument(
        "--proofs",
        type=Path,
        default=None,
        help="Directory containing proof output (summary.json, plan logs, candidates).",
    )
    p.add_argument(
        "--logs",
        type=Path,
        default=None,
        help="Directory containing formalization logs (for eval grades).",
    )
    p.add_argument(
        "--vault",
        type=Path,
        default=Path("ObsidianVault"),
        help="Output directory for the Obsidian vault (default: ObsidianVault/).",
    )
    p.add_argument(
        "--lean-project",
        type=Path,
        default=None,
        help="Lean project directory (contains lakefile.toml). When provided, queries Lean for lemma type signatures.",
    )
    p.add_argument(
        "--compile-cmd",
        type=str,
        default="lake env lean {file}",
        help="Compile command template for Lean (default: 'lake env lean {file}').",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to vault manifest.json. When provided, only process listed files.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.formalizations is None and args.proofs is None:
        print(
            "Error: at least one of --formalizations or --proofs must be provided.",
            file=sys.stderr,
        )
        return 2

    generate_vault(
        formalizations_dir=args.formalizations,
        proofs_dir=args.proofs,
        logs_dir=args.logs,
        vault_dir=args.vault,
        lean_project_dir=args.lean_project,
        compile_cmd=args.compile_cmd,
        manifest_path=args.manifest,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
