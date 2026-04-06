#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autolean.core import (  # noqa: E402
    _call_openrouter_chat,
    _call_claude_cli,
    _call_codex_exec,
    _extract_model_response_text,
    _parse_json_object_from_model_text,
)
from autolean.util import ensure_dir  # noqa: E402


_REASONING_CHOICES = ["minimal", "low", "medium", "high", "xhigh"]
_FORBIDDEN_LINE_KEYWORDS = ("axiom", "constant", "postulate")
_THEOREM_DECL_RE_TEMPLATE = r"(?m)^\s*(?:theorem|lemma)\s+{name}\b"
_WHITESPACE_RE = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class ProofConfig:
    input_dir: Path
    out_dir: Path
    planner_model: str
    planner_reasoning_effort: str
    prover_model: str
    prover_reasoning_effort: str
    attempts_before_replan: int
    max_plan_rounds: int
    workers: int
    resume: bool
    api_key_env: str
    openrouter_base_url: str
    openrouter_timeout_s: int
    openrouter_max_retries: int
    use_codex_exec: bool
    codex_exec_model: Optional[str]
    codex_exec_sandbox: str
    use_claude_cli: bool
    claude_cli_cmd: str
    compile_cmd: str
    cwd: Path
    use_lsp: bool = False
    lsp_timeout_s: float = 120.0
    lemma_cache_dir: Optional[Path] = None
    agent_prove: bool = False
    agent_session_timeout: int = 600
    agent_max_compiles: int = 5
    vault_dir: Optional[Path] = None


@dataclasses.dataclass(frozen=True)
class ProofTarget:
    lean_path: Path
    relative_path: Path
    theorem_name: str
    original_text: str
    normalized_header: str
    original_import_block: str
    frozen_before_proof: str
    frozen_suffix: str


@dataclasses.dataclass(frozen=True)
class _DeclLayout:
    theorem_start: int
    proof_start: int
    normalized_header: str


@dataclasses.dataclass(frozen=True)
class CompileResult:
    returncode: int
    stdout: str
    stderr: str


@dataclasses.dataclass(frozen=True)
class AttemptRecord:
    attempt_no: int
    plan_round: int
    status: str
    candidate_path: Optional[str]
    error: Optional[str]


@dataclasses.dataclass(frozen=True)
class ProblemOutcome:
    relative_path: str
    theorem_name: str
    passed: bool
    attempts_used: int
    plan_rounds_used: int
    successful_attempt_no: Optional[int]
    final_lean_path: Optional[str]
    error: Optional[str]
    attempts: list[AttemptRecord]


@dataclasses.dataclass(frozen=True)
class ProgressState:
    relative_path: str
    theorem_name: str
    next_attempt_no: int
    prev_lean: str
    last_failure: str
    last_candidate_path: Optional[str]
    plan_text_by_round: dict[str, str]
    attempts: list[AttemptRecord]


def _format_progress(completed: int, total: int, *, label: str = "Proving") -> str:
    width = 24
    if total <= 0:
        bar = "[" + "." * width + "]"
        return f"{label}: {bar} 0/0"
    filled = int(width * completed / total)
    filled = min(width, max(0, filled))
    bar = "[" + "#" * filled + "." * (width - filled) + "]"
    return f"{label}: {bar} {completed}/{total}"


