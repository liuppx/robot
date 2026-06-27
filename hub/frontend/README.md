# Hub Frontend

`hub/frontend` 是 Hub 控制台的 React 前端源码目录。

## 本地开发

先启动 backend：

```bash
cd hub/backend
uv run uvicorn hub.app:create_app --factory --reload --host 127.0.0.1 --port 3900
```

再启动 frontend：

```bash
cd hub/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

访问：`http://127.0.0.1:5173/`

Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:3900`。

## 构建

```bash
npm run build
```

构建产物输出到 `hub/frontend/dist/`，该目录不作为源码提交。
