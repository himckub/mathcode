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

- 如果本地没有 `./mathcode`，从 GitHub Releases 下载匹配平台的 `mathcode-vX.Y.Z-<os>-<arch>.tar.gz`
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