def _make_progress_printer():
    last_len = 0

    def _print(msg: str, *, done: bool = False) -> None:
        nonlocal last_len
        pad = " " * max(0, last_len - len(msg))
        end = "\n" if done else ""
        print("\r" + msg + pad, end=end, flush=True)
        last_len = len(msg)

    return _print


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _split_import_block(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    idx = 0
    in_block_comment = False

    while idx < len(lines):
        stripped = lines[idx].strip()
        if in_block_comment:
            idx += 1
            if "-/" in stripped:
                in_block_comment = False
            continue
        if not stripped:
            idx += 1
            continue
        if stripped.startswith("--"):
            idx += 1
            continue
        if stripped.startswith("/-"):
            idx += 1
            if "-/" not in stripped:
                in_block_comment = True
            continue
        if stripped.startswith("import "):
            idx += 1
            continue
        break

    return "".join(lines[:idx]), "".join(lines[idx:])


def _extract_decl_layout(lean_code: str, theorem_name: str) -> Optional[_DeclLayout]:
    start_re = re.compile(_THEOREM_DECL_RE_TEMPLATE.format(name=re.escape(theorem_name)))
    match = start_re.search(lean_code)
    if match is None:
        return None

    depth = 0
    for idx in range(match.end(), len(lean_code) - 1):
        ch = lean_code[idx]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(depth - 1, 0)
        elif ch == ":" and depth == 0 and lean_code[idx + 1] == "=":
            body_idx = idx + 2
            while body_idx < len(lean_code) and lean_code[body_idx].isspace():
                body_idx += 1
            if not lean_code.startswith("by", body_idx):
                continue
            header = lean_code[match.start() : idx].strip()
            return _DeclLayout(
                theorem_start=match.start(),
                proof_start=body_idx + 2,
                normalized_header=_normalize_whitespace(header),
            )
    return None


def _extract_import_lines(import_block: str) -> list[str]:
    lines: list[str] = []
    for raw_line in import_block.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("import "):
            lines.append(stripped)
    return lines


def _find_frozen_suffix_start(text: str, proof_start: int) -> int:
    suffix_re = re.compile(r"(?m)^\s*end(?:\s+[A-Za-z0-9_'.]+)?\s*$")
    matches = list(suffix_re.finditer(text, proof_start))
    if not matches:
        return len(text)
    return matches[-1].start()


def _has_placeholder_proof(lean_code: str) -> bool:
    return bool(re.search(r"\b(?:sorry|admit)\b", lean_code))


def _detect_forbidden_content(candidate_text: str, original_text: str) -> Optional[str]:
    if re.search(r"\bsorry\b", candidate_text):
        return "generated Lean still contains `sorry`"
    if re.search(r"\badmit\b", candidate_text):
        return "generated Lean still contains `admit`"

    for keyword in _FORBIDDEN_LINE_KEYWORDS:
        candidate_count = len(re.findall(rf"(?m)^\s*{keyword}\b", candidate_text))
        original_count = len(re.findall(rf"(?m)^\s*{keyword}\b", original_text))
        if candidate_count > original_count:
            return f"generated Lean introduced forbidden top-level `{keyword}` declarations"
    return None


def _load_targets(
    *,
    input_dir: Path,
    include_complete: bool,
    skip_subtree: Optional[Path] = None,
    limit: Optional[int] = None,
) -> tuple[list[ProofTarget], list[Path], list[Path]]:
    targets: list[ProofTarget] = []
    invalid_targets: list[Path] = []
    skipped_complete: list[Path] = []

    for lean_path in sorted(input_dir.rglob("*.lean")):
        if skip_subtree is not None and _is_relative_to(lean_path, skip_subtree):
            continue

        try:
            original_text = lean_path.read_text(encoding="utf-8")
        except OSError:
            invalid_targets.append(lean_path)
            continue

        if not include_complete and not _has_placeholder_proof(original_text):
            skipped_complete.append(lean_path)
            continue

        theorem_name = lean_path.stem
        original_import_block, _rest = _split_import_block(original_text)
        layout = _extract_decl_layout(original_text, theorem_name)
        if layout is None:
            invalid_targets.append(lean_path)
            continue
        suffix_start = _find_frozen_suffix_start(original_text, layout.proof_start)

        targets.append(
            ProofTarget(
                lean_path=lean_path,
                relative_path=lean_path.relative_to(input_dir),
                theorem_name=theorem_name,
                original_text=original_text,
                normalized_header=layout.normalized_header,
                original_import_block=original_import_block,
                frozen_before_proof=original_text[len(original_import_block) : layout.proof_start],
                frozen_suffix=original_text[suffix_start:],
            )
        )
        if limit is not None and len(targets) >= limit:
            break

    return targets, invalid_targets, skipped_complete


def _build_plan_prompt(
    target: ProofTarget,
    *,
    plan_round: int,
    prev_lean: Optional[str] = None,
    failure_reason: Optional[str] = None,
    lsp_context: str = "",
) -> str:
    prompt = f"""You are planning a Lean 4 proof for an already-formalized theorem.

Planning round: {plan_round}.

Goal:
- Produce a concise technical proof plan for the existing theorem.
- Keep the theorem statement and file structure fixed.
- Identify likely Mathlib lemmas, rewrite steps, and type/coercion pitfalls.
- Do not write the final proof.

Hard constraints:
- The main theorem name must stay exactly `{target.theorem_name}`.
- The main theorem header must stay exactly the same.
- The file must remain unchanged outside the main theorem proof body, except for genuinely necessary extra imports.
- Do not suggest `axiom`, `constant`, `postulate`, or new top-level helper declarations.

Return plain text with these sections:
1) Proof sketch
2) Candidate lemmas
3) Type/coercion pitfalls
4) Import advice
5) Repair focus for the next proving attempt

The exact frozen file prefix ending at the theorem's `by` is:
```lean
{target.original_import_block}{target.frozen_before_proof}
```

The exact frozen file suffix after the proof body is:
```lean
{target.frozen_suffix}
```

Original source file:
```lean
{target.original_text}
```"""

    if prev_lean is not None and prev_lean.strip():
        prompt += f"\n\nLatest Lean attempt:\n```lean\n{prev_lean}\n```"
    if failure_reason is not None and failure_reason.strip():
        prompt += f"\n\nLatest failure / compile report:\n```text\n{failure_reason}\n```"
    if lsp_context:
        prompt += f"\n\n{lsp_context}"
    return prompt


def _build_initial_proof_prompt(
    target: ProofTarget,
    *,
    plan_round: int,
    total_plan_rounds: int,
    attempt_no: int,
    total_attempts: int,
    plan_text: str,
    lsp_context: str = "",
) -> str:
    lsp_block = f"\n\n{lsp_context}" if lsp_context else ""
    return f"""You are completing a Lean 4 proof for an already-formalized theorem.

Plan round: {plan_round} of {total_plan_rounds}.
Proof attempt: {attempt_no} of {total_attempts}.

Goal:
- Replace the placeholder proof body with a complete proof that compiles.
- Follow the planner notes, but prioritize a compiling proof over stylistic preferences.
- Keep the file unchanged outside the main theorem proof body, except for genuinely necessary extra imports.

Hard constraints:
- The main theorem name must stay exactly `{target.theorem_name}`.
- The main theorem header must stay exactly the same.
- Remove all `sorry` and `admit`.
- Do not introduce `axiom`, `constant`, or `postulate`.
- Do not rewrite the theorem statement, docstrings, namespace, earlier definitions, or trailing `end`.
- Do not add new top-level lemmas/defs. Use local `have`, `let`, `suffices`, or `calc` inside the proof body instead.
- Do not repeat the theorem header or the leading `by` in your `proof` field.
- If extra imports are needed, put them only in the `imports` list.

Planner notes:
{plan_text}

Return ONLY a JSON object of this form:
{{
  "imports": ["import Mathlib.X", "import Mathlib.Y"],
  "proof": "<Lean proof body that comes after the existing `by`>"
}}

If no extra imports are needed, return `"imports": []`.

The exact frozen file prefix ending at the theorem's `by` is:
```lean
{target.original_import_block}{target.frozen_before_proof}
```

The exact frozen file suffix after the proof body is:
```lean
{target.frozen_suffix}
```

Current Lean file:
```lean
{target.original_text}
```{lsp_block}"""


def _build_repair_prompt(
    target: ProofTarget,
    *,
    plan_round: int,
    total_plan_rounds: int,
    attempt_no: int,
    total_attempts: int,
    plan_text: str,
    prev_lean: str,
    failure_reason: str,
    lsp_context: str = "",
) -> str:
    lsp_block = f"\n\n{lsp_context}" if lsp_context else ""
    return f"""You are repairing a Lean 4 proof attempt for an already-formalized theorem.

Plan round: {plan_round} of {total_plan_rounds}.
Proof attempt: {attempt_no} of {total_attempts}.

Hard constraints:
- Keep the main theorem name exactly `{target.theorem_name}`.
- Keep the file unchanged outside the main theorem proof body, except for genuinely necessary extra imports.
- Remove all `sorry` and `admit`.
- Do not introduce `axiom`, `constant`, or `postulate`.
- Do not add new top-level lemmas/defs. Use local proof structure only.
- Do not repeat the theorem header or the leading `by` in your `proof` field.
- If extra imports are needed, put them only in the `imports` list.

Planner notes for this round:
{plan_text}

Return ONLY a JSON object of this form:
{{
  "imports": ["import Mathlib.X", "import Mathlib.Y"],
  "proof": "<Lean proof body that comes after the existing `by`>"
}}

Previous Lean attempt:
```lean
{prev_lean}
```

Failure reason / compiler output:
```text
{failure_reason}
```

The exact frozen file prefix ending at the theorem's `by` is:
```lean
{target.original_import_block}{target.frozen_before_proof}
```

The exact frozen file suffix after the proof body is:
```lean
{target.frozen_suffix}
```

Original source file for reference:
```lean
{target.original_text}
```{lsp_block}"""


def _normalize_extra_imports(imports_obj: object) -> list[str]:
    if imports_obj is None:
        return []
    if not isinstance(imports_obj, list):
        raise ValueError("Model output field `imports` must be a list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in imports_obj:
        if not isinstance(item, str):
            raise ValueError("Model output field `imports` must be a list of strings.")
        stripped = item.strip()
        if not stripped:
            continue
        line = stripped if stripped.startswith("import ") else f"import {stripped}"
        if "\n" in line or "\r" in line:
            raise ValueError("Import entries must be single lines.")
        if line in seen:
            continue
        seen.add(line)
        normalized.append(line)
    return normalized


def _normalize_proof_body(proof_obj: object) -> str:
    if not isinstance(proof_obj, str) or not proof_obj.strip():
        raise ValueError("Model output missing non-empty string field `proof`.")
    if re.match(r"^\s*by(\s|$)", proof_obj):
        raise ValueError("Proof body must not repeat the leading `by`.")
    if proof_obj.startswith(("\n", " ", "\t")):
        return proof_obj
    return "\n  " + proof_obj


def _extract_extra_imports_from_candidate_block(
    original_import_block: str,
    candidate_import_block: str,
) -> list[str]:
    if not candidate_import_block.startswith(original_import_block):
        raise ValueError("Candidate changed the original import block.")

    tail = candidate_import_block[len(original_import_block) :]
    for raw_line in tail.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.startswith("/-") or stripped == "-/":
            raise ValueError("Extra imports tail may not contain new block comments.")
        if not stripped.startswith("import "):
            raise ValueError("Only extra import lines may be added before the frozen prefix.")

    original_imports = set(_extract_import_lines(original_import_block))
    extra_imports: list[str] = []
    for line in _extract_import_lines(tail):
        if line not in original_imports:
            extra_imports.append(line)
    return extra_imports


def _extract_edits_from_full_file_candidate(
    target: ProofTarget,
    candidate_text: str,
) -> tuple[list[str], str]:
    candidate_import_block, _rest = _split_import_block(candidate_text)
    candidate_layout = _extract_decl_layout(candidate_text, target.theorem_name)
    if candidate_layout is None:
        raise ValueError(f"Candidate full file was missing theorem `{target.theorem_name}`.")

    candidate_suffix_start = _find_frozen_suffix_start(candidate_text, candidate_layout.proof_start)
    candidate_frozen_before_proof = candidate_text[
        len(candidate_import_block) : candidate_layout.proof_start
    ]
    candidate_suffix = candidate_text[candidate_suffix_start:]

    if candidate_frozen_before_proof != target.frozen_before_proof:
        raise ValueError("Candidate full file changed frozen content before the proof body.")
    if candidate_suffix != target.frozen_suffix:
        raise ValueError("Candidate full file changed frozen content after the proof body.")

    extra_imports = _extract_extra_imports_from_candidate_block(
        target.original_import_block,
        candidate_import_block,
    )
    proof_body = candidate_text[candidate_layout.proof_start:candidate_suffix_start]
    return extra_imports, _normalize_proof_body(proof_body)


def _extract_candidate_edits(
    target: ProofTarget,
    payload: dict,
) -> tuple[list[str], str]:
    if "proof" in payload:
        return _normalize_extra_imports(payload.get("imports")), _normalize_proof_body(payload["proof"])

    lean_obj = payload.get("lean")
    if isinstance(lean_obj, str) and lean_obj.strip():
        return _extract_edits_from_full_file_candidate(target, lean_obj)

    raise ValueError("Model output must contain either `proof` or `lean`.")


def _build_candidate_text(
    target: ProofTarget,
    *,
    extra_imports: list[str],
    proof_body: str,
) -> str:
    import_block = target.original_import_block
    original_imports = set(_extract_import_lines(import_block))
    new_imports = [line for line in extra_imports if line not in original_imports]
    if new_imports:
        import_block = import_block.rstrip("\n")
        if import_block:
            import_block += "\n"
        import_block += "\n".join(new_imports) + "\n"
        if not target.frozen_before_proof.startswith("\n"):
            import_block += "\n"
    return import_block + target.frozen_before_proof + proof_body + target.frozen_suffix


def _detect_forbidden_proof_body_content(proof_body: str) -> Optional[str]:
    forbidden_re = re.compile(
        r"(?m)^(?:theorem|lemma|def|noncomputable\s+def|example|namespace|section|end)\b"
    )
    if forbidden_re.search(proof_body):
        return "proof body introduced top-level declarations or structure changes"
    return None


def _call_openrouter_text(
    *,
    prompt: str,
    model: str,
    reasoning_effort: str,
    api_key_env: str,
    openrouter_base_url: str,
    openrouter_timeout_s: int,
    openrouter_max_retries: int,
    use_codex_exec: bool = False,
    codex_exec_model: Optional[str] = None,
    codex_exec_sandbox: str = "read-only",
    use_claude_cli: bool = False,
    claude_cli_cmd: str = "",
    cwd: Optional[Path] = None,
) -> tuple[str, str, int]:
    if use_claude_cli:
        res = _call_claude_cli(
            prompt=prompt,
            claude_cli_cmd=claude_cli_cmd,
            workdir=cwd or Path("."),
            timeout_s=openrouter_timeout_s,
        )
        return res.stdout, res.stderr, res.returncode
    if use_codex_exec:
        out_message_path = (cwd or Path(".")) / f".codex_prove_{id(prompt)}.log"
        res = _call_codex_exec(
            prompt=prompt,
            out_message_path=out_message_path,
            model=codex_exec_model,
            reasoning_effort=reasoning_effort,
            sandbox=codex_exec_sandbox,
            workdir=cwd or Path("."),
        )
        return res.stdout, res.stderr, res.returncode
    res = _call_openrouter_chat(
        prompt=prompt,
        model=model,
        base_url=openrouter_base_url,
        api_key_env=api_key_env,
        timeout_s=openrouter_timeout_s,
        max_retries=openrouter_max_retries,
        reasoning_effort=reasoning_effort,
        openrouter_web_search=False,
        openrouter_web_search_engine=None,
        openrouter_web_search_max_results=None,
    )
    return res.stdout, res.stderr, res.returncode


def _compile_candidate(*, lean_path: Path, compile_cmd: str, cwd: Path) -> CompileResult:
    argv = shlex.split(compile_cmd.replace("{file}", str(lean_path.resolve())))
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    return CompileResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _total_attempts(cfg: ProofConfig) -> int:
    return cfg.attempts_before_replan * cfg.max_plan_rounds


def _plan_round_for_attempt(attempt_no: int, cfg: ProofConfig) -> int:
    return ((attempt_no - 1) // cfg.attempts_before_replan) + 1


def _progress_state_to_payload(state: ProgressState) -> dict[str, object]:
    return {
        "relative_path": state.relative_path,
        "theorem_name": state.theorem_name,
        "next_attempt_no": state.next_attempt_no,
        "prev_lean": state.prev_lean,
        "last_failure": state.last_failure,
        "last_candidate_path": state.last_candidate_path,
        "plan_text_by_round": state.plan_text_by_round,
        "attempts": [dataclasses.asdict(item) for item in state.attempts],
    }


def _write_progress_state(problem_out_dir: Path, state: ProgressState) -> None:
    _write_json(problem_out_dir / "progress.json", _progress_state_to_payload(state))


def _load_problem_outcome(summary_path: Path) -> ProblemOutcome:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    attempts = [
        AttemptRecord(
            attempt_no=int(item["attempt_no"]),
            plan_round=int(item["plan_round"]),
            status=str(item["status"]),
            candidate_path=item.get("candidate_path"),
            error=item.get("error"),
        )
        for item in payload.get("attempts", [])
        if isinstance(item, dict)
    ]
    return ProblemOutcome(
        relative_path=str(payload["relative_path"]),
        theorem_name=str(payload["theorem_name"]),
        passed=bool(payload["passed"]),
        attempts_used=int(payload["attempts_used"]),
        plan_rounds_used=int(payload["plan_rounds_used"]),
        successful_attempt_no=(
            int(payload["successful_attempt_no"])
            if payload.get("successful_attempt_no") is not None
            else None
        ),
        final_lean_path=payload.get("final_lean_path"),
        error=payload.get("error"),
        attempts=attempts,
    )


def _load_progress_state(progress_path: Path) -> ProgressState:
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    attempts = [
        AttemptRecord(
            attempt_no=int(item["attempt_no"]),
            plan_round=int(item["plan_round"]),
            status=str(item["status"]),
            candidate_path=item.get("candidate_path"),
            error=item.get("error"),
        )
        for item in payload.get("attempts", [])
        if isinstance(item, dict)
    ]
    raw_plan_map = payload.get("plan_text_by_round")
    plan_map: dict[str, str] = {}
    if isinstance(raw_plan_map, dict):
        for key, value in raw_plan_map.items():
            if isinstance(key, str) and isinstance(value, str):
                plan_map[key] = value
    return ProgressState(
        relative_path=str(payload["relative_path"]),
        theorem_name=str(payload["theorem_name"]),
        next_attempt_no=int(payload["next_attempt_no"]),
        prev_lean=str(payload["prev_lean"]),
        last_failure=str(payload["last_failure"]),
        last_candidate_path=payload.get("last_candidate_path"),
        plan_text_by_round=plan_map,
        attempts=attempts,
    )


def _reconstruct_plan_text_from_logs(stdout_text: str, stderr_text: str) -> str:
    if stderr_text.strip() and not stdout_text.strip():
        return (
            "Planner request failed. Continue with direct proof repair.\n"
            f"Failure: {stderr_text.strip() or 'request failed'}"
        )
    try:
        plan_text = _extract_model_response_text(stdout_text).strip()
    except ValueError as exc:
        return (
            "Planner output parse failed. Continue with direct proof repair.\n"
            f"Failure: {exc}"
        )
    if not plan_text:
        return "Planner returned empty output. Continue with direct proof repair."
    return plan_text


def _load_existing_plan_text(problem_out_dir: Path, plan_round: int) -> Optional[str]:
    stdout_path = problem_out_dir / f"plan_round{plan_round}.model_stdout.log"
    stderr_path = problem_out_dir / f"plan_round{plan_round}.model_stderr.log"
    if not stdout_path.exists() or not stderr_path.exists():
        return None
    try:
        stdout_text = stdout_path.read_text(encoding="utf-8")
        stderr_text = stderr_path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _reconstruct_plan_text_from_logs(stdout_text, stderr_text)


def _extract_attempt_failure_from_compile_logs(
    compile_stdout_text: str,
    compile_stderr_text: str,
) -> str:
    failure = (compile_stderr_text.strip() + "\n" + compile_stdout_text.strip()).strip()
    if failure:
        return failure
    return "Lean compiler failed without stdout/stderr output."


def _replay_attempt_from_artifacts(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    problem_out_dir: Path,
    attempt_no: int,
    prev_lean: str,
) -> Optional[tuple[AttemptRecord, str, str, Optional[str], bool]]:
    prefix = f"proof_attempt{attempt_no}"
    prompt_path = problem_out_dir / f"{prefix}.prompt.txt"
    stdout_path = problem_out_dir / f"{prefix}.model_stdout.log"
    stderr_path = problem_out_dir / f"{prefix}.model_stderr.log"
    candidate_path = problem_out_dir / f"{prefix}.candidate.lean"
    compile_stdout_path = problem_out_dir / f"{prefix}.compile_stdout.log"
    compile_stderr_path = problem_out_dir / f"{prefix}.compile_stderr.log"

    if not prompt_path.exists():
        return None
    if not stdout_path.exists() or not stderr_path.exists():
        return None

    try:
        stdout_text = stdout_path.read_text(encoding="utf-8")
        stderr_text = stderr_path.read_text(encoding="utf-8")
    except OSError:
        return None

    plan_round = _plan_round_for_attempt(attempt_no, cfg)

    if stderr_text.strip() and not stdout_text.strip():
        failure = stderr_text.strip() or "model request failed"
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="model_request_failed",
                candidate_path=None,
                error=failure,
            ),
            prev_lean,
            failure,
            None,
            False,
        )

    try:
        model_text = _extract_model_response_text(stdout_text)
        payload = _parse_json_object_from_model_text(model_text)
    except ValueError as exc:
        failure = f"Model output parse failure: {exc}"
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="model_parse_failed",
                candidate_path=None,
                error=failure,
            ),
            prev_lean,
            failure,
            None,
            False,
        )

    try:
        extra_imports, proof_body = _extract_candidate_edits(target, payload)
    except ValueError as exc:
        failure = f"Model edit parse failure: {exc}"
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="edit_parse_failed",
                candidate_path=None,
                error=failure,
            ),
            prev_lean,
            failure,
            None,
            False,
        )

    proof_body_policy_failure = _detect_forbidden_proof_body_content(proof_body)
    if proof_body_policy_failure is not None:
        failure = f"Policy failure: {proof_body_policy_failure}."
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="policy_failed",
                candidate_path=None,
                error=failure,
            ),
            prev_lean,
            failure,
            None,
            False,
        )

    candidate_text = _build_candidate_text(
        target,
        extra_imports=extra_imports,
        proof_body=proof_body,
    )

    forbidden_reason = _detect_forbidden_content(candidate_text, target.original_text)
    if forbidden_reason is not None:
        failure = f"Policy failure: {forbidden_reason}."
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="policy_failed",
                candidate_path=None,
                error=failure,
            ),
            candidate_text,
            failure,
            None,
            False,
        )

    if not candidate_path.exists() or not compile_stdout_path.exists() or not compile_stderr_path.exists():
        return None

    try:
        compile_stdout_text = compile_stdout_path.read_text(encoding="utf-8")
        compile_stderr_text = compile_stderr_path.read_text(encoding="utf-8")
    except OSError:
        return None

    compile_res = _compile_candidate(
        lean_path=candidate_path,
        compile_cmd=cfg.compile_cmd,
        cwd=cfg.cwd,
    )
    if compile_res.returncode == 0:
        return (
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="ok",
                candidate_path=str(candidate_path),
                error=None,
            ),
            candidate_text,
            "",
            str(candidate_path),
            True,
        )

    failure = _extract_attempt_failure_from_compile_logs(compile_stdout_text, compile_stderr_text)
    return (
        AttemptRecord(
            attempt_no=attempt_no,
            plan_round=plan_round,
            status="compile_failed",
            candidate_path=str(candidate_path),
            error=failure,
        ),
        candidate_text,
        failure,
        str(candidate_path),
        False,
    )


