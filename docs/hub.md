# Hub（Robot 控制面，生产入口）

## 1. 定位

Hub 是本仓库的唯一生产控制入口：

- 钱包登录门禁
- 多实例编排（WhatsApp / DingTalk）
- 每实例独立 profile/端口/目录
- 统一模型配置与运行诊断

## 2. 目录

```text
hub/
  backend/
    .env.example
    pyproject.toml
    src/hub/
  frontend/
    src/
    package.json
  ui/
    index.html  # frontend build output
robots/
  openclaw/
  nanobot/
  custom/
scripts/
  starter.sh
  package.sh
  bootstrap_full_stack.sh
  deploy_full_stack.sh
  doctor_full_stack.sh
  setup/openclaw_prepare.sh
```

## 3. 启动（推荐）

```bash
cd /home/administrator/code/hub
bash scripts/deploy_full_stack.sh
```

或分步执行：

```bash
cd /home/administrator/code/hub
bash scripts/bootstrap_full_stack.sh
# 编辑 config/hub.env，填 ROUTER_API_KEY
bash scripts/starter.sh start
```

访问：`http://127.0.0.1:3900/`

前端约定：

- `hub/frontend/` 是 React 源码目录
- `hub/ui/` 是前端构建产物目录，由 `npm run build` 输出
- Python backend 统一托管 `hub/ui/`，并负责 SPA 路由回退

认证相关配置建议：

- `HUB_PUBLIC_BASE_URL`：线上填写控制台真实 HTTPS 地址，Hub 会把它写入钱包签名 challenge 的 `domain` / `uri`
- `HUB_SESSION_COOKIE_SECURE_MODE`：`auto` / `always` / `never`，默认 `auto`

## 4. 停止与重启

```bash
bash scripts/starter.sh stop
bash scripts/starter.sh restart
```

## 5. 兼容入口（已降级）

以下脚本仍可用，但仅做兼容转发，不再是主路径：

- `scripts/run_full_stack.sh`
- `scripts/stop_full_stack.sh`
- `scripts/status_full_stack.sh`

## 6. 核心接口分层

### public
- `GET /api/v1/public/health`
- `GET /api/v1/public/version`
- `GET /api/v1/public/auth/me`
- `POST /api/v1/public/auth/wallet/challenge`
- `POST /api/v1/public/auth/wallet/verify`
- `POST /api/v1/public/auth/logout`
- `GET /api/v1/public/robot/types`
- `GET /api/v1/public/robots`
- `GET /api/v1/public/router/models`
- `GET /api/v1/public/robots/{key}/summary`
- `GET /api/v1/public/robots/{key}/config`
- `PUT /api/v1/public/robots/{key}/config`
- `POST /api/v1/public/robots/{key}/actions/run-once`
- `POST /api/v1/public/robots/{key}/actions/start`
- `POST /api/v1/public/robots/{key}/actions/stop`
- `GET /api/v1/public/robot/instances`
- `POST /api/v1/public/robot/instances`
- `GET /api/v1/public/robot/instances/{id}`
- `DELETE /api/v1/public/robot/instances/{id}`
- `PATCH /api/v1/public/robot/instances/{id}/model`
- `POST /api/v1/public/robot/instances/{id}/start`
- `POST /api/v1/public/robot/instances/{id}/stop`
- `POST /api/v1/public/robot/instances/{id}/pair-whatsapp`
- `GET /api/v1/public/robot/instances/{id}/logs`
- `GET /api/v1/public/robot/instances/{id}/diagnose`

### admin
- 预留，当前默认控制面未开放生产 admin 路由

### internal
- 预留，当前默认控制面未开放 production internal 路由

## 7. 常见问题

### Q1: 浏览器打开 127.0.0.1:3900 连接被拒绝

```bash
bash scripts/status_full_stack.sh
bash scripts/starter.sh start
```

### Q2: 模型拉取失败

```bash
bash scripts/doctor_full_stack.sh
# 检查 config/hub.env 中 ROUTER_API_KEY
```

### Q3: WhatsApp 配对后不回消息

1. UI 打开实例日志与诊断。
2. 查看 `recommended_action` 与证据链。
3. 若提示 `router_auth_missing`，补齐 Router 配置并重启实例。

## 8. Legacy 文档

手工单 profile 路径已归档：`docs/archive/legacy/`
