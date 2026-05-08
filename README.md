# Bot Hub

## 项目名称
Bot Hub 控制平面（OpenClaw 多机器人统一编排）

### 项目简介
本仓库当前以 **Bot 平面** 为唯一生产入口：

- 提供 Web 控制台（钱包登录）
- 统一创建/启动/停止 WhatsApp 与 DingTalk 机器人实例
- 每实例独立 OpenClaw profile、目录、端口，互不影响
- 模型统一走 Router（默认 `gpt-5.3-codex`）

你可以把它理解为：

- OpenClaw 负责“机器人执行”
- Bot Hub 负责“机器人编排与运维”

### 功能特性
- ✅ Web 控制台（钱包登录 + 实例管理）
- ✅ 多实例隔离（profile/端口/目录）
- ✅ WhatsApp 配对日志与二维码展示
- ✅ 实例诊断与自动恢复（WhatsApp）
- ✅ Router 模型统一配置
- ✅ 标准启动脚本：`scripts/starter.sh`
- ✅ 标准打包脚本：`scripts/package.sh`
- ✅ Legacy 文档归档到 `docs/archive/legacy/`

## 快速开始

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Linux / WSL2 | Ubuntu 22.04+ | 推荐运行环境 |
| Node.js | >= 22 | OpenClaw 依赖 |
| OpenClaw | 2026.2.26（锁定） | 通道与网关 |
| Rust | stable（自动安装） | 控制平面编译（源码部署时） |

### 安装步骤

1. **克隆项目**
```bash
git clone git@github.com:ShengNW/bot.git
cd bot
```

2. **一键部署（推荐）**
```bash
bash scripts/deploy_full_stack.sh
```

3. **打开控制台**
- 浏览器访问：`http://127.0.0.1:3900/`
- 连接钱包后即可创建实例。

4. **首次创建 WhatsApp 实例后配对**
- 在实例行点击“配对”
- 在日志面板扫码完成 linked

### 配置说明

主配置文件：`config/bot-hub.env`

关键变量：

```text
BOT_HUB_BIND_ADDR=127.0.0.1:3900
ROUTER_BASE_URL=https://test-router.yeying.pub/v1
ROUTER_API_KEY=
BOT_HUB_DEFAULT_MODEL=gpt-5.3-codex
BOT_HUB_ADMIN_TOKEN=change-me-admin-token
BOT_HUB_INTERNAL_TOKEN=change-me-internal-token
BOT_HUB_INSTANCE_PORT_START=18800
BOT_HUB_INSTANCE_PORT_END=18999
```

> `scripts/bootstrap_full_stack.sh` 会自动从模板创建该文件。

## 本地开发

### 开发环境搭建

```bash
bash scripts/bootstrap_full_stack.sh
```

可选：仅准备 OpenClaw

```bash
bash scripts/setup/openclaw_prepare.sh install
bash scripts/setup/openclaw_prepare.sh configure
bash scripts/setup/openclaw_prepare.sh patch
```

### 运行项目

```bash
bash scripts/starter.sh start
bash scripts/starter.sh restart
bash scripts/starter.sh stop
```

### 调试方法

```bash
# 基础体检
bash scripts/doctor_full_stack.sh

# 服务状态（兼容入口）
bash scripts/status_full_stack.sh

# 健康接口
curl -sS http://127.0.0.1:3900/api/v1/public/health

# 若出现 "openclaw: command not found"，执行下面修复
OPENCLAW_BIN="$(npm config get prefix)/bin/openclaw"
if [[ -x "$OPENCLAW_BIN" ]]; then
  sudo ln -sf "$OPENCLAW_BIN" /usr/local/bin/openclaw
  openclaw --version
fi
```

## 生产部署

### 部署前准备

- [ ] `config/bot-hub.env` 已配置 `ROUTER_API_KEY`
- [ ] OpenClaw 可执行：`openclaw --version`
- [ ] 机器网络满足 Router 与通道连接要求
- [ ] 已完成钱包登录链路可用性验证

### 部署步骤

#### 方式一：源码部署（推荐内网研发）

```bash
git clone git@github.com:ShengNW/bot.git
cd bot
bash scripts/deploy_full_stack.sh
```

#### 方式二：安装包部署（无需 Rust）

```bash
# 在构建机打包
bash scripts/package.sh

# 在目标机解压后
cd <pkg-dir>
cp config/bot-hub.env.template config/bot-hub.env
# 编辑 config/bot-hub.env
bash scripts/starter.sh start
```

### 环境变量配置

见 `config/bot-hub.env`（源码）或 `config/bot-hub.env.template`（安装包）。

### 健康检查

```bash
curl -sS http://127.0.0.1:3900/api/v1/public/health
curl -sS http://127.0.0.1:3900/api/v1/public/version
```

## API文档
- 控制平面总览：`docs/rust-control-plane.md`
- 详细设计：`docs/bot-hub-control-plane-detailed-design.md`
- GitHub Issue 机器人方案：`docs/github机器人方案.md`
- GitHub Issue 机器人部署手册：`docs/github机器人部署手册.md`
- 旧单 profile 手册（归档）：`docs/archive/legacy/`

## 测试

```bash
# 1) 启动可用
bash scripts/starter.sh start

# 2) 健康可用
curl -sS http://127.0.0.1:3900/api/v1/public/health

# 3) 页面可访问
curl -sSI http://127.0.0.1:3900/ | head -n 5
```

## 贡献指南

1. **提交规范**
- 使用：`<type>(scope): <summary>`
- 示例：`docs(readme): 收口 bot 平面启动路径`

2. **提交要求**
- 不提交任何真实密钥
- 不破坏 `scripts/starter.sh` / `scripts/package.sh` 主路径
- 若改动接口，更新 `docs/rust-control-plane.md`

3. **Legacy 处理原则**
- 旧路径先“降级兼容”，再“物理删除”
- 删除前需给出回滚路径与验证记录
