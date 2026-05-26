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

<p align="right"><a href="./README.md">English</a> | <strong>中文</strong></p>

MathCode 是一个终端 AI 编程助手，内置数学形式化引擎。输入一道自然语言数学题，它会自动将其转化为 Lean 4 定理并尝试完成形式化证明。

![](./Demo.png)

## 快速开始

```bash
git clone https://github.com/math-ai-org/mathcode.git
cd mathcode
bash setup.sh
codex auth login
mathcode
```

`setup.sh` 会准备本地安装，在需要时从 GitHub Releases 下载匹配的 `mathcode` 和 `mathcode-webui` 二进制，并为后续 shell 安装一个 user-local 的 `mathcode` 启动命令。如果你还没 reload 当前 shell，`./run` 仍然可以作为 bundle 内的即时兜底入口。

`setup.sh` 会做这些事：

- 如果 bundle 里的运行时文件不完整、版本过旧、无法验证，或当前平台需要的 payload 无效，就从 GitHub Releases 下载匹配平台的 `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` 并恢复 `./mathcode`、`./mathcode-webui` 与 bundled `vendor/ripgrep/`
- 使用 `shasum` 或 `sha256sum` 校验当前平台 archive 对应的
  `SHA256SUMS.txt` 条目
- 在替换已有可用安装前，先校验下载得到的运行时文件是否完整
- bundle 内包含 CLI 和 WebUI helper 的 release metadata；archive 修复后 setup
  会刷新这份 metadata，之后 setup 或 `--status` 可以发现并修复过旧或无法验证的二进制
- 在需要时从 `.env.example` 创建 `.env`
- 如果设置 `MATHCODE_SETUP_USE_SYSTEM_LEAN=1` 且系统 `lean` / `lake` 都可用，就复用系统 Lean/Lake，保留现有 `ELAN_HOME`，并覆盖已有的完整 bundle-local `.local/elan` 工具对；否则使用 bundle 内完整的 `.local/elan` Lean 工具链，包括 Git Bash/MSYS 下可见的 `lean.exe` / `lake.exe`；如果本地 elan 工具文件不完整，会先修复；否则默认初始化本地 Lean
- 默认在 `~/.local/bin/` 安装一个 user-local 的 `mathcode` 启动器，并在需要时把该目录写入 shell 的 `PATH`
- 创建 `skills/`、`tools/`、`plugins/` 扩展目录
- 在 `vendor/ripgrep/` 内自带一个供 MathCode 内部搜索使用的 `rg` 二进制，因此仅运行发行版 bundle 时不需要额外安装系统级 ripgrep

为了避免覆盖已有但不属于这次安装的 `mathcode` 命令，setup 只会覆盖它自己之前创建过的 launcher 文件。
如果你把 `MATHCODE_INSTALL_BIN_DIR` 指到一个只在当前 shell `PATH` 里临时存在的自定义目录，`setup.sh` 仍然会刷新它管理的 profile 配置块，保证后续新开的 shell 也能继续识别 `mathcode`。
如果你传入相对路径形式的 `MATHCODE_INSTALL_BIN_DIR`，`setup.sh` 会先把它解析成相对于 bundle 根目录的绝对路径，再写入 launcher、记录状态文件和受管 PATH 配置块。
如果设置了 `MATHCODE_SETUP_USE_SYSTEM_LEAN=1`，且当前 shell 通过相对 `PATH` 项找到 `lean` / `lake`，setup 会使用切换到 bundle 根目录前捕获到的工具路径，即使 bundle 内已有完整的 `.local/elan` 工具对。未设置这个 opt-in 时，`--status` 会报告默认的本地 `.local/elan` bootstrap 路径，而不会把系统 Lean 当成已安装。
`setup.sh --status` 还会检查 `./mathcode --version` 和 checksum 是否匹配当前 release tag 记录的 metadata、`./mathcode-webui` 是否匹配记录的 release metadata，以及当前平台的 bundled `rg` 是否能正常输出 ripgrep 版本信息。
生成的 `.env` 路径值会按 shell 规则引用，因此 bundle 路径里包含 `$` 或单引号等字符时，`./run` source `.env` 后仍会保留字面值。
如果选定的 `MATHCODE_INSTALL_BIN_DIR` 无法真正用于安装 launcher，例如路径本身已被“非目录文件”占住，或者 launcher 文件根本写不进去，`setup.sh` 会只跳过 user-local launcher 这一步，其他 setup 过程仍会继续完成。
如果你之后用不同的 `MATHCODE_INSTALL_BIN_DIR` 重新运行 `setup.sh`，setup 会把受管 launcher 和对应的 `PATH` 配置块更新到新目录。之后即使不再设置这个环境变量，`--status` 和 `--clean` 也会继续跟踪这次记录下来的受管 launcher。

