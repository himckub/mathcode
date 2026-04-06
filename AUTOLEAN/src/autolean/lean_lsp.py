"""Lean LSP session wrapper for the AUTOLEAN proving pipeline.

Provides three capabilities gated behind --use-lsp:
  1. Lemma search via leansearch.net + loogle.lean-lang.org
  2. Structured diagnostics (errors with line/col/severity)
  3. Goal state at arbitrary positions (proof state at error location)

Usage:
    session = LeanLSPSession(Path("lean-workspace"))
    try:
        lemmas = session.search_lemmas("sum over empty range")
        ok, diags = session.get_structured_diagnostics(lean_code)
        goal = session.get_goal_at_position(lean_code, line=15, col=8)
    finally:
        session.close()
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

try:
    import orjson as _json_mod

    def _json_loads(data: bytes | str) -> object:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return _json_mod.loads(data)
except ImportError:
    def _json_loads(data: bytes | str) -> object:
        return json.loads(data)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LemmaSearchResult:
    name: str
    type_sig: Optional[str]
    module: str
    source: str  # "leansearch" or "loogle"


@dataclass(frozen=True)
class StructuredDiagnostic:
    severity: str  # "error", "warning", "info", "hint"
    message: str
    line: int      # 1-indexed
    column: int    # 1-indexed


@dataclass(frozen=True)
class GoalInfo:
    goals: list[str]
    rendered: str


# ---------------------------------------------------------------------------
# Rate limiter (thread-safe)
# ---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self, max_requests: int, per_seconds: int) -> None:
        self._timestamps: list[float] = []
        self._max = max_requests
        self._window = per_seconds
        self._lock = threading.Lock()

    def wait_and_acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [
                    t for t in self._timestamps if t > now - self._window
                ]
                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return
            time.sleep(0.5)


_leansearch_limiter = _RateLimiter(3, 30)
_loogle_limiter = _RateLimiter(3, 30)


# ---------------------------------------------------------------------------
# HTTP search functions (no LSP needed)
# ---------------------------------------------------------------------------

def _leansearch(query: str, num_results: int = 6) -> list[LemmaSearchResult]:
    """Natural language search via leansearch.net.

    Note: leansearch.net API may require specific headers or auth.
    Falls back gracefully if unavailable.
    """
    _leansearch_limiter.wait_and_acquire()

    url = "https://leansearch.net/search"
    payload = json.dumps({"query": query, "num_results": num_results}).encode()
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "AUTOLEAN/0.1")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            data = _json_loads(resp.read())
    except (HTTPError, URLError, OSError, ValueError):
        return []

    if not isinstance(data, list):
        return []

    results: list[LemmaSearchResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("formal_name") or ""
        if not name:
            continue
        type_sig = item.get("type") or item.get("formal_type")
        module = item.get("module") or item.get("docurl") or ""
        results.append(LemmaSearchResult(
            name=str(name).strip(),
            type_sig=str(type_sig).strip() if type_sig else None,
            module=str(module).strip(),
            source="leansearch",
        ))
    return results


def _loogle(query: str, num_results: int = 8) -> list[LemmaSearchResult]:
    """Type-pattern search via loogle.lean-lang.org."""
    _loogle_limiter.wait_and_acquire()

    from urllib.parse import quote
    url = f"https://loogle.lean-lang.org/json?q={quote(query)}"
    req = Request(url, method="GET")

    try:
        with urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            data = _json_loads(resp.read())
    except (HTTPError, URLError, OSError, ValueError):
        return []

    if not isinstance(data, dict):
        return []

    hits = data.get("hits")
    if not isinstance(hits, list):
        return []

    results: list[LemmaSearchResult] = []
    for item in hits[:num_results]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        if not name:
            continue
        type_sig = item.get("type")
        module = item.get("module") or ""
        results.append(LemmaSearchResult(
            name=str(name).strip(),
            type_sig=str(type_sig).strip() if type_sig else None,
            module=str(module).strip(),
            source="loogle",
        ))
    return results


# ---------------------------------------------------------------------------
# LSP severity mapping
# ---------------------------------------------------------------------------

_SEVERITY_MAP = {1: "error", 2: "warning", 3: "info", 4: "hint"}


# ---------------------------------------------------------------------------
# LeanLSPSession
# ---------------------------------------------------------------------------

class LeanLSPSession:
    """Manages a Lean LSP server for the proving pipeline.

    Start once per theorem, reuse across all proof attempts, close at end.
    All public line/col parameters are 1-indexed.
    """

    def __init__(
        self,
        project_path: Path,
        lsp_timeout_s: float = 120.0,
    ) -> None:
        self._project_path = project_path.resolve()
        self._timeout = lsp_timeout_s
        self._client = None
        self._probe_rel_path: Optional[str] = None
        self._probe_abs_path: Optional[Path] = None

        try:
            from leanclient import LeanLSPClient
            self._client = LeanLSPClient(
                str(self._project_path),
                max_opened_files=2,
                initial_build=False,
                prevent_cache_get=True,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to start Lean LSP: {exc}") from exc

        # Create a persistent temp file for LSP probes
        fd, tmp_path = tempfile.mkstemp(
            suffix=".lean",
            prefix="_autolean_probe_",
            dir=str(self._project_path),
        )
        os.close(fd)
        self._probe_abs_path = Path(tmp_path)
        self._probe_rel_path = self._probe_abs_path.relative_to(
            self._project_path
        ).as_posix()

    def _write_probe(self, content: str) -> None:
        """Write content to probe file and sync with LSP."""
        if self._client is None or self._probe_abs_path is None:
            return
        self._probe_abs_path.write_text(content, encoding="utf-8")
        try:
            self._client.open_file(self._probe_rel_path)
            self._client.update_file_content(self._probe_rel_path, content)
        except Exception:
            # If update fails, try reopening
            try:
                self._client.open_file(
                    self._probe_rel_path, force_reopen=True
                )
            except Exception:
                pass

    # ----- Public: Lemma Search -----

    def search_lemmas(
        self,
        query: str,
        num_results: int = 8,
    ) -> list[LemmaSearchResult]:
        """Search leansearch + loogle, deduplicate, return combined results.

        Tries multiple query strategies:
        1. leansearch with the full query (natural language)
        2. loogle with the full query (may match as a pattern or name)
        3. loogle with key terms extracted from the query
        """
        results: list[LemmaSearchResult] = []
        seen: set[str] = set()

        def _add(items: list[LemmaSearchResult]) -> None:
            for r in items:
                if r.name not in seen:
                    seen.add(r.name)
                    results.append(r)

        # Natural language search (may be unavailable)
        try:
            _add(_leansearch(query, num_results))
        except Exception:
            pass

        # Loogle: try with the raw query
        try:
            _add(_loogle(query, num_results))
        except Exception:
            pass

        # If raw query didn't yield much, try extracting key math terms
        if len(results) < 3:
            # Extract qualified-looking names from the query
            qualified = re.findall(
                r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b",
                query,
            )
            for term in qualified[:2]:
                try:
                    _add(_loogle(term, 4))
                except Exception:
                    pass

        return results[:num_results]

    # ----- Public: Structured Diagnostics -----

    def get_structured_diagnostics(
        self,
        lean_content: str,
    ) -> tuple[bool, list[StructuredDiagnostic]]:
        """Get structured diagnostics for lean_content via LSP.

        Returns (success, diagnostics_list). Success means no errors.
        """
        if self._client is None:
            return False, []

        self._write_probe(lean_content)

        try:
            diags_result = self._client.get_diagnostics(
                self._probe_rel_path,
                inactivity_timeout=self._timeout,
            )
        except Exception:
            return False, []

        if diags_result is None:
            return False, []

        structured: list[StructuredDiagnostic] = []
        for d in diags_result.diagnostics:
            if not isinstance(d, dict):
                continue
            sev_num = d.get("severity", 1)
            severity = _SEVERITY_MAP.get(sev_num, "error")
            message = d.get("message", "")
            rng = d.get("range", {})
            start = rng.get("start", {})
            # Convert 0-indexed LSP to 1-indexed
            line = start.get("line", 0) + 1
            col = start.get("character", 0) + 1
            structured.append(StructuredDiagnostic(
                severity=severity,
                message=str(message).strip(),
                line=line,
                column=col,
            ))

        success = diags_result.success
        return success, structured

    # ----- Public: Goal at Position -----

    def get_goal_at_position(
        self,
        lean_content: str,
        line: int,
        col: int = 1,
    ) -> Optional[GoalInfo]:
        """Get proof goal state at a 1-indexed position.

        Must call get_structured_diagnostics first (or _write_probe)
        to ensure the LSP has the content loaded.
        """
        if self._client is None:
            return None

        # Ensure content is loaded
        self._write_probe(lean_content)

        try:
            # Convert 1-indexed to 0-indexed for LSP
            result = self._client.get_goal(
                self._probe_rel_path,
                line=line - 1,
                character=col - 1,
            )
        except Exception:
            return None

        if result is None:
            return None

        goals = result.get("goals", [])
        rendered = result.get("rendered", "")
        if not goals and not rendered:
            return None

        return GoalInfo(
            goals=[str(g) for g in goals],
            rendered=str(rendered),
        )

    # ----- Public: Query Lemma Definitions -----

    def query_lemma_definitions(
        self,
        lemma_names: list[str],
    ) -> dict[str, str]:
        """Query Lean for full definitions of lemmas via #print.

        Uses the LSP's already-loaded Mathlib environment.
        Returns dict mapping lemma_name -> full #print output.
        """
        if self._client is None or not lemma_names:
            return {}

        lines = ["import Mathlib", ""]
        for name in lemma_names:
            lines.append(f"#print {name}")
        source = "\n".join(lines) + "\n"

        self._write_probe(source)

        try:
            diags = self._client.get_diagnostics(
                self._probe_rel_path,
                inactivity_timeout=self._timeout,
            )
        except Exception:
            return {}

        if diags is None:
            return {}

        name_set = set(lemma_names)
        result: dict[str, str] = {}
        for d in diags.diagnostics:
            if not isinstance(d, dict):
                continue
            sev = d.get("severity", 1)
            if sev != 3:  # 3 = info
                continue
            msg = d.get("message", "")
            if not msg or " : " not in msg:
                continue

            msg_text = str(msg).strip()
            first_line = msg_text.split("\n")[0]
            header_parts = first_line.split(" : ")[0].split()
            if not header_parts:
                continue
            extracted_name = header_parts[-1].lstrip("@").split(".{")[0]
            if extracted_name in name_set and extracted_name not in result:
                result[extracted_name] = msg_text

        return result

    # ----- Public: Close -----

    def close(self) -> None:
        """Shut down the LSP server and clean up probe file."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        if self._probe_abs_path is not None:
            try:
                self._probe_abs_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._probe_abs_path = None


