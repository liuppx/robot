# Hub Frontend

`hub/frontend` 是 Hub 控制台的 React 前端源码目录。

## 本地开发

先启动 backend：

```bash
cd hub/backend
uv run python -m uvicorn hub.app:create_app --factory --reload --host 127.0.0.1 --port 3900
```

再启动 frontend：

```bash
cd hub/frontend
npm install
npm run dev
```

访问：`http://127.0.0.1:5174/`

Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:3900`。

停止：在运行 frontend 的终端里按 `Ctrl+C`。如果端口被旧进程占用，可执行：

```bash
lsof -tiTCP:5174 -sTCP:LISTEN | xargs kill
```

## 构建

```bash
npm run build
```

构建产物输出到 `hub/frontend/dist/`，该目录不作为源码提交。
