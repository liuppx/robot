# Dashboard（Bot 平面，生产入口）

## 1. 定位

Dashboard 是本仓库的唯一生产控制入口：

- 钱包登录门禁
- 多实例编排（WhatsApp / DingTalk）
- 每实例独立 profile/端口/目录
- 统一模型配置与运行诊断

## 2. 目录

```text
dashboard/
  Cargo.toml
  .env.example
  src/main.rs
  web/index.html
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
cd /home/administrator/code/bot_hub
bash scripts/deploy_full_stack.sh
```

或分步执行：

```bash
cd /home/administrator/code/bot_hub
bash scripts/bootstrap_full_stack.sh
# 编辑 config/bot-hub.env，填 ROUTER_API_KEY
bash scripts/starter.sh start
```

访问：`http://127.0.0.1:3900/`

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
- `POST /api/v1/public/auth/wallet/connect`
- `POST /api/v1/public/auth/logout`
- `GET /api/v1/public/bot/types`
- `GET /api/v1/public/router/models`
- `GET /api/v1/public/bot/instances`
- `POST /api/v1/public/bot/instances`
- `GET /api/v1/public/bot/instances/{id}`
- `DELETE /api/v1/public/bot/instances/{id}`
- `PATCH /api/v1/public/bot/instances/{id}/model`
- `POST /api/v1/public/bot/instances/{id}/start`
- `POST /api/v1/public/bot/instances/{id}/stop`
- `POST /api/v1/public/bot/instances/{id}/pair-whatsapp`
- `GET /api/v1/public/bot/instances/{id}/logs`
- `GET /api/v1/public/bot/instances/{id}/diagnose`

### admin
- `PATCH /api/v1/admin/router/default-model`
- `GET /api/v1/admin/runtime/summary`

### internal
- `POST /api/v1/internal/runtime/health/probe`

## 7. 常见问题

### Q1: 浏览器打开 127.0.0.1:3900 连接被拒绝

```bash
bash scripts/status_full_stack.sh
bash scripts/starter.sh start
```

### Q2: 模型拉取失败

```bash
bash scripts/doctor_full_stack.sh
# 检查 config/bot-hub.env 中 ROUTER_API_KEY
```

### Q3: WhatsApp 配对后不回消息

1. UI 打开实例日志与诊断。
2. 查看 `recommended_action` 与证据链。
3. 若提示 `router_auth_missing`，补齐 Router 配置并重启实例。

## 8. Legacy 文档

手工单 profile 路径已归档：`docs/archive/legacy/`
