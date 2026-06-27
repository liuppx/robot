# Dashboard Backend

`hub/backend` 是新的 Python 控制面骨架，目标定位是通用机器人管理后台。

当前迁移状态：

- 新的 Python 版本已经作为默认控制面入口
- 前端产品界面继续位于 `hub/ui/`

## 设计目标

控制面职责：

- 提供 hub API
- 发现并管理 `robots/` 下的机器人
- 统一起停、运行状态、配置读写和动作触发
- 通过适配器接入不同机器人，而不是在控制面里写死业务

## 当前骨架

```text
hub/backend/
  src/control_plane/
    api/
    adapters/
    models/
    services/
    app.py
    config.py
```

## 当前阶段

当前目录已经接管默认运行入口。

后续迁移建议：

1. 继续收敛控制面脚本和打包链路
2. 收敛机器人适配器边界，避免控制面里继续堆积机器人特例
3. 把钱包登录链路逐步升级到更完整的服务端校验模型