可选维护命令：

```bash
bash setup.sh --status   # 检查二进制和依赖是否健康
bash setup.sh --clean    # 删除安装产物，但保留证明结果和 vault 数据
bash setup.sh --help     # 查看全部 setup 参数
```

`bash setup.sh --clean` 会保留 `LeanFormalizations/` 和 vault 里的用户输出。

## 环境要求

- macOS (arm64) 或 Linux (x86_64)
- `curl`，用于 setup/bootstrap 下载
- `shasum` 或 `sha256sum`，用于校验 release archive 并写入 metadata
- 足够的磁盘空间用于 bundle、Lean 工具链和 Mathlib cache
- 如果你想走默认后端和默认数学流程，需要本机安装 `codex` CLI
- Python 3.12+（可选，仅 `tools/` 目录下的分析脚本需要）

## 常用命令

```bash
mathcode -p "prove that the square of an even number is even"
echo "hello" | mathcode -p
mathcode --help
```

发布包也包含浏览器 UI daemon：

```bash
./run webui
```

它会先 source bundle 内的 `.env`，再启动本地 daemon，并打印浏览器认证 URL。
如果直接运行打包后的 `./mathcode-webui` helper，它会先重新进入同目录的 `./run webui` wrapper，因此同样会加载 `.env`、本地 Lean 工具链和 bundle 默认配置；如果同目录 wrapper 存在但无法执行，会作为启动失败报告。
可以在 bundle `.env` 或 shell 里设置 `MATHCODE_GOAL_MAX_TOKEN_BUDGET`，
限制 source `/goal`、`/goal` daemon commands 和
`/api/v1/sessions/:id/goal` 接受的 token budget；它支持与 `/goal` 相同的
正整数、整数值小数和 `k`/`m`/`b` 紧凑格式，未设置或非法值会回退到
`1000000000`。
可以设置 `MATHCODE_MAX_CHAINED_COMMAND_INPUTS`，限制 `QueryEngine` abort
前允许的嵌套本地 slash-command next-input 提交数量；未设置或非法值会回退到
`25`。

交互式发布版 session 支持 `/goal <token-budget> <objective>`，也支持
`/goal --budget <token-budget>` 或 `/goal --budget=<token-budget>`，并可附加
`--max-continuations N`/`--max-continuations=<N>`，以及 `/goal pause`、
`/goal resume`、裸 `/goal`、`/goal help`、`/goal -h`、`/goal --help`、
`/goal status`、`/goal clear`。该命令会继续同一个 session，
不会启动单独 agent。以 `/` 开头的 objective 会作为普通 goal 文本提交，
不会再次解析成 slash command。budget 后的第一个 objective token 可以是 `--help`；
objective 解析已经开始后，flag-like token 会保留为 objective 文本，除非后置的有效
`--budget` 正在用于提供必需的显式 budget。当 `--budget` 被解析为 budget 选项时，
非法值会被拒绝，包括 `1 + 1 ... --budget nope` 这样的数值表达式 objective。

