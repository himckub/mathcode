# MathCode

### MathCode: A Frontier Mathematical Coding Agent

```
███╗   ███╗ █████╗ ████████╗██╗  ██╗ ██████╗ ██████╗ ██████╗ ███████╗
████╗ ████║██╔══██╗╚══██╔══╝██║  ██║██╔════╝██╔═══██╗██╔══██╗██╔════╝
██╔████╔██║███████║   ██║   ███████║██║     ██║   ██║██║  ██║█████╗
██║╚██╔╝██║██╔══██║   ██║   ██╔══██║██║     ██║   ██║██║  ██║██╔══╝
██║ ╚═╝ ██║██║  ██║   ██║   ██║  ██║╚██████╗╚██████╔╝██████╔╝███████╗
╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
```

**Project Page:** [math-ai-org/mathcode](https://github.com/math-ai-org/mathcode)

<p align="right"><strong>English</strong> | <a href="./README.ZH.md">中文</a></p>

MathCode is a terminal AI coding assistant with a built-in math formalization engine. Give it a math problem in plain language and it will automatically convert it into a Lean 4 theorem and attempt a formal proof.

![](./Demo.png)

## Quick Start

```bash
git clone https://github.com/math-ai-org/mathcode.git
cd mathcode
bash setup.sh
codex auth login
./run
```

The `math-ai-org/mathcode` repository is a lightweight bootstrap checkout. You clone the repo, run `bash setup.sh`, and `setup.sh` downloads the matching `mathcode` binary from GitHub Releases when it is missing.

What `setup.sh` does:

- downloads the matching `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` asset from GitHub Releases when `./mathcode` or `AUTOLEAN/` is missing, and extracts both the binary and the bundled AUTOLEAN pipeline
- verifies `SHA256SUMS.txt` when `shasum` or `sha256sum` is available
- creates `.env` from `.env.example` when needed
- installs the bundled AUTOLEAN Python environment
- bootstraps Lean locally when `lean` / `lake` are missing

## Requirements

- macOS (arm64) or Linux (x86_64)
- Python 3.12+
- enough disk space for the bundle, Lean toolchain, and Mathlib caches
- `codex` CLI if you want the default backend and default math flow

## Common Commands

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

Math outputs are written to `LeanFormalizations/`.

## Features

### Lean LSP Integration

Enable Lean LSP for smarter lemma discovery and structured error feedback during proving:

```env
MATHCODE_USE_LSP=1
```

When enabled, the prover:
- Searches Loogle for verified Mathlib lemma names before planning
- Uses structured LSP diagnostics (line/col/severity) instead of raw stderr
- Extracts proof goal at error location for targeted repairs

The first LSP operation takes ~60s while Mathlib loads; subsequent operations are fast.

If you set `MATHCODE_USE_LSP=1` before running `bash setup.sh`, the LSP dependency is installed automatically. To enable it in an existing installation, re-run:

```bash
bash setup.sh
```

### Obsidian Theorem Graph

Generate an Obsidian vault that visualizes theorem dependencies as a knowledge graph:

```bash
/obsidian on       # enable + generate from existing formalizations
/obsidian off      # disable
/obsidian generate # regenerate now
```

When enabled, every formalization and proof auto-updates the vault at `./ObsidianVault/`. Open it in Obsidian and use Graph View to see theorem-to-lemma relationships. Each lemma stub includes the full Lean definition queried from Mathlib via `#print`.

### Agent-Mode Proving

Each proof session becomes a full interactive chat where the agent uses tools to iteratively prove theorems:

```env
MATHCODE_AGENT_PROVE=1
```

Works best with Obsidian Theorem Graph enabled (the agent reads the vault for context). When enabled, the agent can:
- Search the Obsidian vault for relevant Mathlib lemmas
- Write proof candidates and compile them with `lake env lean`
- Read compile errors, search for fixes, and recompile (up to 10 times per session)
- Stream its reasoning and tool calls in real-time

### Multi-Planner

Run multiple planners in parallel to get diverse proof strategies:

```env
MATHCODE_NUM_PLANNERS=3
```

Each planner proposes a different strategy. All discovered lemmas are saved to the vault. The prover sees all plans and picks the best approach. Default is 1 (single planner, unchanged behavior).

## Backend Setup

Default setup: no `.env` edits are required.

```bash
codex auth login
./run
```

To use an Anthropic-compatible backend instead, set:

```env
MATHCODE_USE_OPENAI=0

ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
```

If you also want the math tools to stop using `codex exec`, add:

```env
AUTOLEAN_USE_CODEX=0
```

Shell-exported environment variables override `.env`.

## FAQ

**Q: `./run` says the MathCode binary is not installed yet**

Run:

```bash
bash setup.sh
```

**Q: `./run` fails with `exec format error`, `Bad CPU type in executable`, or a similar startup error**

You probably downloaded the wrong binary for your platform. Re-run `bash setup.sh`, or download the correct release asset manually from GitHub Releases.

**Q: Startup says Codex auth is missing**

Run:

```bash
codex auth login
```

**Q: Can I skip cloning and just download a release asset**

Yes. You can download and extract the `.tar.gz` bundle from GitHub Releases directly. The bootstrap repo just makes `bash setup.sh` the default path.

## Acknowledgments

The math formalization and proving pipeline in MathCode is based on the [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) project.
