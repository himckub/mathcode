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
mathcode
```

`setup.sh` prepares the local installation, downloads the matching `mathcode` and `mathcode-webui` binaries from GitHub Releases when needed, and installs a user-local `mathcode` launcher for future shells. If you have not reloaded your shell yet, `./run` remains available as the bundle-local fallback.

What `setup.sh` does:

- downloads the matching `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` asset from GitHub Releases when bundled runtime files are missing, stale, unverified, or invalid for the current platform, restoring `./mathcode`, `./mathcode-webui`, and the bundled `vendor/ripgrep/` payload
- verifies the matching `SHA256SUMS.txt` entry for the current platform archive
  using `shasum` or `sha256sum`
- validates downloaded runtime files before replacing an existing working install
- includes release metadata for the bundled CLI and WebUI helper and refreshes
  it after archive repairs so future setup and `--status` runs can repair stale
  or unverified binaries
- creates `.env` from `.env.example` when needed
- uses system Lean/Lake when `MATHCODE_SETUP_USE_SYSTEM_LEAN=1` and both tools are available, preserving your existing `ELAN_HOME` and overriding a complete bundle-local `.local/elan` pair; otherwise uses the bundle-local `.local/elan` Lean toolchain when complete, including `lean.exe` / `lake.exe` pairs seen from Git Bash/MSYS, repairs partial local elan tool-file installs, or bootstraps Lean locally by default
- installs a user-local `mathcode` launcher in `~/.local/bin/` by default and adds that directory to your shell `PATH` when needed
- creates `skills/`, `tools/`, `plugins/` extension directories
- ships a bundled `rg` binary under `vendor/ripgrep/` for MathCode's internal search paths, so you do not need a separate system ripgrep install just to run the release bundle

To avoid clobbering an existing unrelated `mathcode` command, setup only overwrites launcher files that it previously created itself.
If you point `MATHCODE_INSTALL_BIN_DIR` at a custom directory that is only on the current shell's `PATH`, `setup.sh` still refreshes its managed profile block so future shells keep resolving `mathcode`.
If you pass a relative `MATHCODE_INSTALL_BIN_DIR`, `setup.sh` resolves it against the bundle root before writing the launcher, recorded state, or managed PATH block.
If `MATHCODE_SETUP_USE_SYSTEM_LEAN=1` and your current shell finds `lean` / `lake` through relative `PATH` entries, setup uses the tool paths captured before it changes into the bundle root, even when a complete bundle-local `.local/elan` pair already exists. Without that opt-in, `--status` reports the default local `.local/elan` bootstrap path instead of treating system Lean as installed.
`setup.sh --status` also checks that `./mathcode --version` and checksum match this release tag's recorded metadata, that `./mathcode-webui` matches the recorded release metadata, and that the current platform's bundled `rg` starts successfully with a ripgrep version banner.
Generated `.env` path values are shell-quoted so bundle paths containing characters such as `$` or single quotes remain literal when `./run` sources `.env`.
If the chosen `MATHCODE_INSTALL_BIN_DIR` cannot be used for launcher installation, for example because the path is already occupied by a non-directory file or the launcher file cannot be written there, `setup.sh` skips only the user-local launcher step and still completes the rest of setup.
If you rerun `setup.sh` with a different `MATHCODE_INSTALL_BIN_DIR`, setup updates its managed launcher and `PATH` block to match the new location. Later `--status` and `--clean` runs keep tracking that recorded managed launcher even if the env var is no longer set.

Optional maintenance commands:

```bash
bash setup.sh --status   # check whether the binary/tooling look healthy
bash setup.sh --clean    # remove install artifacts, keep proofs/vault data
bash setup.sh --help     # show all setup flags
```

`bash setup.sh --clean` preserves user outputs in `LeanFormalizations/` and vault data.

## Requirements

- macOS (arm64) or Linux (x86_64)
- `curl` for setup/bootstrap downloads
- `shasum` or `sha256sum` for release archive verification and metadata
- enough disk space for the bundle, Lean toolchain, and Mathlib caches
- `codex` CLI if you want the default backend and default math flow
- Python 3.12+ (optional, only needed for analysis tools in `tools/`)

## Common Commands

```bash
mathcode -p "prove that the square of an even number is even"
echo "hello" | mathcode -p
mathcode --help
```

The release bundle also includes the browser UI daemon:

```bash
./run webui
```

It sources the bundle `.env`, starts the local daemon, and prints the browser authentication URL.
If launched directly, the packaged `./mathcode-webui` helper re-enters the sibling `./run webui` wrapper first so the same `.env`, local Lean toolchain, and bundle defaults apply; a present but broken wrapper is reported as a launch failure.
Set `MATHCODE_GOAL_MAX_TOKEN_BUDGET` in the bundle `.env` or shell to cap token
budgets accepted by source `/goal`, `/goal` daemon commands, and
`/api/v1/sessions/:id/goal`; it accepts the same positive integer,
integer-valued decimal, and `k`/`m`/`b` compact formats as `/goal`, while unset
or invalid values fall back to `1000000000`.
Set `MATHCODE_MAX_CHAINED_COMMAND_INPUTS` to cap nested local slash-command
next-input submissions before `QueryEngine` aborts; unset or invalid values
fall back to `25`.

Interactive release sessions support `/goal <token-budget> <objective>` and
`/goal --budget <token-budget>` or `/goal --budget=<token-budget>` with optional
`--max-continuations N`/`--max-continuations=<N>`, plus `/goal pause`,
`/goal resume`, bare `/goal`, `/goal help`, `/goal -h`, `/goal --help`,
`/goal status`, and `/goal clear`. The command continues the
same session; it does not spawn a separate agent. Objectives that begin with
`/` are submitted as plain goal text, not parsed as another slash command. After
a budget, `--help` can be the first objective token; once objective parsing has
started, flag-looking tokens remain objective text unless a valid later
`--budget` is being used to supply the required explicit budget. Invalid
`--budget` values are rejected when `--budget` is parsed as the budget option,
including numeric-expression objectives such as `1 + 1 ... --budget nope`.

Use `--effort <level>` or interactive `/effort <level>` with `low`, `medium`,
`high`, `max`, or a positive integer; `/effort auto` and `/effort unset` return
the session to the model default.
For CLI model overrides, the reserved `default` value is matched
case-insensitively; custom model IDs keep their original casing.

If you have not reloaded your shell yet, the bundle-local fallback still works:

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

Math outputs are written to `LeanFormalizations/`.

Custom-agent definitions trim `description`, JSON `prompt`, markdown prompt
bodies, `initialPrompt`, and JSON enum fields such as `effort`,
`permissionMode`, `memory`, and `isolation`; blank required
descriptions/prompts are rejected, and blank optional initial prompts are
ignored. JSON `skills` lists are normalized like markdown frontmatter.

Interactive context/task displays keep diagnostic context intact: `/context`
uses the same visible markdown transcript output in interactive and
non-interactive sessions, escapes markdown-table cell content, shows
slash-command and deferred built-in tool details plus MCP loaded/available
status, excludes deferred categories from current-usage tables, treats manual
compact reserve as a reserved buffer, keeps free/reserved rows visible when
current usage is empty, avoids invalid suggestion percentages for zero-token
synthetic windows and malformed negative/non-finite token rows, and counts
server-side/MCP tool blocks in message breakdowns. Compact/autocompact paths
clamp malformed thresholds, token counts, legacy string/single-block/singleton-
nested content, and blank tool IDs; preserve singleton tool-result pairs; scope
statusline, away-summary, survey, and sticky-prompt UI to the active
post-compact transcript; suppress stale warnings after partial compact; and
coalesce duplicate remote compacting statuses so tiny test windows, stale raw
string blocks, malformed resumed transcripts, invalid remote microcompact
config, or noisy remote status streams do not force spurious compaction
decisions or UI state. `/tasks`, `TaskStop`, and SDK `stop_task` do not count the
selectable leader row as a running teammate and can stop pending remote agents
plus running in-process teammates;
task tools trim task ids, recover legacy persisted task statuses, accept legacy
`TaskUpdate` status aliases, reject blank task text fields, trim metadata keys,
reject blank or unsafe `__proto__` metadata keys, replay TaskOutput/TaskStop
mixed text/structured result arrays, preserve tag-looking text inside legacy
TaskOutput output replay, avoid false TaskStop truncation markers when command
whitespace is trimmed, and keep hidden summaries visible in very short
terminals with recently completed rows expiring on schedule. Shell sleep auto-backgrounding and
path validation recognize decimal, suffixed, signed, exponent, and trailing-dot
durations such as `sleep 2s`, `sleep 2m`, `sleep +2`, `sleep 2e0`, wrapped
`env ... sleep 2s`, PowerShell quoted/commented/redirected/module-qualified
sleep commands such as `& 'sleep' 2` or `Start-Sleep -Seconds:2 > $null`,
or `Microsoft.PowerShell.Utility\Start-Sleep -Seconds 2`,
TimeSpan `-Duration` values, PowerShell parameter abbreviations/common
parameters, and short/fractional/signed/exponent `timeout` wrappers. Task tools
and SDK `stop_task` trim task ids; task tools let deprecated `shell_id` and
TaskOutput `agentId`/`bash_id` aliases backfill a blank `task_id`, normalize
legacy `wait_up_to` seconds, recover legacy persisted task statuses, accept
legacy `TaskUpdate` status aliases, require integer-valued TaskOutput
timeouts, and
treat idle in-process teammate output as ready instead of waiting for timeout.

## Features

### Persistent Lean REPL

Enable a persistent Lean language server for sub-second compile checks:

```env
MATHCODE_LEAN_REPL=1
```

After a one-time ~90s warmup (importing Mathlib), every subsequent compile check takes **~0.4s** instead of ~30s. Both error detection and pass confirmation are near-instant. The REPL automatically imports your theorem library and axiom library.

### Theorem Library

Automatically store proved theorems for reuse in future proofs:

```bash
/theorem-store on     # enable (writes to .env)
/theorem-store off    # disable
/theorem-store sync   # backfill all proved-but-unstored theorems
/theorem-store status # show stored count and vault info
```

When enabled, every successfully proved theorem is automatically named, appended to `TheoremLib/Stored.lean`, and made importable for future proofs. The prover and planner can reuse stored theorems instead of re-deriving them.

### Axiom Library

Store conversational assumptions as persistent, consistency-checked declarations:

```bash
/axiomatize "A is faster than B"     # formalize + store
/axiomatize list                     # show all active axioms
/axiomatize check                    # consistency review
/axiomatize remove <name>            # remove a declaration
```

Axioms are stored per-vault with Lean formalization, compile-checked, and auto-injected into formalization and proving prompts. Supports any domain: math, physics, chemistry, narrative, general.

### Lean LSP Integration

Enable Lean LSP for smarter lemma discovery and structured error feedback during proving:

```env
MATHCODE_USE_LSP=1
```

When enabled, the prover:
- Searches leansearch.net and Loogle for verified Mathlib lemma names before planning
- Uses structured LSP diagnostics (line/col/severity) instead of raw stderr
- Extracts proof goal at error location for targeted repairs
- Injects search results and vault knowledge into planner and prover prompts

LSP is built-in — no separate installation required.

### Obsidian Theorem Graph

Generate an Obsidian vault that visualizes theorem dependencies as a knowledge graph:

```bash
/obsidian on       # enable + generate from existing formalizations
/obsidian off      # disable
/obsidian generate # regenerate now
```

When enabled, every formalization and proof auto-updates the vault. Open it in Obsidian and use Graph View to see theorem-to-lemma relationships. Each lemma stub includes the full Lean definition queried from Mathlib via `#print`.