`--effort <level>` 或交互式 `/effort <level>` 可使用 `low`、`medium`、
`high`、`max` 或正整数；`/effort auto` 和 `/effort unset` 会让当前
session 回到模型默认值。
CLI 模型覆盖里的保留值 `default` 会按大小写不敏感匹配；自定义模型 ID
会保留原始大小写。

如果你还没 reload 当前 shell，也可以继续使用 bundle 内的兜底入口：

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

数学结果会写到 `LeanFormalizations/`。

自定义 agent 定义会 trim `description`、JSON `prompt`、markdown prompt 正文、`initialPrompt`，
以及 `effort`、`permissionMode`、`memory`、`isolation` 这类 JSON enum 字段；
空白的必填 description/prompt 会被拒绝，空白的可选 initial prompt 会被忽略；
JSON `skills` 列表会按 markdown frontmatter 的规则归一化。

交互式 context/task 显示会保留诊断信息：`/context` 在交互式和非交互式会话里
使用同一套可见 markdown transcript 输出，表格会转义单元格内容，显示 slash-command 与 deferred built-in tool 明细，
展示 MCP loaded/available 状态，从当前 usage 表格排除 deferred categories，
把手动 compact reserve 当作 reserved buffer 展示，在当前 usage 为空时仍显示
free/reserved 行，并在 zero-token synthetic window 以及异常 negative/non-finite token
行下避免非法建议百分比，把 server-side/MCP tool blocks 计入 message breakdown；compact/autocompact
路径会钳制异常阈值、token 计数、legacy string/single-block/singleton-nested 内容和空白 tool ID，
保留 singleton tool-result 配对，把 statusline、away summary、survey、sticky prompt UI
限定到 compact 后的活跃 transcript，partial compact 后会抑制陈旧 warning，并合并重复的远端 compacting
状态，因此很小的测试 window、陈旧 raw string blocks、异常 resumed transcript、非法远端 microcompact config
或嘈杂的远端状态流不会触发错误的 compaction 决策或 UI 状态；`/tasks`、
`TaskStop` 和 SDK `stop_task` 不会把可选中的 leader 行计入 running
teammate 数，并且可以停止 pending remote agent 和 running in-process teammate；task tools 和 SDK
`stop_task` 会 trim task id，task tools 允许 deprecated `shell_id`
以及 TaskOutput `agentId`/`bash_id` alias 回填空白 `task_id`、
归一化 legacy `wait_up_to` 秒数、拒绝空白任务文本字段、trim task metadata key、
跨用户类型恢复旧版持久化 task status、接受旧版 `TaskUpdate` status alias、拒绝空白或不安全的 `__proto__` metadata key、要求 TaskOutput timeout 为整数，并把 idle in-process teammate output 视为可读取而不是等到 timeout，正确重放 TaskOutput/TaskStop 的混合 text/structured result 数组，在 legacy TaskOutput output 回放中保留类似 `<error>` 的普通文本，避免仅因命令空白被 trim 就显示 TaskStop 截断省略号，同时在很矮的终端里仍显示隐藏任务摘要并让 recently completed 行按时过期。shell sleep 自动后台化与
path validation 会识别 `sleep 2s`、`sleep 2m`、`sleep +2`、`sleep 2e0`、`env ... sleep 2s` 这类
decimal / suffix / signed / exponent duration、PowerShell `& 'sleep' 2` 这类 quoted
call-operator sleep、`Start-Sleep -Seconds:2 > $null` 这类 redirected sleep、
`Microsoft.PowerShell.Utility\Start-Sleep -Seconds 2` 这类 module-qualified sleep
以及 `-Duration 00:00:02` 和 PowerShell 参数缩写/common parameters，并尊重短/小数/signed/exponent `timeout` wrapper。

## 功能特性

### 持久化 Lean REPL

启用持久化 Lean 语言服务器，实现亚秒级编译检查：

