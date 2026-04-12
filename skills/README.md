# Skills

Drop Lean 4 / math domain skills here as `.md` files. They are auto-discovered and available as `/skill-name` commands in MathCode.

## Format

Each `.md` file becomes a skill. The filename (without `.md`) is the command name.

```markdown
# My Skill Title

## When to use
Description of when this skill applies.

## Content
The actual skill content — tactics, patterns, reference material, etc.
```

## Optional Frontmatter

```markdown
---
description: What this skill does
when_to_use: When to activate
allowed-tools: Bash, Read, Write
model: inherit
---
```

## Built-in Skills

These domain skills are compiled into the binary (no `.md` file needed):

- `compilation-errors` — Common Lean 4 error patterns and fixes
- `group-theory` — Group theory proving patterns and key lemmas
- `number-theory` — Number theory tactics and Fermat/Euler theorems
- `parity-proofs` — Even/odd proof strategies
- `proof-golfing` — Proof optimization patterns
- `tactic-cascade` — Fast-to-slow tactic ordering reference
- `type-coercion-patterns` — Nat.card vs Fintype.card, Fact vs Prop, etc.

Add your own `.md` skills here to extend MathCode with domain-specific knowledge.