### Agent-Mode Proving

Each proof session becomes a full interactive chat where the agent uses tools to iteratively prove theorems:

```env
MATHCODE_AGENT_PROVE=1
```

Works best with Obsidian Theorem Graph enabled (the agent reads the vault for context). When enabled, the agent can:
- Search the vault for relevant Mathlib lemmas
- Write proof candidates and compile them via the persistent REPL
- Read compile errors, search for fixes, and recompile (up to 10 times per session)
- Stream its reasoning and tool calls in real-time

### Tree-of-Subgoals Proving

Decompose complex theorems into independent subgoals and prove them in parallel:

```env
MATHCODE_TREE_PROVE=1
MATHCODE_MAX_TREE_DEPTH=2    # recursion depth (default: 1)
```

The decomposer generates a skeleton with `have ... := by sorry` placeholders. Each subgoal is proved independently (with cooperative cancellation if one fails). Proven bodies are stitched back into the skeleton and compile-checked.

### Multi-Planner

Run multiple planners in parallel to get diverse proof strategies:

```env
MATHCODE_NUM_PLANNERS=3
```

Each planner proposes a different strategy. All discovered lemmas are saved to the vault. The prover sees all plans and picks the best approach. Default is 1 (single planner).

### Scheduled Agent Loops

The bundled CLI ships with recurring prompt scheduling enabled out of the box.