```env
MATHCODE_LEAN_REPL=1
```

一次性 ~90 秒预热（导入 Mathlib）后，后续每次编译检查仅需 **~0.4 秒**（而非 ~30 秒）。错误检测和通过确认均近乎即时。REPL 自动导入你的定理库和公理库。

### 定理库

自动存储已证明的定理，供未来证明复用：

```bash
/theorem-store on     # 启用（写入 .env）
/theorem-store off    # 禁用
/theorem-store sync   # 补录所有已证明但未入库的定理
/theorem-store status # 查看入库数量和 vault 信息
```

启用后，每个成功证明的定理会自动命名并追加到 `TheoremLib/Stored.lean`。Planner 和 prover 可以导入并直接复用这些定理，无需重新推导。

### 公理库

将对话中的假设存储为持久化、一致性检查的声明：

```bash
/axiomatize "A 比 B 快"             # 形式化 + 存储
/axiomatize list                     # 查看所有活跃公理
/axiomatize check                    # 一致性审查
/axiomatize remove <name>            # 删除一个声明
```

公理按 vault 存储，带有 Lean 形式化，经过编译检查，并自动注入到形式化和证明的提示中。支持任何领域：数学、物理、化学、叙事、通用。

### Lean LSP 集成

启用 Lean LSP 以获得更智能的 lemma 检索和结构化报错：

```env
MATHCODE_USE_LSP=1
```

启用后，prover 会：
- 在 planning 前通过 leansearch.net 和 Loogle 检索已验证的 Mathlib lemma 名
- 用带行列号和严重级别的 LSP 诊断代替原始 stderr
- 在出错位置提取 proof goal，给后续 repair 更精确的上下文
- 将搜索结果和 vault 知识注入 planner 和 prover 提示

LSP 已内置——不需要单独安装。

### Obsidian 定理图谱

生成 Obsidian vault，以知识图谱的形式可视化定理依赖关系：

```bash
/obsidian on       # 启用并从已有形式化结果生成
/obsidian off      # 禁用
/obsidian generate # 立即重新生成
```

启用后，每次形式化和证明都会自动更新 vault。在 Obsidian 中打开并使用 Graph View 查看定理与引理之间的关系。每个引理条目都包含通过 `#print` 从 Mathlib 查询到的完整 Lean 定义。

### Agent 模式证明

每个证明会话变成一个完整的交互式对话，Agent 使用工具迭代地证明定理：

```env
MATHCODE_AGENT_PROVE=1
```

建议同时启用 Obsidian 定理图谱（Agent 会读取 vault 获取上下文）。启用后，Agent 可以：
- 检索 vault 中的相关 Mathlib 引理
- 编写证明候选并通过持久化 REPL 编译
- 读取编译错误、搜索修复方案并重新编译（每个会话最多 10 次）
- 实时输出推理过程和工具调用

### 子目标树分解证明

将复杂定理分解为独立子目标并行证明：

```env
MATHCODE_TREE_PROVE=1
MATHCODE_MAX_TREE_DEPTH=2    # 递归深度（默认：1）
```

分解器生成带有 `have ... := by sorry` 占位符的骨架。每个子目标独立证明（如果某个失败，协同取消其他子目标）。已证明的子目标体被缝合回骨架并编译检查。

### 多规划器

并行运行多个规划器以获得多样化的证明策略：

```env
MATHCODE_NUM_PLANNERS=3
```

每个规划器会提出不同的策略，所有发现的引理都会保存到 vault 中。prover 综合所有方案选择最优路径。默认值为 1（单规划器）。

### 定时 Agent 循环

发行版自带循环调度能力，不需要额外构建参数。

在交互式 MathCode 会话里可以直接用：

```bash
/loop 10m check the deploy
/loop 1h /standup 1
```

短期提醒或监控建议直接用这种循环；如果你希望任务在重启后继续保留，就在交互式会话里创建持久化定时任务。

