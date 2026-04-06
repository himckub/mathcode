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

- downloads the matching `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` asset from GitHub Releases when `./mathcode` is missing
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