# ---------------------------------------------------------------------------
# Formatting helpers for prompt injection
# ---------------------------------------------------------------------------

def format_lemma_search_results(results: list[LemmaSearchResult]) -> str:
    """Format search results for injection into a planner prompt."""
    if not results:
        return ""

    lines = ["Verified Mathlib lemmas from search (these names exist in Mathlib):"]
    for r in results:
        sig = f" : {r.type_sig}" if r.type_sig else ""
        lines.append(f"  - {r.name}{sig}")
    return "\n".join(lines)


def format_structured_diagnostics(diags: list[StructuredDiagnostic]) -> str:
    """Format diagnostics for injection into a repair prompt."""
    if not diags:
        return ""

    lines = ["Structured compiler diagnostics:"]
    for d in diags:
        if d.severity == "error":
            lines.append(f"  [{d.severity}] line {d.line}, col {d.column}: {d.message}")
    # If no errors, include warnings
    if len(lines) == 1:
        for d in diags:
            if d.severity == "warning":
                lines.append(f"  [{d.severity}] line {d.line}: {d.message}")
    return "\n".join(lines)


def format_goal_at_error(goal: Optional[GoalInfo]) -> str:
    """Format a goal state for injection into a repair prompt."""
    if goal is None or not goal.goals:
        return ""

    lines = ["Proof goal at error location (what remains to be proven):"]
    for i, g in enumerate(goal.goals, 1):
        if len(goal.goals) > 1:
            lines.append(f"  Goal {i}:")
        lines.append(f"  {g}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vault knowledge base — read cached lemmas from previous sessions
# ---------------------------------------------------------------------------

_LEMMA_CACHE_FILENAME = "lemma_cache.json"


def load_vault_knowledge(
    cache_dir: Path,
    theorem_statement: str,
    max_lemmas: int = 15,
) -> list[LemmaSearchResult]:
    """Load relevant cached lemmas from the vault knowledge base.

    Reads lemma_cache.json and filters by namespace relevance to the
    current theorem statement. Returns the most relevant cached lemmas.
    """
    cache_path = cache_dir / _LEMMA_CACHE_FILENAME
    if not cache_path.exists():
        return []

    try:
        raw = cache_path.read_text(encoding="utf-8")
        cached = json.loads(raw)
        if not isinstance(cached, dict):
            return []
    except (OSError, json.JSONDecodeError):
        return []

    if not cached:
        return []

    # Extract key type/namespace terms from the theorem statement for relevance
    # e.g., "∀ (n : ℕ), n + 0 = n" → {"Nat", "ℕ"}
    relevance_terms: set[str] = set()

    # Unicode math symbols → namespace mappings
    _type_map = {
        "ℕ": "Nat", "ℤ": "Int", "ℝ": "Real", "ℂ": "Complex", "ℚ": "Rat",
    }
    for sym, ns in _type_map.items():
        if sym in theorem_statement:
            relevance_terms.add(ns)

    # Extract capitalized words that look like type/namespace names
    for word in re.findall(r"\b([A-Z][A-Za-z0-9_]+)\b", theorem_statement):
        relevance_terms.add(word)

    # Score each cached lemma by relevance
    scored: list[tuple[int, str, str]] = []  # (score, name, definition)
    for name, definition in cached.items():
        score = 0
        parts = name.split(".")

        # Exact namespace match (e.g., Nat.add_zero matches "Nat")
        if parts and parts[0] in relevance_terms:
            score += 10

        # Partial namespace match
        for term in relevance_terms:
            if term.lower() in name.lower():
                score += 3

        # Boost if the definition mentions types from the theorem
        for term in relevance_terms:
            if term in definition:
                score += 1

        if score > 0:
            scored.append((score, name, definition))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: -x[0])
    results: list[LemmaSearchResult] = []
    for _score, name, definition in scored[:max_lemmas]:
        # Extract type signature from definition (first line after " : ")
        type_sig = None
        first_line = definition.split("\n")[0] if definition else ""
        if " : " in first_line:
            type_sig = first_line.split(" : ", 1)[1].split(" :=")[0].strip()

        results.append(LemmaSearchResult(
            name=name,
            type_sig=type_sig,
            module=".".join(name.split(".")[:-1]),
            source="vault",
        ))

    return results


_cache_lock = threading.Lock()


def save_lemma_to_vault(cache_dir: Path, name: str, definition: str) -> None:
    """Save a single lemma definition to the vault cache.

    Thread-safe, merges with existing cache.
    """
    cache_path = cache_dir / _LEMMA_CACHE_FILENAME
    with _cache_lock:
        existing: dict[str, str] = {}
        if cache_path.exists():
            try:
                existing = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict):
                    existing = {}
            except (OSError, json.JSONDecodeError):
                existing = {}

        existing[name] = definition
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def format_vault_knowledge(results: list[LemmaSearchResult]) -> str:
    """Format vault knowledge for injection into a prompt."""
    if not results:
        return ""

    lines = ["Known lemmas from previous sessions (verified, from vault):"]
    for r in results:
        sig = f" : {r.type_sig}" if r.type_sig else ""
        lines.append(f"  - {r.name}{sig}")
    return "\n".join(lines)
