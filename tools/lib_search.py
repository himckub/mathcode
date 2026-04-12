#!/usr/bin/env python3
# ---
# name: lib-search
# description: Search the theorem library for stored theorems matching a keyword query
# input:
#   query:
#     type: string
#     description: Space-separated keywords to search for in theorem names and signatures
#     required: true
# output: json
# ---
"""Search the user theorem library for relevant stored theorems.

Searches Stored.lean and INDEX.md for theorems matching a keyword query.
Useful for the agent to check what's already proved before attempting
a new proof.

Usage:
    python3 Tools/lib_search.py "cyclic"
    python3 Tools/lib_search.py "prime order"
    python3 Tools/lib_search.py "even odd parity"
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def search_stored(query: str, vault_path: Path) -> list[dict]:
    """Search the theorem library for matching entries."""
    # Reuse theorem_lib's parser for robust Stored.lean parsing
    scripts_dir = Path(__file__).resolve().parent.parent / "AUTOLEAN" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    stored_path = vault_path / "TheoremLib" / "Stored.lean"
    if not stored_path.exists():
        return []

    try:
        import theorem_lib
        text = stored_path.read_text(encoding="utf-8")
        records = theorem_lib.parse_stored_lean(text)
    except Exception:
        return []

    keywords = query.lower().split()
    results: list[dict] = []

    for rec in records:
        searchable = f"{rec.stored_name} {rec.original_name} {rec.normalized_header} {rec.proof_body}".lower()
        if any(kw in searchable for kw in keywords):
            results.append({
                "stored_name": rec.stored_name,
                "original_name": rec.original_name,
                "signature": rec.normalized_header,
                "source": rec.source_relpath,
                "proved_at": rec.proved_at,
                "usage": f"exact ObsidianVaultTheoremLib.{rec.stored_name} <args>",
            })

    return results


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 Tools/lib_search.py <query>", file=sys.stderr)
        return 1

    query = " ".join(sys.argv[1:])
    vault_path_str = os.environ.get("MATHCODE_OBSIDIAN_VAULT", "")
    if not vault_path_str:
        print(
            "Error: MATHCODE_OBSIDIAN_VAULT not set. "
            "Set the env var or pass --vault <path>.",
            file=sys.stderr,
        )
        return 1

    vault_path = Path(vault_path_str)
    results = search_stored(query, vault_path)

    if not results:
        print(json.dumps({"query": query, "matches": 0, "results": []}))
    else:
        print(json.dumps({"query": query, "matches": len(results), "results": results}, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
