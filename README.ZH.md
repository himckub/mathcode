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

- 如果本地缺少 `./mathcode` 或 `AUTOLEAN/`，从 GitHub Releases 下载匹配平台的 `mathcode-vX.Y.Z-<os>-<arch>.tar.gz`，并解压出二进制和内置 AUTOLEAN 流水线
- 在有 `shasum` 或 `sha256sum` 时校验 `SHA256SUMS.txt`
- 在需要时从 `.env.example` 创建 `.env`
- 安装内置 AUTOLEAN 的 Python 虚拟环境
- 在本机缺少 `lean` / `lake` 时完成 Lean 初始化

## 环境要求

- macOS (arm64) 或 Linux (x86_64)
- Python 3.12+
- 足够的磁盘空间用于 bundle、Lean 工具链和 Mathlib cache
- 如果你想走默认后端和默认数学流程，需要本机安装 `codex` CLI

## 常用命令

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

数学结果会写到 `LeanFormalizations/`。

## 功能特性

### Lean LSP 集成

启用 Lean LSP 以获得更智能的 lemma 检索和结构化报错：

```env
MATHCODE_USE_LSP=1
```

启用后，prover 会：
- 在 planning 前用 Loogle 检索已验证的 Mathlib lemma 名
- 用带行列号和严重级别的 LSP 诊断代替原始 stderr
- 在出错位置提取 proof goal，给后续 repair 更精确的上下文

第一次 LSP 操作通常需要约 60 秒（加载 Mathlib），之后会快很多。

如果你在运行 `bash setup.sh` 之前就设置了 `MATHCODE_USE_LSP=1`，LSP 依赖会自动安装。要在已有环境中启用，重新运行：

```bash
bash setup.sh
```

### Obsidian 定理图谱

生成 Obsidian vault，以知识图谱的形式可视化定理依赖关系：

```bash
/obsidian on       # 启用并从已有形式化结果生成
/obsidian off      # 禁用
/obsidian generate # 立即重新生成
```

启用后，每次形式化和证明都会自动更新 `./ObsidianVault/`。在 Obsidian 中打开并使用 Graph View 查看定理与引理之间的关系。每个引理条目都包含通过 `#print` 从 Mathlib 查询到的完整 Lean 定义。

### Agent 模式证明

每个证明会话变成一个完整的交互式对话，Agent 使用工具迭代地证明定理：

```env
MATHCODE_AGENT_PROVE=1
```

建议同时启用 Obsidian 定理图谱（Agent 会读取 vault 获取上下文）。启用后，Agent 可以：
- 检索 Obsidian vault 中的相关 Mathlib 引理
- 编写证明候选并用 `lake env lean` 编译
- 读取编译错误、搜索修复方案并重新编译（每个会话最多 10 次）
- 实时输出推理过程和工具调用

### 多规划器

并行运行多个规划器以获得多样化的证明策略：

```env
MATHCODE_NUM_PLANNERS=3
```

每个规划器会提出不同的策略，所有发现的引理都会保存到 vault 中。prover 综合所有方案选择最优路径。默认值为 1（单规划器，行为不变）。

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

## 致谢

MathCode 的数学形式化与证明流水线基于 [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) 项目。