def _summarize_finished_progress(
    target: ProofTarget,
    *,
    problem_out_dir: Path,
    attempts: list[AttemptRecord],
    attempts_used: int,
    plan_rounds_used: int,
    successful_attempt_no: Optional[int],
    final_lean_path: Optional[str],
    error: Optional[str],
    passed: bool,
) -> ProblemOutcome:
    outcome = ProblemOutcome(
        relative_path=str(target.relative_path),
        theorem_name=target.theorem_name,
        passed=passed,
        attempts_used=attempts_used,
        plan_rounds_used=plan_rounds_used,
        successful_attempt_no=successful_attempt_no,
        final_lean_path=final_lean_path,
        error=error,
        attempts=attempts,
    )
    _write_json(problem_out_dir / "summary.json", dataclasses.asdict(outcome))
    return outcome


def _infer_progress_state_from_artifacts(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    problem_out_dir: Path,
) -> ProgressState | ProblemOutcome:
    attempts: list[AttemptRecord] = []
    plan_text_by_round: dict[str, str] = {}
    prev_lean = target.original_text
    last_failure = "No attempt made."
    last_candidate_path: Optional[str] = None
    total_attempts = _total_attempts(cfg)

    attempt_no = 1
    while attempt_no <= total_attempts:
        plan_round = _plan_round_for_attempt(attempt_no, cfg)
        plan_key = str(plan_round)
        if plan_key not in plan_text_by_round:
            plan_text = _load_existing_plan_text(problem_out_dir, plan_round)
            if plan_text is None:
                break
            plan_text_by_round[plan_key] = plan_text

        replayed = _replay_attempt_from_artifacts(
            target,
            cfg=cfg,
            problem_out_dir=problem_out_dir,
            attempt_no=attempt_no,
            prev_lean=prev_lean,
        )
        if replayed is None:
            break

        record, prev_lean, last_failure, last_candidate_path, succeeded = replayed
        attempts.append(record)
        if succeeded:
            return _summarize_finished_progress(
                target,
                problem_out_dir=problem_out_dir,
                attempts=attempts,
                attempts_used=attempt_no,
                plan_rounds_used=plan_round,
                successful_attempt_no=attempt_no,
                final_lean_path=last_candidate_path,
                error=None,
                passed=True,
            )
        attempt_no += 1

    if attempt_no > total_attempts:
        return _summarize_finished_progress(
            target,
            problem_out_dir=problem_out_dir,
            attempts=attempts,
            attempts_used=total_attempts,
            plan_rounds_used=cfg.max_plan_rounds,
            successful_attempt_no=None,
            final_lean_path=last_candidate_path,
            error=last_failure,
            passed=False,
        )

    return ProgressState(
        relative_path=str(target.relative_path),
        theorem_name=target.theorem_name,
        next_attempt_no=attempt_no,
        prev_lean=prev_lean,
        last_failure=last_failure,
        last_candidate_path=last_candidate_path,
        plan_text_by_round=plan_text_by_round,
        attempts=attempts,
    )


