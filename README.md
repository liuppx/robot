# Robot

## 项目定位

Robot 当前的主产品形态是 **Hub 控制平面**，用于统一部署、管理和编排多种机器人实例，并动态装配技能、工具与渠道能力。

当前仓库里保留了 Hub 控制面、机器人定义、运维脚本和历史参考材料。主入口：

- **当前生产入口**：`hub/`
- **生产启停**：`scripts/starter.sh`
- **生产打包**：`scripts/package.sh`
- **仓库结构说明**：`docs/repository-layout.md`

### 项目简介

本仓库当前以 **Hub 控制平面** 为唯一生产入口：

- 提供 Web 控制台（钱包登录）
- 统一创建/启动/停止 WhatsApp 与 DingTalk 机器人实例
- 每实例独立 OpenClaw profile、目录、端口，互不影响
- 模型统一走 Router（默认 `gpt-5.3-codex`）

你可以把它理解为：

- OpenClaw 负责“机器人执行”
- Hub 负责“机器人编排与运维”

### 功能特性
- Web 控制台（钱包登录 + 实例管理）
- 多实例隔离（profile/端口/目录）
- WhatsApp 配对日志与二维码展示
- 实例诊断与自动恢复（WhatsApp）
- Router 模型统一配置
- 标准生产启停脚本：`scripts/starter.sh`
- 标准生产打包脚本：`scripts/package.sh`

## 仓库分区

- `hub/`：当前 Hub 控制平面服务和 Web 控制台
- `scripts/`：Hub 启停、打包、体检、OpenClaw 准备和协作脚本
- `config/`：当前运行配置模板
- `docs/`：当前设计文档和运维手册
- `robots/openclaw/`：当前受控机器人实现目录，包含基于 OpenClaw 的机器人子系统
- `robots/nanobot/`：纳入统一机器人目录的 nanobot 实现
- `workspace/`：机器人实例使用的工作区模板

完整说明见 `docs/repository-layout.md`。

## 快速开始

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Linux / WSL2 | Ubuntu 22.04+ | 推荐运行环境 |
| Node.js | >= 22 | OpenClaw 依赖 |
| OpenClaw | 2026.2.26（锁定） | 通道与网关 |
| Python | >= 3.11 | Hub Python 控制面 |
| uv | 最新稳定版 | Python 依赖同步与运行入口 |

### 配置说明

生产服务配置文件：`config/hub.env`

关键变量：

```text
HUB_BIND_ADDR=127.0.0.1:3900
HUB_PUBLIC_BASE_URL=
ROUTER_BASE_URL=https://test-router.yeying.pub/v1
ROUTER_API_KEY=
HUB_DEFAULT_MODEL=gpt-5.3-codex
HUB_CHALLENGE_TTL_SECONDS=300
HUB_SESSION_TTL_SECONDS=86400
HUB_SESSION_SECRET=change-me-control-plane-session-secret
HUB_SESSION_COOKIE_SECURE_MODE=auto
HUB_ADMIN_TOKEN=change-me-admin-token
HUB_INTERNAL_TOKEN=change-me-internal-token
HUB_INSTANCE_PORT_START=18800
HUB_INSTANCE_PORT_END=18999
```

> `scripts/bootstrap_full_stack.sh` 会自动从模板创建这个生产服务配置文件。

- `HUB_PUBLIC_BASE_URL`：线上建议填写控制台真实 HTTPS 地址，用于钱包签名 challenge 的 `domain` 和 `uri`
- `HUB_SESSION_COOKIE_SECURE_MODE`：支持 `auto` / `always` / `never`，默认 `auto`

## 本地开发

本地开发以快速启动和热更新为主，前后端分进程运行。

### 1. 准备依赖

```bash
bash scripts/bootstrap_full_stack.sh
```

本地 backend 配置文件：

```bash
cp hub/backend/.env.template hub/backend/.env
```

`.env` 只用于本地开发 backend，不提交到远端。

### 2. 启动 backend

```bash
cd hub/backend
uv run python -m uvicorn hub.app:create_app --factory --reload --host 127.0.0.1 --port 3900
```

### 3. 启动 frontend

```bash
cd hub/frontend
npm install
npm run dev
```

访问：`http://127.0.0.1:5174/`

Vite 会把 `/api` 代理到 `http://127.0.0.1:3900`。

### 4. 停止开发服务

- 正常停止：在 backend 和 frontend 各自终端里按 `Ctrl+C`
- 如果端口被旧进程占用，可手动清理：

```bash
lsof -tiTCP:3900 -sTCP:LISTEN | xargs kill
lsof -tiTCP:5174 -sTCP:LISTEN | xargs kill
```

### 5. 快速测试

```bash
cd hub/backend
uv run python -m unittest discover -s tests
```

```bash
cd hub/frontend
npm run build
```

```bash
curl -sS http://127.0.0.1:3900/api/v1/public/health
```

## 生产部署

生产部署以构建产物和 `scripts/starter.sh` 为主。

### 1. 准备配置

```bash
cp config/hub.env.template config/hub.env
```

编辑 `config/hub.env`，至少填入 `ROUTER_API_KEY`。
这个文件用于打包后的服务运行，不用于本地开发 backend 直启。

### 2. 构建运行依赖和前端产物

```bash
bash scripts/bootstrap_full_stack.sh
```

### 3. 启停 Hub

```bash
bash scripts/starter.sh start
bash scripts/starter.sh restart
bash scripts/starter.sh stop
```

访问：`http://127.0.0.1:3900/`

### 4. 打包发布

在构建机执行：

```bash
bash scripts/package.sh
```

安装包输出到 `output/`。目标机解压后：

```bash
cd <pkg-dir>
cp config/hub.env.template config/hub.env
# 编辑 config/hub.env
bash scripts/bootstrap_full_stack.sh
bash scripts/starter.sh start
```

### 5. 生产检查

```bash
curl -sS http://127.0.0.1:3900/api/v1/public/health
curl -sS http://127.0.0.1:3900/api/v1/public/version
bash scripts/doctor_full_stack.sh
```

### 6. OpenClaw 准备

通常 `scripts/bootstrap_full_stack.sh` 会自动安装 OpenClaw。需要单独操作时使用：

```bash
bash scripts/setup/openclaw_prepare.sh install
bash scripts/setup/openclaw_prepare.sh configure
bash scripts/setup/openclaw_prepare.sh patch
```

## 常用脚本

```bash
scripts/bootstrap_full_stack.sh          # 准备 Python 依赖、前端构建产物、OpenClaw 和配置文件
scripts/starter.sh start|stop|restart    # 生产启停 Hub
scripts/package.sh                       # 打包发布包
scripts/doctor_full_stack.sh             # 生产体检
scripts/setup/openclaw_prepare.sh        # OpenClaw 安装、配置和补丁
scripts/sync.sh                          # 同步 fork/main
scripts/pr.sh                            # 创建或复用 PR
```

## API 文档

- 控制平面总览：`docs/hub.md`
- 详细设计：`docs/hub-control-plane-detailed-design.md`
- 旧文档归档：`docs/archive/legacy/`
