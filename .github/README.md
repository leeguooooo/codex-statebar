# Codex Statebar

为 Codex CLI 显示配额、上下文、模型、推理强度、Git 状态、活动和用量预测。

```text
5h[░░░--%░░░░]⏰-- | 7d[█░░░7%░░░░]⏰6d21h →91% | gpt-5.6(178.7k/353.4k)
⤷ codex-statebar ⎇ main●
⚙ effort:high
```

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh | sh
cxs doctor
```

安装脚本会校验 SHA-256，安装 `cxs` 和补丁版 `codex-cxs`，把用户级 `codex` 指向补丁版，再运行 `cxs --setup` 接入状态栏。已有的 `~/.local/bin/codex` 会先备份；ChatGPT App 内置二进制不会被修改。

GitHub Actions 为 macOS 和 Linux 构建独立二进制，用户端不需要 Python 或 Rust。若只想安装 `cxs`，不替换用户级 `codex`：

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh \
  | CODEX_STATEBAR_INSTALL_CODEX_DEFAULT=0 sh
```

## 使用

| 目标 | 命令 |
| --- | --- |
| 显示当前状态 | `cxs` |
| 实时独立面板 | `cxs watch` |
| 诊断数据与接入 | `cxs doctor` |
| 预览主题和样式 | `cxs preview` |
| 切换主题 | `cxs config set theme nord` |
| 切换样式 | `cxs config set style capsule` |
| 配置嵌入式富状态栏 | `cxs --setup` |
| 输出 JSON | `cxs --json-output` |
| 精确选择会话 | `cxs --session <thread-id>` |

数据来自本地 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions`。`cxs` 不读取 `auth.json`，也不上传 session 内容。独立运行时只选择当前目录的顶层 Codex CLI 会话，不会混入同目录的 Codex Desktop 或 VS Code 会话。

视觉配置优先读取 `~/.codex/codex-statebar.json`。若没有 Codex 专属配置，则继承 `~/.claude/claude-statusbar.json`，让 `cs` 和 `cxs` 使用同一套主题、颜色和密度。

## 嵌入 Codex TUI

Codex 0.144.1 稳定版还不能执行外部状态栏命令。本项目包含一个 Rust TUI 补丁，把 `cxs render` 的 ANSI 输出显示在输入框下方；Python 负责主题和指标计算。

一键安装会安装预构建的补丁版。需要从源码构建时：

```bash
./scripts/build-patched-codex.sh
cxs --setup
./dist/codex-cxs
```

`cxs --setup` 写入以下配置：

```toml
[tui]
status_line = ["command", "…/cxs", "render"]
```

只有首项为 `command` 时，Codex 才会调用外部渲染器。补丁包含异步执行、更新合并、超时保护、会话隔离和 ANSI 色彩解析。它最多读取三行输出，并按实际行数调整底部状态区高度。

上游扩展点仍在讨论：[openai/codex#17827](https://github.com/openai/codex/issues/17827)。构建脚本固定使用 `rust-v0.144.1`，避免补丁套到不兼容版本。

新建 Codex CLI 后，先发送一条消息，让 Codex 创建 rollout。若还没有 rollout，`cxs` 会提示完成首轮交互，不会改用同目录的 Desktop 会话。

## 上下文和缓存

上下文用量取最近一次 `token_count` 的 `total_tokens / model_context_window`。`cached_input_tokens` 能减少计费，但仍占用模型上下文，因此计入已用 token；计算 billable input 时才从 input tokens 中扣除。

`cache 3m24s` 表示 Claude prompt cache 的剩余 TTL。`cxs` 根据最近 assistant turn 的时间戳识别 5 分钟或 1 小时写入桶。Codex rollout 只提供 cached token 数量，不提供缓存创建时间或 TTL，因此 Codex 状态不会显示虚构的 `cache COLD` 或倒计时。

## 开发验证

```bash
python -m pip install -e . pytest
python -m pytest -q
python -m compileall -q src
cxs --no-color
patch --dry-run -d /path/to/codex -p1 < patches/codex-0.144.1-external-status-line.patch
patch --dry-run -d /path/to/codex -p1 < patches/codex-0.144.1-multiline-status-line.patch
cargo check -p codex-tui
```

完整的离线网页版本见 [README.html](../README.html)。