def _fresh_progress_state(target: ProofTarget) -> ProgressState:
    return ProgressState(
        relative_path=str(target.relative_path),
        theorem_name=target.theorem_name,
        next_attempt_no=1,
        prev_lean=target.original_text,
        last_failure="No attempt made.",
        last_candidate_path=None,
        plan_text_by_round={},
        attempts=[],
    )


def _load_resume_state(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    problem_out_dir: Path,
) -> ProgressState | ProblemOutcome:
    summary_path = problem_out_dir / "summary.json"
    if cfg.resume and summary_path.exists():
        return _load_problem_outcome(summary_path)

    progress_path = problem_out_dir / "progress.json"
    if cfg.resume and progress_path.exists():
        return _load_progress_state(progress_path)

    if cfg.resume and problem_out_dir.exists():
        inferred = _infer_progress_state_from_artifacts(
            target,
            cfg=cfg,
            problem_out_dir=problem_out_dir,
        )
        if isinstance(inferred, ProgressState):
            _write_progress_state(problem_out_dir, inferred)
        return inferred

    state = _fresh_progress_state(target)
    _write_progress_state(problem_out_dir, state)
    return state


def _run_plan_round(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    problem_out_dir: Path,
    plan_round: int,
    prev_lean: Optional[str],
    failure_reason: Optional[str],
    lsp_context: str = "",
) -> str:
    prompt = _build_plan_prompt(
        target,
        plan_round=plan_round,
        prev_lean=prev_lean,
        failure_reason=failure_reason,
        lsp_context=lsp_context,
    )
    prefix = f"plan_round{plan_round}"
    _write_text(problem_out_dir / f"{prefix}.prompt.txt", prompt)
    stdout_text, stderr_text, returncode = _call_openrouter_text(
        prompt=prompt,
        model=cfg.planner_model,
        reasoning_effort=cfg.planner_reasoning_effort,
        api_key_env=cfg.api_key_env,
        openrouter_base_url=cfg.openrouter_base_url,
        openrouter_timeout_s=cfg.openrouter_timeout_s,
        openrouter_max_retries=cfg.openrouter_max_retries,
        use_codex_exec=cfg.use_codex_exec,
        codex_exec_model=cfg.codex_exec_model,
        codex_exec_sandbox=cfg.codex_exec_sandbox,
        use_claude_cli=cfg.use_claude_cli,
        claude_cli_cmd=cfg.claude_cli_cmd,
        cwd=cfg.cwd,
    )
    _write_text(problem_out_dir / f"{prefix}.model_stdout.log", stdout_text)
    _write_text(problem_out_dir / f"{prefix}.model_stderr.log", stderr_text)

    if returncode != 0:
        return (
            "Planner request failed. Continue with direct proof repair.\n"
            f"Failure: {stderr_text.strip() or 'request failed'}"
        )

    try:
        plan_text = _extract_model_response_text(stdout_text).strip()
    except ValueError as exc:
        return (
            "Planner output parse failed. Continue with direct proof repair.\n"
            f"Failure: {exc}"
        )

    if not plan_text:
        return "Planner returned empty output. Continue with direct proof repair."
    # Emit the plan so the TypeScript UI can show it in real time
    print("[AUTOLEAN] [PLAN_START]", file=sys.stderr, flush=True)
    for _pl in plan_text.splitlines():
        print(f"[AUTOLEAN] {_pl}", file=sys.stderr, flush=True)
    print("[AUTOLEAN] [PLAN_END]", file=sys.stderr, flush=True)
    return plan_text


def _build_agent_session_prompt(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    plan_text: str,
    session_no: int,
    candidate_path: Path,
    prev_lean: Optional[str] = None,
    prev_failure: Optional[str] = None,
    lsp_context: str = "",
) -> str:
    """Build the prompt for an agent-mode proving session.

    The MathCode agent will use Read, Grep, Bash, Write tools to prove
    the theorem interactively, with multiple compile-fix cycles.
    """
    vault_section = ""
    if cfg.vault_dir:
        vault_section = f"""
VAULT (your knowledge base of Mathlib lemmas from previous sessions):
  Search: use Grep to search {cfg.vault_dir}/Lemmas/ for relevant lemmas by keyword
  Read: use Read to read a specific lemma file for its full Lean definition
  Example: Grep for "Nat.add" in {cfg.vault_dir}/Lemmas/ to find addition lemmas
"""

    prev_section = ""
    if prev_lean and prev_failure:
        prev_section = f"""
PREVIOUS SESSION (session {session_no - 1}) FAILED:

Previous best attempt:
```lean
{prev_lean}
```

Last compile error:
```
{prev_failure[:3000]}
```

{lsp_context}

Try a DIFFERENT approach from the previous session.
"""

    # Build the compile command — use LSP wrapper if available
    autolean_dir = Path(__file__).resolve().parent.parent
    compile_script = autolean_dir / "scripts" / "compile_with_lsp.py"
    python_bin = sys.executable
    if cfg.use_lsp and compile_script.exists():
        compile_command = (
            f"{python_bin} {compile_script} {candidate_path}"
            f" --cwd {cfg.cwd}"
            f" --compile-cmd '{cfg.compile_cmd}'"
            f" --lsp-timeout {cfg.lsp_timeout_s}"
        )
        compile_note = """The compile command returns structured output:
  - COMPILE_OK if successful
  - COMPILE_FAILED followed by [error], [goal], and [raw] lines
  - [error] lines show exact location and message
  - [goal] lines show the proof state at the error (what remains to prove)
  - [raw] lines show raw compiler output"""
    else:
        compile_command = f"cd {cfg.cwd} && {cfg.compile_cmd.replace('{file}', str(candidate_path))}"
        compile_note = "Check stderr for compiler errors."

    max_compiles = cfg.agent_max_compiles

    return f"""You are proving a Lean 4 theorem interactively. You have tool access.

AVAILABLE TOOLS:
  - Grep: search files by content (use to find lemmas in the vault)
  - Read: read file contents (use to read lemma definitions or error details)
  - Write: write file contents (use to write your proof candidate)
  - Bash: run shell commands (use to compile Lean files)

THEOREM TO PROVE:
```lean
{target.original_text}
```

The theorem name is `{target.theorem_name}`.
The file structure is frozen — only the proof body (after `by`) can change.
You may add extra `import` lines if needed.

PLANNER NOTES (proof strategy):
{plan_text}
{vault_section}{prev_section}
COMPILE COMMAND:
```
{compile_command}
```
{compile_note}

COMPILE LIMIT: You have at most {max_compiles} compile attempts in this session.

WORKFLOW:
1. Search the vault for relevant lemmas (if vault is available)
2. Read promising lemma definitions to understand their types
3. Write your complete proof to: {candidate_path}
   The file must start with the import block, include the full theorem, and end properly.
4. Compile using the command above
5. If compilation fails:
   - Read the [error] and [goal] output carefully
   - Search the vault for lemmas that address the specific error
   - Write a fixed version to the same file
   - Compile again
6. Repeat until the proof compiles or you hit the compile limit

RULES:
- The theorem name must stay exactly `{target.theorem_name}`
- The theorem statement must NOT change
- Remove all `sorry` and `admit` from the proof
- Do NOT introduce `axiom`, `constant`, or `postulate`
- When the proof compiles successfully, say PROOF_FOUND
- If you hit the compile limit or cannot find a proof, say PROOF_NOT_FOUND with a brief summary
"""