## 可扩展性

MathCode 支持三种扩展机制：

### 技能 (`skills/`)

放入 `.md` 文件即可添加领域特定知识和证明策略。启动时自动发现。

### 工具 (`tools/`)

放入带有 YAML frontmatter 的 Python `.py` 脚本即可添加分析工具。启动时自动发现。

4 个分析工具已包含：`axiom_checker`、`sorry_analyzer`、`proof_stats`、`lib_search`。仅在使用这些工具时才需要 Python 3.12+。

### 插件 (`plugins/`)

放入带有 `.mathcode-plugin/plugin.json` 清单的插件文件夹，即可添加命令、技能、Agent、MCP 服务器、钩子等。通过 `--plugin-dir` 加载或在 MathCode 内通过 `/plugin` 从 Git 仓库安装。

## 后端设置

默认配置下不需要改 `.env`：

```bash
codex auth login
mathcode
```

如果你还在刚执行完 setup 的同一个 shell 里，先用 `./run` 也可以；reload shell 之后再直接用 `mathcode`。

如果你想改成 Anthropic 兼容后端，可以设置：

```env
MATHCODE_USE_OPENAI=0

ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
```

如果你还想让数学工具也停止使用 `codex exec`，再加：

```env
AUTOLEAN_USE_CODEX=0
```

Shell 里导出的环境变量优先级高于 `.env`。

WebUI 设置页里的 provider-key 行只包含 daemon 当前能传给真实 child session 的
secret：`anthropic` 和 `openrouter`。Codex/OpenAI 路线使用 Codex OAuth，
不使用 `OPENAI_API_KEY` 行。
WebUI 的 `minimal` reasoning effort 会在 OpenAI/OpenRouter 路线上保留；
在 Anthropic 兼容路线中会映射到 CLI 当前可用的最低档 `low`。

发行版二进制会打包 Anthropic 兼容、Bedrock、Vertex 和 Foundry 分支所需的
provider SDK，以及 MCPB/DXT plugin package；这些路线不需要源码 checkout
里的 `node_modules`。Bedrock、Vertex 和 Foundry 使用各自 provider 的认证方式，
不使用 Anthropic 兼容的 `ANTHROPIC_AUTH_TOKEN` / `apiKeyHelper` bearer headers。

## 常见问题

**Q: setup 之后立刻执行 `mathcode` 还是找不到命令**

开一个新的 shell，或者执行：

```bash
source ~/.zshrc
```

如果你想在 reload 之前先继续使用，也可以直接运行：

```bash
./run
```

**Q: `./run` 报 `exec format error`、`Bad CPU type in executable` 或类似启动错误**

通常是因为下载了错误平台的二进制。重新执行 `bash setup.sh`，或者手动从 GitHub Releases 下载匹配你平台的 asset。

**Q: 启动时提示缺少 Codex 认证**

执行：

```bash
codex auth login
```

**Q: 能不能不 clone，直接下载 release asset**

可以。你也可以直接从 GitHub Releases 下载 `.tar.gz` bundle 后解压使用。这个 archive 本身是自包含的；只有当 bundled runtime 文件缺失、过旧或无法验证时，`bash setup.sh` 才会再从 GitHub 下载。bootstrap 仓库只是把 `bash setup.sh` 作为默认路径。

## Star History

想看项目的关注度变化，可以直接查看下面的 Star History 图表：

[![Star History Chart](https://api.star-history.com/svg?repos=math-ai-org/mathcode&type=Date)](https://www.star-history.com/#math-ai-org/mathcode&Date)

## 引用

如果你在研究中使用 MathCode，可以按下面的方式引用：

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

## 社区

加入我们的 Discord 获取帮助、反馈和讨论：**[discord.gg/f2AFP9W5](https://discord.gg/f2AFP9W5)**

## 致谢

MathCode 的数学形式化与证明流水线基于 [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) 项目。
