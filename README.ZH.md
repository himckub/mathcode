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
./run
```

`math-ai-org/mathcode` 现在是一个轻量 bootstrap 仓库。正常用法就是先 clone，再执行 `bash setup.sh`；如果本地缺少 `mathcode`，`setup.sh` 会自动从 GitHub Releases 下载对应平台的二进制。

`setup.sh` 会做这些事：

- 如果本地缺少 `./mathcode`，从 GitHub Releases 下载匹配平台的 `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` 并解压
- 在有 `shasum` 或 `sha256sum` 时校验 `SHA256SUMS.txt`
- 在需要时从 `.env.example` 创建 `.env`
- 在本机缺少 `lean` / `lake` 时完成 Lean 初始化
- 创建 `skills/`、`tools/`、`plugins/` 扩展目录

可选维护命令：

```bash
bash setup.sh --status   # 检查二进制和依赖是否健康
bash setup.sh --clean    # 删除安装产物，但保留证明结果和 vault 数据
bash setup.sh --help     # 查看全部 setup 参数
```

`bash setup.sh --clean` 会保留 `LeanFormalizations/` 和 vault 里的用户输出。

## 环境要求

- macOS (arm64) 或 Linux (x86_64)
- 足够的磁盘空间用于 bundle、Lean 工具链和 Mathlib cache
- 如果你想走默认后端和默认数学流程，需要本机安装 `codex` CLI
- Python 3.12+（可选，仅 `tools/` 目录下的分析脚本需要）

## 常用命令

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

数学结果会写到 `LeanFormalizations/`。

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
./run
```

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

## 常见问题

**Q: `./run` 提示还没有安装 MathCode binary**

执行：

```bash
bash setup.sh
```

**Q: `./run` 报 `exec format error`、`Bad CPU type in executable` 或类似启动错误**

通常是因为下载了错误平台的二进制。重新执行 `bash setup.sh`，或者手动从 GitHub Releases 下载匹配你平台的 asset。

**Q: 启动时提示缺少 Codex 认证**

执行：

```bash
codex auth login
```

**Q: 能不能不 clone，直接下载 release asset**

可以。你也可以直接从 GitHub Releases 下载 `.tar.gz` bundle 后解压使用。bootstrap 仓库只是把 `bash setup.sh` 作为默认路径。

## 社区

加入我们的 Discord 获取帮助、反馈和讨论：**[discord.gg/f2AFP9W5](https://discord.gg/f2AFP9W5)**

## 致谢

MathCode 的数学形式化与证明流水线基于 [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) 项目。