def _run_agent_session(
    target: ProofTarget,
    *,
    cfg: ProofConfig,
    problem_out_dir: Path,
    plan_text: str,
    session_no: int,
    prev_lean: Optional[str] = None,
    prev_failure: Optional[str] = None,
    lsp_context: str = "",
) -> tuple[bool, Optional[str], Optional[str]]:
    """Run a single agent-mode proving session.

    Returns (success, candidate_text, error_summary).
    """
    candidate_path = problem_out_dir / f"agent_session{session_no}.candidate.lean"

    prompt = _build_agent_session_prompt(
        target,
        cfg=cfg,
        plan_text=plan_text,
        session_no=session_no,
        candidate_path=candidate_path,
        prev_lean=prev_lean,
        prev_failure=prev_failure,
        lsp_context=lsp_context,
    )

    # Save the prompt for debugging
    _write_text(problem_out_dir / f"agent_session{session_no}.prompt.txt", prompt)

    # Run the MathCode agent session
    res = _call_claude_cli(
        prompt=prompt,
        claude_cli_cmd=cfg.claude_cli_cmd,
        workdir=cfg.cwd,
        timeout_s=cfg.agent_session_timeout,
    )

    # Save agent output
    _write_text(problem_out_dir / f"agent_session{session_no}.stdout.log", res.stdout)
    _write_text(problem_out_dir / f"agent_session{session_no}.stderr.log", res.stderr)

    # Check if the agent wrote a candidate file
    if not candidate_path.exists():
        return False, None, res.stdout[-2000:] if res.stdout else "Agent did not write a candidate file."

    candidate_text = candidate_path.read_text(encoding="utf-8")

    # Verify compilation independently (don't trust the agent's claim)
    compile_res = _compile_candidate(
        lean_path=candidate_path,
        compile_cmd=cfg.compile_cmd,
        cwd=cfg.cwd,
    )

    # Save compile logs
    _write_text(problem_out_dir / f"agent_session{session_no}.compile_stdout.log", compile_res.stdout)
    _write_text(problem_out_dir / f"agent_session{session_no}.compile_stderr.log", compile_res.stderr)

    if compile_res.returncode == 0:
        # Verify no sorry/admit
        forbidden = _detect_forbidden_content(candidate_text, target.original_text)
        if forbidden:
            return False, candidate_text, f"Policy failure: {forbidden}"
        return True, candidate_text, None

    failure = _extract_attempt_failure_from_compile_logs(compile_res.stdout, compile_res.stderr)
    return False, candidate_text, failure


