# Coder Bot

GitHub issue orchestration service backed by OpenClaw and Feishu.

- 源代码：`src/`
- 核心模块：`src/main.py`、`src/issue_service.py`、`src/webhook_server.py`、`src/worker.py`、`src/scheduler.py`
- 配置文件：`config/`
- 运维脚本：`scripts/`
- 运行数据：`data/`
- 文档：`docs/`

## 启动方式

开始部署前，先确认两件事：

- Python 运行时使用 `3.11`
- OpenClaw 至少安装到和本仓库当前验证版本一致；本次联调验证版本是 `2026.5.6`

推荐初始化顺序：

```bash
uv python install 3.11
uv sync --frozen
openclaw plugins install @openclaw/feishu
openclaw plugins registry --refresh
uv run --frozen coder-bot --env-file config/coder-bot.env prepare-openclaw-runtime
uv run --frozen coder-bot --env-file config/coder-bot.env doctor
```

手动启动：

```bash
./scripts/start_gateway.sh
CODER_BOT_ENV_FILE=config/coder-bot.env uv run --frozen gunicorn -c config/gunicorn.conf.py src.main:APP
```

或者直接用 Python 包入口：

```bash
python -m src --env-file config/coder-bot.env serve
```

systemd 部署：

```bash
BOT_USER="$(id -un)" UV_BIN="$HOME/.local/bin/uv" ./scripts/install_systemd.sh
sudo systemctl restart openclaw-gateway coder-bot
sudo systemctl status --no-pager coder-bot
```

## 使用链路

标准使用方式只有一条：

1. 在 GitHub issue 评论区发送 `/run`
2. 等机器人在飞书 handoff 群里创建讨论线程
3. 先在线程里讨论方案
4. 在线程里再次发送 `/run`，才会真正开始执行

执行完成后，结果会回到同一个飞书线程里。

详细说明见：

- [部署手册](docs/部署手册.md)
- [使用手册](docs/使用手册.md)
