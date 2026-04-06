#!/usr/bin/env python3
"""Compile a Lean file and provide structured LSP diagnostics.

Used by agent-mode proving sessions. Compiles the file with lake env lean,
then if compilation fails, queries the LSP for structured diagnostics and
proof goal at the first error location.

Usage:
    python compile_with_lsp.py <lean_file> --cwd <lean_workspace> [--lsp-timeout 60]

Output (on stdout):
    COMPILE_OK                          — if compilation succeeds
    COMPILE_FAILED                      — if compilation fails, followed by:
    [error] line N, col M: message      — structured errors
    [goal] ⊢ remaining_goal            — proof goal at first error (if available)
    [raw] ...                           — raw compiler output (truncated)
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="compile_with_lsp")
    parser.add_argument("lean_file", type=Path)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--compile-cmd", type=str, default="lake env lean {file}")
    parser.add_argument("--lsp-timeout", type=float, default=60.0)
    args = parser.parse_args()

    lean_file = args.lean_file.resolve()
    cwd = args.cwd.resolve()

    # Step 1: Compile
    cmd_parts = shlex.split(args.compile_cmd)
    cmd = [p.replace("{file}", str(lean_file)) for p in cmd_parts]
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)

    if proc.returncode == 0:
        print("COMPILE_OK")
        return 0

    print("COMPILE_FAILED")

    # Step 2: Try LSP for structured diagnostics
    lsp_available = False
    try:
        from autolean.lean_lsp import LeanLSPSession

        session = LeanLSPSession(project_path=cwd, lsp_timeout_s=args.lsp_timeout)
        lsp_available = True

        content = lean_file.read_text(encoding="utf-8")
        _ok, diags = session.get_structured_diagnostics(content)

        # Print structured errors
        for d in diags:
            if d.severity == "error":
                print(f"[error] line {d.line}, col {d.column}: {d.message}")

        # Get goal at first error
        first_error = next((d for d in diags if d.severity == "error"), None)
        if first_error is not None:
            goal = session.get_goal_at_position(content, first_error.line, first_error.column)
            if goal and goal.goals:
                for g in goal.goals:
                    print(f"[goal] {g}")

        session.close()
    except Exception:
        pass

    # Step 3: Always include raw output (truncated)
    raw = (proc.stderr + "\n" + proc.stdout).strip()
    if raw:
        for line in raw.splitlines()[:20]:
            print(f"[raw] {line}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