def _prove_target(target: ProofTarget, *, cfg: ProofConfig) -> ProblemOutcome:
    problem_out_dir = cfg.out_dir / target.relative_path.with_suffix("")
    ensure_dir(problem_out_dir)

    loaded = _load_resume_state(target, cfg=cfg, problem_out_dir=problem_out_dir)
    if isinstance(loaded, ProblemOutcome):
        return loaded

    state = loaded
    total_attempts = _total_attempts(cfg)
    attempt_no = state.next_attempt_no
    prev_lean = state.prev_lean
    last_failure = state.last_failure
    last_candidate_path: Optional[Path] = (
        Path(state.last_candidate_path) if state.last_candidate_path is not None else None
    )
    attempts = list(state.attempts)
    plan_text_by_round = dict(state.plan_text_by_round)

    def _progress(msg: str) -> None:
        print(f"[AUTOLEAN] {msg}", file=sys.stderr, flush=True)

    # ---- LSP session (optional) ----
    lsp_session = None
    if cfg.use_lsp:
        try:
            from autolean.lean_lsp import LeanLSPSession
            lsp_session = LeanLSPSession(
                project_path=cfg.cwd,
                lsp_timeout_s=cfg.lsp_timeout_s,
            )
            _progress("LSP session started.")
        except Exception as exc:
            _progress(f"LSP startup failed ({exc}), falling back to non-LSP mode.")
            lsp_session = None

    def _close_lsp() -> None:
        nonlocal lsp_session
        if lsp_session is not None:
            try:
                lsp_session.close()
            except Exception:
                pass
            lsp_session = None

    lsp_repair_context = ""  # populated after each compile failure via LSP

    # Load vault knowledge base — cached lemmas from previous sessions
    vault_knowledge_text = ""
    if cfg.lemma_cache_dir:
        try:
            from autolean.lean_lsp import load_vault_knowledge, format_vault_knowledge
            vault_results = load_vault_knowledge(
                cfg.lemma_cache_dir,
                theorem_statement=target.normalized_header,
            )
            vault_knowledge_text = format_vault_knowledge(vault_results)
            if vault_results:
                _progress(f"Vault: loaded {len(vault_results)} relevant lemma(s) from previous sessions.")
        except Exception:
            pass

    while attempt_no <= total_attempts:
        _progress(f"=== Proof attempt {attempt_no}/{total_attempts} ===")
        plan_round = _plan_round_for_attempt(attempt_no, cfg)
        plan_key = str(plan_round)
        if plan_key not in plan_text_by_round:
            _progress(f"Running planner (round {plan_round})...")
            # LSP: search for verified Mathlib lemmas
            plan_lsp_context = ""
            if lsp_session is not None:
                try:
                    from autolean.lean_lsp import format_lemma_search_results
                    nl_query = target.normalized_header.removeprefix("theorem ").removeprefix("lemma ")
                    _progress(f"LSP: searching lemmas for '{nl_query[:60]}...'")
                    lemma_results = lsp_session.search_lemmas(nl_query)
                    plan_lsp_context = format_lemma_search_results(lemma_results)
                    if lemma_results:
                        _progress(f"LSP: found {len(lemma_results)} verified lemma(s).")
                        # Save to vault immediately
                        if cfg.lemma_cache_dir:
                            from autolean.lean_lsp import save_lemma_to_vault
                            for r in lemma_results:
                                if r.type_sig:
                                    save_lemma_to_vault(cfg.lemma_cache_dir, r.name, f"{r.name} : {r.type_sig}")
                except Exception as exc:
                    _progress(f"LSP lemma search failed: {exc}")

            # Combine vault knowledge + fresh search results for planner
            plan_lsp_context = "\n\n".join(filter(None, [vault_knowledge_text, plan_lsp_context]))

            checkpoint = ProgressState(
                relative_path=str(target.relative_path),
                theorem_name=target.theorem_name,
                next_attempt_no=attempt_no,
                prev_lean=prev_lean,
                last_failure=last_failure,
                last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                plan_text_by_round=plan_text_by_round,
                attempts=attempts,
            )
            _write_progress_state(problem_out_dir, checkpoint)
            plan_text_by_round[plan_key] = _run_plan_round(
                target,
                cfg=cfg,
                problem_out_dir=problem_out_dir,
                plan_round=plan_round,
                prev_lean=prev_lean,
                failure_reason=None
                if attempt_no == 1 and last_failure == "No attempt made."
                else last_failure,
                lsp_context=plan_lsp_context,
            )
            checkpoint = ProgressState(
                relative_path=str(target.relative_path),
                theorem_name=target.theorem_name,
                next_attempt_no=attempt_no,
                prev_lean=prev_lean,
                last_failure=last_failure,
                last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                plan_text_by_round=plan_text_by_round,
                attempts=attempts,
            )
            _write_progress_state(problem_out_dir, checkpoint)

        plan_text = plan_text_by_round[plan_key]

        checkpoint = ProgressState(
            relative_path=str(target.relative_path),
            theorem_name=target.theorem_name,
            next_attempt_no=attempt_no,
            prev_lean=prev_lean,
            last_failure=last_failure,
            last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
            plan_text_by_round=plan_text_by_round,
            attempts=attempts,
        )
        _write_progress_state(problem_out_dir, checkpoint)

        # ---- Agent-mode proving: full MathCode chat session ----
        if cfg.agent_prove:
            _progress(f"Running agent session {attempt_no}...")
            session_success, session_candidate, session_error = _run_agent_session(
                target,
                cfg=cfg,
                problem_out_dir=problem_out_dir,
                plan_text=plan_text,
                session_no=attempt_no,
                prev_lean=prev_lean if attempt_no > 1 else None,
                prev_failure=last_failure if attempt_no > 1 else None,
                lsp_context=lsp_repair_context,
            )

            if session_candidate:
                prev_lean = session_candidate

            if session_success:
                _progress(f"Agent session {attempt_no} PASSED! Proof found!")
                candidate_path = problem_out_dir / f"agent_session{attempt_no}.candidate.lean"
                outcome = _summarize_finished_progress(
                    target,
                    problem_out_dir=problem_out_dir,
                    attempts=attempts + [AttemptRecord(
                        attempt_no=attempt_no,
                        plan_round=plan_round,
                        status="ok",
                        candidate_path=str(candidate_path),
                        error=None,
                    )],
                    attempts_used=attempt_no,
                    plan_rounds_used=plan_round,
                    successful_attempt_no=attempt_no,
                    final_lean_path=str(candidate_path),
                    error=None,
                    passed=True,
                )

                # Save lemma discoveries to vault
                if cfg.lemma_cache_dir and lsp_session is not None:
                    try:
                        from autolean.lean_lsp import save_lemma_to_vault
                        proof_refs = re.findall(
                            r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b",
                            session_candidate,
                        )
                        unique_refs = sorted(set(proof_refs) - {"Mathlib", "Formalizations"})
                        if unique_refs:
                            defs = lsp_session.query_lemma_definitions(unique_refs)
                            for name, defn in defs.items():
                                save_lemma_to_vault(cfg.lemma_cache_dir, name, defn)
                    except Exception:
                        pass

                _close_lsp()
                return outcome

            # Session failed
            last_failure = session_error or "Agent session failed without error details."
            _progress(f"Agent session {attempt_no} FAILED.")
            attempts.append(AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="compile_failed",
                candidate_path=str(problem_out_dir / f"agent_session{attempt_no}.candidate.lean")
                    if session_candidate else None,
                error=last_failure,
            ))

            # LSP: get structured diagnostics + goal at error for next session
            if lsp_session is not None and session_candidate:
                try:
                    from autolean.lean_lsp import (
                        format_structured_diagnostics,
                        format_goal_at_error,
                    )
                    lsp_ok, lsp_diags = lsp_session.get_structured_diagnostics(session_candidate)
                    structured = format_structured_diagnostics(lsp_diags)
                    if structured:
                        last_failure = structured

                    first_error = next((d for d in lsp_diags if d.severity == "error"), None)
                    if first_error is not None:
                        error_goal = lsp_session.get_goal_at_position(
                            session_candidate, first_error.line, first_error.column
                        )
                        goal_text = format_goal_at_error(error_goal)
                        if goal_text:
                            lsp_repair_context = goal_text
                except Exception:
                    pass

            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue  # next session

        # ---- Standard script-attempt mode ----

        # LSP: search for lemmas tailored to this attempt's context
        attempt_lsp_lemmas = ""
        if lsp_session is not None:
            try:
                from autolean.lean_lsp import format_lemma_search_results
                if attempt_no == 1:
                    # First attempt: search based on theorem header
                    search_query = target.normalized_header.removeprefix("theorem ").removeprefix("lemma ")
                else:
                    # Repair attempts: search based on the error to find fixing lemmas
                    # Extract identifiers from the failure — both uppercase (Nat.cast)
                    # and lowercase (add_comm, mul_zero) Lean/Mathlib names
                    error_names = re.findall(
                        r"(?:unknown identifier|expected|got|type mismatch)\s*[`']*([A-Za-z_][A-Za-z0-9_.]*)",
                        last_failure,
                    )
                    if error_names:
                        search_query = " ".join(error_names[:3])
                    else:
                        # Fall back to qualified dot-names from error
                        dot_names = re.findall(
                            r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*)\b",
                            last_failure,
                        )
                        if dot_names:
                            search_query = " ".join(dot_names[:3])
                        else:
                            search_query = target.normalized_header.removeprefix("theorem ").removeprefix("lemma ")

                lemma_results = lsp_session.search_lemmas(search_query)
                attempt_lsp_lemmas = format_lemma_search_results(lemma_results)
                # Save to vault immediately
                if cfg.lemma_cache_dir and lemma_results:
                    from autolean.lean_lsp import save_lemma_to_vault
                    for r in lemma_results:
                        if r.type_sig:
                            save_lemma_to_vault(cfg.lemma_cache_dir, r.name, f"{r.name} : {r.type_sig}")
            except Exception:
                pass  # silent fallback

        # Combine LSP lemma search with repair context (goal at error)
        combined_lsp = "\n\n".join(filter(None, [vault_knowledge_text, attempt_lsp_lemmas, lsp_repair_context]))

        if attempt_no == 1:
            prompt = _build_initial_proof_prompt(
                target,
                plan_round=plan_round,
                total_plan_rounds=cfg.max_plan_rounds,
                attempt_no=attempt_no,
                total_attempts=total_attempts,
                plan_text=plan_text,
                lsp_context=combined_lsp,
            )
        else:
            prompt = _build_repair_prompt(
                target,
                plan_round=plan_round,
                total_plan_rounds=cfg.max_plan_rounds,
                attempt_no=attempt_no,
                total_attempts=total_attempts,
                plan_text=plan_text,
                prev_lean=prev_lean,
                failure_reason=last_failure,
                lsp_context=combined_lsp,
            )

        prefix = f"proof_attempt{attempt_no}"
        _write_text(problem_out_dir / f"{prefix}.prompt.txt", prompt)
        _progress(f"Running prover model (attempt {attempt_no})...")
        stdout_text, stderr_text, returncode = _call_openrouter_text(
            prompt=prompt,
            model=cfg.prover_model,
            reasoning_effort=cfg.prover_reasoning_effort,
            api_key_env=cfg.api_key_env,
            openrouter_base_url=cfg.openrouter_base_url,
            openrouter_timeout_s=cfg.openrouter_timeout_s,
            openrouter_max_retries=cfg.openrouter_max_retries,
            use_codex_exec=cfg.use_codex_exec,
            codex_exec_model=cfg.codex_exec_model,
            codex_exec_sandbox=cfg.codex_exec_sandbox,
            use_claude_cli=cfg.use_claude_cli,
            claude_cli_cmd=cfg.claude_cli_cmd,
            cwd=cfg.cwd,
        )
        _write_text(problem_out_dir / f"{prefix}.model_stdout.log", stdout_text)
        _write_text(problem_out_dir / f"{prefix}.model_stderr.log", stderr_text)

        if returncode != 0:
            _progress(f"Prover model FAILED (attempt {attempt_no}).")
            last_failure = stderr_text.strip() or "model request failed"
            attempts.append(
                AttemptRecord(
                    attempt_no=attempt_no,
                    plan_round=plan_round,
                    status="model_request_failed",
                    candidate_path=None,
                    error=last_failure,
                )
            )
            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue

        try:
            model_text = _extract_model_response_text(stdout_text)
            payload = _parse_json_object_from_model_text(model_text)
        except ValueError as exc:
            last_failure = f"Model output parse failure: {exc}"
            attempts.append(
                AttemptRecord(
                    attempt_no=attempt_no,
                    plan_round=plan_round,
                    status="model_parse_failed",
                    candidate_path=None,
                    error=last_failure,
                )
            )
            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue

        try:
            extra_imports, proof_body = _extract_candidate_edits(target, payload)
        except ValueError as exc:
            last_failure = f"Model edit parse failure: {exc}"
            attempts.append(
                AttemptRecord(
                    attempt_no=attempt_no,
                    plan_round=plan_round,
                    status="edit_parse_failed",
                    candidate_path=None,
                    error=last_failure,
                )
            )
            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue

        proof_body_policy_failure = _detect_forbidden_proof_body_content(proof_body)
        if proof_body_policy_failure is not None:
            last_failure = f"Policy failure: {proof_body_policy_failure}."
            attempts.append(
                AttemptRecord(
                    attempt_no=attempt_no,
                    plan_round=plan_round,
                    status="policy_failed",
                    candidate_path=None,
                    error=last_failure,
                )
            )
            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue

        candidate_text = _build_candidate_text(
            target,
            extra_imports=extra_imports,
            proof_body=proof_body,
        )

        forbidden_reason = _detect_forbidden_content(candidate_text, target.original_text)
        if forbidden_reason is not None:
            prev_lean = candidate_text
            last_failure = f"Policy failure: {forbidden_reason}."
            attempts.append(
                AttemptRecord(
                    attempt_no=attempt_no,
                    plan_round=plan_round,
                    status="policy_failed",
                    candidate_path=None,
                    error=last_failure,
                )
            )
            attempt_no += 1
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no,
                    prev_lean=prev_lean,
                    last_failure=last_failure,
                    last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                    plan_text_by_round=plan_text_by_round,
                    attempts=attempts,
                ),
            )
            continue

        candidate_path = problem_out_dir / f"{prefix}.candidate.lean"
        _write_text(candidate_path, candidate_text)
        last_candidate_path = candidate_path
        prev_lean = candidate_text
        _progress(f"Candidate generated ({len(candidate_text)} chars), compiling...")
        # Emit the candidate Lean code so the TypeScript UI can show it
        print("[AUTOLEAN] [CANDIDATE_START]", file=sys.stderr, flush=True)
        for _cl in candidate_text.splitlines():
            print(f"[AUTOLEAN] {_cl}", file=sys.stderr, flush=True)
        print("[AUTOLEAN] [CANDIDATE_END]", file=sys.stderr, flush=True)

        compile_res = _compile_candidate(
            lean_path=candidate_path,
            compile_cmd=cfg.compile_cmd,
            cwd=cfg.cwd,
        )
        _write_text(problem_out_dir / f"{prefix}.compile_stdout.log", compile_res.stdout)
        _write_text(problem_out_dir / f"{prefix}.compile_stderr.log", compile_res.stderr)

        if compile_res.returncode == 0:
            _progress(f"Compilation PASSED (attempt {attempt_no})! Proof found!")
            outcome = _summarize_finished_progress(
                target,
                problem_out_dir=problem_out_dir,
                attempts=attempts
                + [
                    AttemptRecord(
                        attempt_no=attempt_no,
                        plan_round=plan_round,
                        status="ok",
                        candidate_path=str(candidate_path),
                        error=None,
                    )
                ],
                attempts_used=attempt_no,
                plan_rounds_used=plan_round,
                successful_attempt_no=attempt_no,
                final_lean_path=str(candidate_path),
                error=None,
                passed=True,
            )
            _write_progress_state(
                problem_out_dir,
                ProgressState(
                    relative_path=str(target.relative_path),
                    theorem_name=target.theorem_name,
                    next_attempt_no=attempt_no + 1,
                    prev_lean=prev_lean,
                    last_failure="",
                    last_candidate_path=str(candidate_path),
                    plan_text_by_round=plan_text_by_round,
                    attempts=outcome.attempts,
                ),
            )
            # Save lemma discoveries to vault knowledge base
            if cfg.lemma_cache_dir and lsp_session is not None:
                try:
                    from autolean.lean_lsp import save_lemma_to_vault
                    # Extract qualified names from the proven code
                    proof_refs = re.findall(
                        r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b",
                        candidate_text,
                    )
                    unique_refs = sorted(set(proof_refs) - {"Mathlib", "Formalizations"})
                    if unique_refs:
                        defs = lsp_session.query_lemma_definitions(unique_refs)
                        for name, defn in defs.items():
                            save_lemma_to_vault(cfg.lemma_cache_dir, name, defn)
                except Exception:
                    pass

            _close_lsp()
            return outcome

        # Extract error feedback — use LSP for structured diagnostics if available
        lsp_repair_context = ""
        if lsp_session is not None:
            try:
                from autolean.lean_lsp import (
                    format_structured_diagnostics,
                    format_goal_at_error,
                )
                lsp_ok, lsp_diags = lsp_session.get_structured_diagnostics(candidate_text)
                structured = format_structured_diagnostics(lsp_diags)
                if structured:
                    last_failure = structured
                else:
                    last_failure = _extract_attempt_failure_from_compile_logs(compile_res.stdout, compile_res.stderr)

                # Get goal state at first error location
                first_error = next((d for d in lsp_diags if d.severity == "error"), None)
                if first_error is not None:
                    error_goal = lsp_session.get_goal_at_position(
                        candidate_text, first_error.line, first_error.column
                    )
                    goal_text = format_goal_at_error(error_goal)
                    if goal_text:
                        lsp_repair_context = goal_text
            except Exception:
                last_failure = _extract_attempt_failure_from_compile_logs(compile_res.stdout, compile_res.stderr)
        else:
            last_failure = _extract_attempt_failure_from_compile_logs(compile_res.stdout, compile_res.stderr)

        _progress(f"Compilation FAILED (attempt {attempt_no})")
        # Emit a snippet of compiler errors so the UI can show what went wrong
        print("[AUTOLEAN] [COMPILE_ERROR_START]", file=sys.stderr, flush=True)
        for _el in last_failure.splitlines()[:30]:
            print(f"[AUTOLEAN] {_el}", file=sys.stderr, flush=True)
        print("[AUTOLEAN] [COMPILE_ERROR_END]", file=sys.stderr, flush=True)
        attempts.append(
            AttemptRecord(
                attempt_no=attempt_no,
                plan_round=plan_round,
                status="compile_failed",
                candidate_path=str(candidate_path),
                error=last_failure,
            )
        )
        attempt_no += 1
        _write_progress_state(
            problem_out_dir,
            ProgressState(
                relative_path=str(target.relative_path),
                theorem_name=target.theorem_name,
                next_attempt_no=attempt_no,
                prev_lean=prev_lean,
                last_failure=last_failure,
                last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
                plan_text_by_round=plan_text_by_round,
                attempts=attempts,
            ),
        )

    _progress(f"FAILED: No proof found after {total_attempts} attempts across {cfg.max_plan_rounds} plan rounds.")
    outcome = _summarize_finished_progress(
        target,
        problem_out_dir=problem_out_dir,
        attempts=attempts,
        attempts_used=total_attempts,
        plan_rounds_used=cfg.max_plan_rounds,
        successful_attempt_no=None,
        final_lean_path=str(last_candidate_path) if last_candidate_path is not None else None,
        error=last_failure,
        passed=False,
    )
    _write_progress_state(
        problem_out_dir,
        ProgressState(
            relative_path=str(target.relative_path),
            theorem_name=target.theorem_name,
            next_attempt_no=total_attempts + 1,
            prev_lean=prev_lean,
            last_failure=last_failure,
            last_candidate_path=str(last_candidate_path) if last_candidate_path is not None else None,
            plan_text_by_round=plan_text_by_round,
            attempts=attempts,
        ),
    )
    _close_lsp()
    return outcome