Inside interactive MathCode sessions you can use:

```bash
/loop 10m check the deploy
/loop 1h /standup 1
```

Use short-lived loops for reminders and monitoring. When you want a schedule to survive restarts, create a durable schedule from the interactive session.

## Extensibility

MathCode supports three extension mechanisms:

### Skills (`skills/`)

Drop `.md` files to add domain-specific knowledge and proving strategies. Auto-discovered at startup.

### Tools (`tools/`)

Drop Python `.py` scripts with YAML frontmatter to add analysis tools. Auto-discovered at startup.

4 analysis tools are included: `axiom_checker`, `sorry_analyzer`, `proof_stats`, `lib_search`. Python 3.12+ is required only if you use these tools.

### Plugins (`plugins/`)

Drop plugin folders with `.mathcode-plugin/plugin.json` manifests to add commands, skills, agents, MCP servers, hooks, and more. Load via `--plugin-dir` or install from Git repos via `/plugin`.

## Backend Setup

Default setup: no `.env` edits are required.

```bash
codex auth login
mathcode
```

If you are still in the same shell where setup just finished, `./run` is the immediate fallback until you reload your shell profile.

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

In the WebUI settings panel, provider-key rows are limited to secrets the
daemon can pass to real child sessions today: `anthropic` and `openrouter`.
Codex/OpenAI routes use Codex OAuth, not an `OPENAI_API_KEY` row.
WebUI `minimal` reasoning effort is preserved for OpenAI/OpenRouter routes and
maps to the CLI's lowest available `low` effort on Anthropic-compatible routes.

