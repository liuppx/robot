# Coding Bot

`Coding Bot` 是一个基于 GitHub App + 本地 Codex 的 Issue 自动处理机器人。

默认推荐用 `polling-only` 模式部署：

- 不依赖 GitHub Webhook
- 不依赖 Nginx
- 不要求公网域名或 HTTPS
- 只需要 `systemd + uv + gunicorn`



## 工作流程

1. 轮询扫描允许仓库的 open issues 和最新评论
2. 命中 `/run`、标签或其他触发条件后写入 SQLite 队列
3. 复用本地仓库目录，不重复克隆
4. 在 fork 仓库创建分支并调用本地 Codex
5. 默认由 Codex 做最小自检；只有显式配置 `TEST_COMMAND` 时才额外执行外层测试命令
6. 通过仓库内 `merge.sh` 推送 fork 并创建 PR
7. 在 Issue 下回帖结果，并可自动追加 `/submit`
8. 发送飞书通知

## 当前特性

- 默认支持 polling-only 部署
- 支持 `doctor` 自检
- 支持 `uv` 管理依赖
- 支持 `gunicorn` 作为长期运行入口
- 支持每仓库串行队列
- 支持仓库复用，不重复克隆
- 支持 SQLite 持久化
- 支持 fork 提交，不直接 push 上游
- 支持飞书通知

## 路径说明

项目不依赖固定目录，不要求必须放在 `/opt` 或 `/opt/deploy`。

默认规则：

- `APP_HOME` 默认是当前项目目录
- `DATA_DIR` 默认是 `APP_HOME/data`
- `SECRETS_DIR` 默认是 `APP_HOME/secrets`

所以你可以放在任意目录，例如：

- `/opt/coding-bot`
- `/srv/coding-bot`
- `~/coding-bot`

## 目录说明

- `issue_bot_service.py`
  主服务逻辑
- `gunicorn.conf.py`
  gunicorn 启动配置，worker 启动后会自动拉起轮询和调度线程
- `.env.template`
  配置模板
- `.env`
  实际运行配置
- `data/`
  SQLite、任务日志、仓库缓存、状态文件
- `secrets/`
  GitHub App 私钥和 SSH 私钥

## 快速开始

推荐直接走一键安装脚本：

```bash
cp .env.template .env
```

按模板填写 GitHub App、Codex、SSH、飞书配置，并确认机器上已安装 `codex` 和 `gh`。如果目标机器只有 `root` 用户，`CODEX_BIN` 和 `CODEX_SOURCE_HOME` 直接写 `/root/...`。

然后执行：

```bash
sudo BOT_USER="$(id -un)" UV_BIN="${HOME}/.local/bin/uv" ./scripts/bootstrap.sh
```

这个脚本会依次执行：

- `uv sync --frozen`
- `.env` 基础检查
- `uv run --frozen coding-bot --env-file .env doctor`
- `scripts/install_systemd.sh`

如果你只是想先做依赖安装和自检，不安装 systemd：

```bash
INSTALL_SYSTEMD=false UV_BIN="${HOME}/.local/bin/uv" ./scripts/bootstrap.sh
```

安装完成后，本机检查：

```bash
curl -s http://127.0.0.1:9081/health
```

## 推荐配置

如果只是想快速迁移到其他机器，推荐直接使用：

```env
ENABLE_WEBHOOK=false
ENABLE_POLLING=true
```

这种模式不需要公网域名、HTTPS 或 GitHub Webhook URL。systemd 启动后会定时扫描 open issues 和评论触发任务。

多仓库时直接写成逗号分隔，例如：

```env
ALLOWED_REPOS=yeying-community/deployer,yeying-community/another-repo,foo/bar
```

## systemd

推荐使用一键脚本：

```bash
sudo BOT_USER="$(id -un)" UV_BIN="${HOME}/.local/bin/uv" ./scripts/bootstrap.sh
```

如果你只想重装 service，也可以单独执行：

```bash
sudo BOT_USER="$(id -un)" UV_BIN="${HOME}/.local/bin/uv" ./scripts/install_systemd.sh
```

systemd 的 `ExecStart` 会统一生成为：

```bash
uv run --frozen gunicorn -c gunicorn.conf.py issue_bot_service:APP
```


## 部署文档

- 部署步骤见 [部署.md](部署.md)
- 日常操作见 [使用手册.md](使用手册.md)
