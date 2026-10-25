> 用 **Claude Code**？试试姊妹项目 **[Claude Status Bar](https://github.com/leeguooooo/claude-code-usage-bar)**：同款状态栏，专为 Claude Code 打造。

# Codex Statebar

[English](README.md) · **简体中文**

为 Codex CLI 显示配额、上下文、模型、推理强度、Git 状态、活动和用量预测。

![Aurora：默认外观，显示在 Codex 输入框下方](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-aurora.png)

## 安装

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh | sh
cxs doctor
```

安装脚本会校验 SHA-256，安装 `cxs` 和补丁版 `codex-cxs`，再安装一个用户级 `codex` 启动器。已有的 `~/.local/bin/codex` 会先备份；ChatGPT App 内置二进制不会被修改。

启动器会比较 PATH 中的官方 Codex 和补丁版：补丁版版本相同或更新时运行 `codex-cxs`；官方版更新时改为运行官方版，嵌入式状态栏暂停，直到补丁版跟上；`cxs doctor` 会显示当前选择。这样官方更新不会被旧补丁版静默遮住。`cxs upgrade` 会一起校验并更新 `cxs` 与 `codex-cxs`。

GitHub Actions 为 macOS 和 Linux 构建独立二进制，用户端不需要 Python 或 Rust。若不想安装用户级启动器：

```bash
curl -fsSL https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/install.sh \
  | CODEX_STATEBAR_INSTALL_CODEX_DEFAULT=0 sh
```

macOS / Linux 版首次启动时将运行时展开到 `~/.local/lib/codex-statebar/<内容校验值>/`，后续启动复用该目录，不再为每次状态栏刷新创建 `_MEI…`。并发初始化由文件锁保护；中断后的不完整目录会在下次启动时重建。可用 `CODEX_STATEBAR_RUNTIME_DIR` 指定存放位置。旧运行时保留供现有会话使用；`cxs upgrade` 更新安装入口。

版本探测会跳过 cmux 的转发脚本。如果其他包装脚本绕回 `cxs`，探针重入会立即失败并继续选择独立的 Codex 安装，避免形成递归进程链。

## 使用

| 目标 | 命令 |
| --- | --- |
| 显示当前状态 | `cxs` |
| 实时独立面板 | `cxs watch` |
| 诊断数据与接入 | `cxs doctor` |
| 挑选新外观（实时预览） | `cxs theme` |
| 检查更新 | `cxs upgrade --check` |
| 预览主题和样式 | `cxs preview` |
| 切换主题 | `cxs config set theme nord` |
| 切换样式 | `cxs config set style capsule` |
| 配置嵌入式富状态栏 | `cxs --setup` |
| 输出 JSON | `cxs --json-output` |
| 精确选择会话 | `cxs --session <thread-id>` |

数据来自本地 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions`。`cxs` 不读取 `auth.json`，也不上传 session 内容。独立运行时只选择当前目录的顶层 Codex CLI 会话，不会混入同目录的 Codex Desktop 或 VS Code 会话。

视觉配置优先读取 `~/.codex/codex-statebar.json`。若没有 Codex 专属配置，则继承 `~/.claude/claude-statusbar.json`，让 `cs` 和 `cxs` 使用同一套主题、颜色和密度。

## 嵌入 Codex TUI

安装器会自动配置，重新打开终端运行 `codex` 即可。当前补丁基于 Codex 0.164.0，Aurora 等多行样式显示在输入框下方。config.toml 保留原生字段作为后备；富状态栏命令只传给选中的补丁版进程。若官方版比补丁版更新，启动器优先官方版。

## 新外观、worktree 与 OCS

三种外观：Aurora 方块（默认）、Aurora 细线、Classic。

![Aurora 方块（默认）](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-aurora.png)
![Aurora 细线](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-thin.png)
![Classic](https://raw.githubusercontent.com/leeguooooo/codex-statebar/main/docs/images/look-classic.png)

- 首次遇到新的外观集合，状态栏显示 `New looks · cxs theme`。运行 `cxs theme` 用方向键选择 Aurora 方块、Aurora 细线或 Classic；取消保留原来的样式。`cxs themes` 仍列出配色。
- worktree 显示名称、主仓库名与有效 checkout 数量，已删除 checkout 的残留记录不计入。主 checkout 也显示数量，便于分辨正在修改哪棵树。可配置 `worktree_glyph` 与 `color_worktree`。
- 已安装 OCS 时独立显示当前 Codex 会话地址、未读消息和 LAN 状态。按完整 thread ID 校验，避免显示同目录的另一会话；状态只通过只读 OCS CLI 查询，约 30 秒更新一次。
- 每 6 小时后台检查 GitHub Release，发现新版时自动校验并升级。`cxs config set auto_upgrade false` 保留检查及新版提示，关闭自动安装；`CODEX_STATEBAR_NO_UPDATE=1` 关闭检查。手动运行 `cxs upgrade --check` 或 `cxs upgrade`。若需要更换原生 Codex 版本，后台保留提示，退出 Codex 后手动升级，避免破坏正在运行的执行组件。Windows 只提示重新安装。

Codex 不提供的五小时额度、Claude prompt-cache TTL 和按 Claude 模型划分的额度不会伪造显示。Git、活动、任务、推理强度、主题、预测、独立终端面板和诊断保留。

完整安装说明与资源保留规则见 [README.zh-CN.html](README.zh-CN.html)。
