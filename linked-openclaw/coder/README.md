# Coder Bot

`Coder Bot` 是一层 GitHub ingress / orchestrator，真正执行由 OpenClaw 完成。

系统分工：

- GitHub 侧负责发现 issue、排队、准备仓库、提交分支、创建 PR
- Feishu 侧负责 handoff 讨论和执行确认
- OpenClaw 负责在飞书线程上下文里持续执行

## 工作方式

- GitHub 触发方式：
  - 标签 `ai-run`
  - `RUN_ON_ISSUE_OPENED=true`
- GitHub 触发只负责“建会话 + 建飞书线程 + 发 handoff prompt”
- 真正执行必须在线程里确认，默认确认词包含 `/run`
- 执行确认来自飞书线程，不使用 GitHub issue 评论作为执行入口
- PR 发布统一走外层服务的 `git commit`、`git push` 和 GitHub API create PR

## 会话模型

同一个 GitHub issue 会固定复用：

- 一个稳定的 `session_key`
- 一个稳定的 OpenClaw `agent_id`
- 一个固定的 issue 工作区 `data/repos/<repo>/issues/issue-<n>/`
- 至少一个已绑定的飞书线程

同一个 issue 重跑时，会继续沿用飞书讨论上下文和本地工作区。

## 配置约定

- `config/openclaw.json`：静态源配置，只放模型、飞书、gateway 这类固定配置
- `data/openclaw/runtime/openclaw.runtime.json`：启动时自动生成的运行时有效配置
- `data/openclaw/state/`：OpenClaw 自己持久化 agent / plugin / state 的目录

不要把运行时生成内容反写回 `config/openclaw.json`。

## 快速开始

部署目录不是固定的。下面统一用 `APP_DIR` 表示你的实际部署目录，例如：

```bash
export APP_DIR="$HOME/linked-openclaw/coder"
```

快速启动：

```bash
cd "$APP_DIR"
cp config/coder-bot.env.template config/coder-bot.env
cp config/openclaw.json.template config/openclaw.json
BOT_USER="$(id -un)" UV_BIN="$HOME/.local/bin/uv" ./scripts/bootstrap.sh
```

启动前至少要补：

- `config/coder-bot.env` 里的 GitHub App、SSH key、`ROUTER_API_KEY`
- `config/openclaw.json` 里的 Feishu 和 gateway 配置

如果你只想先装依赖并做自检，不安装 systemd：

```bash
cd "$APP_DIR"
INSTALL_SYSTEMD=false BOT_USER="$(id -un)" UV_BIN="$HOME/.local/bin/uv" ./scripts/bootstrap.sh
```

## 常用命令

```bash
cd "$APP_DIR"
uv sync --frozen
UV_CACHE_DIR=/tmp/coder-bot-uv-cache uv run --frozen coder-bot --env-file config/coder-bot.env doctor
./scripts/start_gateway.sh
./scripts/status_gateway.sh
uv run --frozen gunicorn -c gunicorn.conf.py issue_bot_service:APP
curl -s http://127.0.0.1:9081/health
curl -s http://127.0.0.1:9081/issues/<owner>/<repo>/<issue_number>/session
sudo systemctl restart openclaw-gateway coder-bot
```

## 关键接口

- `GET /health`：查看服务健康、轮询状态、队列状态
- `GET /issues/<owner>/<repo>/<issue_number>/session`：查看 issue session、agent、工作区、飞书绑定
- `POST /feishu/bind`：绑定飞书线程到某个 issue
- `GET /feishu/bindings/<chat_id>/<thread_id>`：按飞书线程反查 issue 会话
- `DELETE /feishu/bindings/<chat_id>/<thread_id>`：解绑飞书线程
- `POST /issues/<owner>/<repo>/<issue_number>/session/state`：由 OpenClaw / 飞书侧回写会话状态

## 文档

- 部署步骤见 [部署手册.md](部署手册.md)
- 日常触发、查看状态、排障见 [使用手册.md](使用手册.md)
