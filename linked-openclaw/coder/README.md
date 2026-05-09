# Coder Bot

GitHub issue orchestration service backed by OpenClaw and Feishu.

- 源代码：`src/`
- 核心模块：`src/main.py`、`src/issue_service.py`、`src/webhook_server.py`、`src/worker.py`、`src/scheduler.py`
- 配置文件：`config/`
- 运维脚本：`scripts/`
- 运行数据：`data/`
- 文档：`docs/`

## 启动方式

首次或升级后建议先做一次依赖同步和自检：

```bash
uv sync --frozen
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

systemd 部署或升级：

```bash
BOT_USER="$(id -un)" UV_BIN="$HOME/.local/bin/uv" ./scripts/install_systemd.sh
sudo systemctl restart openclaw-gateway coder-bot
sudo systemctl status --no-pager coder-bot
```

如果你是从旧的单文件版本升级，必须确认 `coder-bot.service` 已经切到新入口：

```bash
uv run --frozen gunicorn -c config/gunicorn.conf.py src.main:APP
```

不应该再看到旧入口：

```bash
issue_bot_service:APP
```

详细说明见：

- [部署手册](docs/部署手册.md)
- [使用手册](docs/使用手册.md)