def _write_report(
    *,
    out_dir: Path,
    cfg: ProofConfig,
    results: list[ProblemOutcome],
    invalid_targets: list[Path],
    skipped_complete: list[Path],
) -> None:
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    summary_payload = {
        "input_dir": str(cfg.input_dir),
        "out_dir": str(cfg.out_dir),
        "planner_model": cfg.planner_model,
        "planner_reasoning_effort": cfg.planner_reasoning_effort,
        "prover_model": cfg.prover_model,
        "prover_reasoning_effort": cfg.prover_reasoning_effort,
        "attempts_before_replan": cfg.attempts_before_replan,
        "max_plan_rounds": cfg.max_plan_rounds,
        "workers": cfg.workers,
        "problem_count": len(results),
        "problems_passed": passed,
        "problems_failed": failed,
        "invalid_targets": [str(path) for path in invalid_targets],
        "skipped_complete": [str(path) for path in skipped_complete],
        "results": [dataclasses.asdict(item) for item in results],
    }
    _write_json(out_dir / "proof_summary.json", summary_payload)

    lines = [
        "Autolean Proof Completion Report (Planner/Replanner Pipeline)",
        f"Input: {cfg.input_dir}",
        f"Output: {cfg.out_dir}",
        f"Planner model: {cfg.planner_model}",
        f"Planner reasoning effort: {cfg.planner_reasoning_effort}",
        f"Prover model: {cfg.prover_model}",
        f"Prover reasoning effort: {cfg.prover_reasoning_effort}",
        f"Attempts before replan: {cfg.attempts_before_replan}",
        f"Max plan rounds: {cfg.max_plan_rounds}",
        f"Workers: {cfg.workers}",
        f"Problems processed: {len(results)}",
        f"Problems passed: {passed}",
        f"Problems failed: {failed}",
        f"Invalid targets skipped: {len(invalid_targets)}",
        f"Already-complete targets skipped: {len(skipped_complete)}",
        "",
        "Per-problem results:",
    ]
    for item in sorted(results, key=lambda result: result.relative_path):
        status = "passed" if item.passed else "failed"
        lines.append(
            f"- {item.relative_path}: {status}; attempts_used={item.attempts_used}; "
            f"plan_rounds_used={item.plan_rounds_used}"
        )
    if invalid_targets:
        lines.extend(["", "Invalid targets:"])
        lines.extend(f"- {path}" for path in invalid_targets)
    if skipped_complete:
        lines.extend(["", "Skipped already-complete targets:"])
        lines.extend(f"- {path}" for path in skipped_complete)
    _write_text(out_dir / "proof_report.txt", "\n".join(lines).rstrip() + "\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Complete proofs for existing Lean formalizations with a single theorem-local agent: "
            "plan with GPT, prove/repair with Gemini, then optionally replan and try again."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("A_evaled_lean_formalizations"),
        help="Root directory containing existing Lean files. The script searches recursively.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("proof_runs_replan"),
        help="Directory where proof attempts, logs, and reports are written.",
    )
    parser.add_argument(
        "--planner-model",
        type=str,
        default="openai/gpt-5.4",
        help="OpenRouter model name for planning and replanning.",
    )
    parser.add_argument(
        "--planner-reasoning-effort",
        choices=_REASONING_CHOICES,
        default="xhigh",
        help="Reasoning effort for the planning model.",
    )
    parser.add_argument(
        "--prover-model",
        type=str,
        default="google/gemini-3-flash-preview",
        help="OpenRouter model name for proof generation and repair.",
    )
    parser.add_argument(
        "--prover-reasoning-effort",
        choices=_REASONING_CHOICES,
        default="xhigh",
        help="Reasoning effort for the proving model.",
    )
    parser.add_argument(
        "--attempts-before-replan",
        type=int,
        default=5,
        help="Number of prove/repair attempts before asking the planner for a new plan.",
    )
    parser.add_argument(
        "--max-plan-rounds",
        type=int,
        default=2,
        help="Maximum number of planning rounds per theorem (default: 2 => 5 attempts + replan + 5 attempts).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel workers across different Lean files.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Resume from existing theorem outputs in --out-dir. Finished theorem folders with "
            "summary.json are skipped; unfinished theorem folders resume from progress.json or "
            "from existing attempt artifacts when possible."
        ),
    )
    parser.add_argument(
        "--include-complete",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Also process Lean files that do not currently contain `sorry`/`admit`.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of target Lean files to process.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show a progress bar.",
    )
    parser.add_argument(
        "--api-key-name",
        type=str,
        choices=["PRINCIPIA_KEY", "AUTOLEAN"],
        default="PRINCIPIA_KEY",
        help="Environment variable name used for OpenRouter API key lookup.",
    )
    parser.add_argument(
        "--openrouter-base-url",
        type=str,
        default="https://openrouter.ai/api/v1",
        help="OpenRouter API base URL.",
    )
    parser.add_argument(
        "--openrouter-timeout-s",
        type=int,
        default=180,
        help="OpenRouter request timeout in seconds.",
    )
    parser.add_argument(
        "--openrouter-max-retries",
        type=int,
        default=2,
        help="Retry count for transient OpenRouter request failures.",
    )
    parser.add_argument(
        "--use-codex-exec",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use local `codex exec` for model calls instead of OpenRouter API.",
    )
    parser.add_argument(
        "--codex-exec-model",
        type=str,
        default=None,
        help="Model to use with `codex exec` (e.g., 'codex-mini-latest').",
    )
    parser.add_argument(
        "--codex-exec-sandbox",
        type=str,
        default="read-only",
        choices=["read-only", "workspace-write", "danger-full-access"],
        help="Sandbox mode passed to `codex exec`.",
    )
    parser.add_argument(
        "--use-claude-cli",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use Claude CLI (headless -p mode) for model calls instead of OpenRouter API.",
    )
    parser.add_argument(
        "--claude-cli-cmd",
        type=str,
        default="",
        help="Full command to invoke Claude CLI in headless mode (e.g. '/path/to/mathcode -p').",
    )
    parser.add_argument(
        "--compile-cmd",
        type=str,
        default="lake env lean {file}",
        help="Compile command template. Must include '{file}'.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory where Lean compilation runs.",
    )
    parser.add_argument(
        "--use-lsp",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use Lean LSP for lemma search, structured errors, and goal states.",
    )
    parser.add_argument(
        "--lsp-timeout-s",
        type=float,
        default=120.0,
        help="Per-operation timeout for LSP requests in seconds (default: 120).",
    )
    parser.add_argument(
        "--lemma-cache-dir",
        type=Path,
        default=None,
        help="Directory for lemma_cache.json (vault knowledge base). Read before proving, write after.",
    )
    parser.add_argument(
        "--agent-prove",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use agent-mode proving: each session is a full MathCode chat with tool access.",
    )
    parser.add_argument(
        "--agent-session-timeout",
        type=int,
        default=600,
        help="Timeout per agent proving session in seconds (default: 600).",
    )
    parser.add_argument(
        "--agent-max-compiles",
        type=int,
        default=5,
        help="Maximum compile attempts per agent session (default: 5).",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Obsidian vault directory for agent-mode proving (agent reads Lemmas/ via Grep).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    input_dir = args.input_dir.resolve()
    out_dir = args.out_dir.resolve()
    compile_cwd = args.cwd.resolve()

    if "{file}" not in str(args.compile_cmd):
        print("Error: --compile-cmd must include '{file}'.", file=sys.stderr)
        return 2
    if not input_dir.is_dir():
        print(f"Error: input directory does not exist: {input_dir}", file=sys.stderr)
        return 2
    if not compile_cwd.is_dir():
        print(f"Error: compile cwd does not exist: {compile_cwd}", file=sys.stderr)
        return 2
    if args.attempts_before_replan < 1:
        print("Error: --attempts-before-replan must be >= 1.", file=sys.stderr)
        return 2
    if args.max_plan_rounds < 1:
        print("Error: --max-plan-rounds must be >= 1.", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("Error: --workers must be >= 1.", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("Error: --limit must be >= 1.", file=sys.stderr)
        return 2

    ensure_dir(out_dir)
    skip_subtree = out_dir if _is_relative_to(out_dir, input_dir) else None

    targets, invalid_targets, skipped_complete = _load_targets(
        input_dir=input_dir,
        include_complete=bool(args.include_complete),
        skip_subtree=skip_subtree,
        limit=args.limit,
    )

    cfg = ProofConfig(
        input_dir=input_dir,
        out_dir=out_dir,
        planner_model=str(args.planner_model),
        planner_reasoning_effort=str(args.planner_reasoning_effort),
        prover_model=str(args.prover_model),
        prover_reasoning_effort=str(args.prover_reasoning_effort),
        attempts_before_replan=int(args.attempts_before_replan),
        max_plan_rounds=int(args.max_plan_rounds),
        workers=int(args.workers),
        resume=bool(args.resume),
        api_key_env=str(args.api_key_name),
        openrouter_base_url=str(args.openrouter_base_url),
        openrouter_timeout_s=int(args.openrouter_timeout_s),
        openrouter_max_retries=int(args.openrouter_max_retries),
        use_codex_exec=bool(args.use_codex_exec),
        codex_exec_model=args.codex_exec_model,
        codex_exec_sandbox=args.codex_exec_sandbox,
        use_claude_cli=bool(args.use_claude_cli),
        claude_cli_cmd=str(args.claude_cli_cmd),
        compile_cmd=str(args.compile_cmd),
        cwd=compile_cwd,
        use_lsp=bool(args.use_lsp),
        lsp_timeout_s=float(args.lsp_timeout_s),
        lemma_cache_dir=args.lemma_cache_dir,
        agent_prove=bool(args.agent_prove),
        agent_session_timeout=int(args.agent_session_timeout),
        agent_max_compiles=int(args.agent_max_compiles),
        vault_dir=args.vault_dir,
    )

    finished_results: list[ProblemOutcome] = []
    pending_targets: list[ProofTarget] = []
    if cfg.resume:
        for target in targets:
            summary_path = cfg.out_dir / target.relative_path.with_suffix("") / "summary.json"
            if summary_path.exists():
                finished_results.append(_load_problem_outcome(summary_path))
            else:
                pending_targets.append(target)
    else:
        pending_targets = list(targets)

    show_progress = bool(args.progress) and len(targets) > 0
    progress_print = _make_progress_printer() if show_progress else None
    if show_progress and progress_print is not None:
        progress_print(_format_progress(len(finished_results), len(targets)))

    results: list[ProblemOutcome] = list(finished_results)
    completed = len(finished_results)
    if cfg.workers <= 1:
        for target in pending_targets:
            results.append(_prove_target(target, cfg=cfg))
            completed += 1
            if show_progress and progress_print is not None:
                progress_print(
                    _format_progress(
                        completed,
                        len(targets),
                        label=f"Processed {target.relative_path.as_posix()}",
                    )
                )
    else:
        with ThreadPoolExecutor(max_workers=cfg.workers) as executor:
            future_map = {
                executor.submit(_prove_target, target, cfg=cfg): target for target in pending_targets
            }
            for future in as_completed(future_map):
                target = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive fallback
                    results.append(
                        ProblemOutcome(
                            relative_path=str(target.relative_path),
                            theorem_name=target.theorem_name,
                            passed=False,
                            attempts_used=0,
                            plan_rounds_used=0,
                            successful_attempt_no=None,
                            final_lean_path=None,
                            error=f"runner_error: {exc}",
                            attempts=[],
                        )
                    )
                completed += 1
                if show_progress and progress_print is not None:
                    progress_print(
                        _format_progress(completed, len(targets), label="Completed")
                    )

    results.sort(key=lambda item: item.relative_path)

    if show_progress and progress_print is not None:
        progress_print(_format_progress(len(targets), len(targets)), done=True)

    _write_report(
        out_dir=out_dir,
        cfg=cfg,
        results=results,
        invalid_targets=invalid_targets,
        skipped_complete=skipped_complete,
    )

    problems_failed = sum(1 for item in results if not item.passed)
    if invalid_targets or problems_failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