The release binary bundles the provider SDKs used by the Anthropic-compatible,
Bedrock, Vertex, and Foundry branches, plus the MCPB/DXT plugin package; these
routes do not require a source checkout's `node_modules`. Bedrock, Vertex, and
Foundry use their provider-specific credentials rather than
Anthropic-compatible `ANTHROPIC_AUTH_TOKEN` / `apiKeyHelper` bearer headers.

## FAQ

**Q: `mathcode` is not found right after setup**

Open a new shell, or run:

```bash
source ~/.zshrc
```

If you want to keep working immediately before reloading your shell, use:

```bash
./run
```

**Q: `./run` fails with `exec format error`, `Bad CPU type in executable`, or a similar startup error**

You probably downloaded the wrong binary for your platform. Re-run `bash setup.sh`, or download the correct release asset manually from GitHub Releases.

**Q: Startup says Codex auth is missing**

Run:

```bash
codex auth login
```

**Q: Can I skip cloning and just download a release asset**

Yes. You can download and extract the `.tar.gz` bundle from GitHub Releases directly. The archive is self-contained; `bash setup.sh` only downloads from GitHub when bundled runtime files are missing, stale, or unverified. The bootstrap repo just makes `bash setup.sh` the default path.

## Star History

Track the project's growth over time here:

[![Star History Chart](https://api.star-history.com/svg?repos=math-ai-org/mathcode&type=Date)](https://www.star-history.com/#math-ai-org/mathcode&Date)

## Citation

If you use MathCode in research, please cite it as:

```bibtex
@misc{mathcode2026,
  title = {MathCode: A Frontier Mathematical Coding Agent},
  author = {Team Math-AI},
  journal = {math-ai-org.github.io},
  year = {2026},
  month = {April},
  url = "https://github.com/math-ai-org/mathcode"
}
```

## Community

Join our Discord for help, feedback, and discussion: **[discord.gg/f2AFP9W5](https://discord.gg/f2AFP9W5)**

## Acknowledgments

The math formalization and proving pipeline in MathCode is based on the [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) project.
