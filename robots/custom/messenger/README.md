# Messenger

`messenger` 是当前仓库里用于承接多实例消息机器人的目录，对应控制台中的 WhatsApp / 钉钉实例管理能力。

页面展示名当前使用“信使”，目录名保持 `messenger`，和现有 `trader -> 交易员` 的命名方式保持一致：

- 目录名使用英文技术标识，便于代码、脚本和接口保持稳定
- 控制台展示名使用中文角色名，便于产品语义统一

## 当前职责

当前 `messenger` 主要对应以下能力：

- WhatsApp 多实例创建、启动、停止、删除
- WhatsApp 配对二维码与运行日志查看
- 钉钉实例创建与配置
- 实例模型配置与基础运行状态管理

这些能力当前主要由 `dashboard/` 提供控制面与运行编排，尚未完全收口到 `robots/custom/messenger/` 目录中。

## 当前实现位置

目前相关实现主要分布在：

- `dashboard/src/main.rs`
- `dashboard/web/index.html`

后续可以逐步把以下内容收口到本目录：

- 机器人定义与说明文档
- 默认配置模板
- 运行脚本
- 渠道能力说明
- 与 dashboard 对接的适配层

## 后续建议

建议后续按下面的顺序演进：

1. 把 dashboard 中与消息机器人强相关的说明文档同步到本目录
2. 为 `messenger` 增加默认配置模板和运行脚本
3. 视需要把运行时配置、模板和脚本进一步收口到 `robots/custom/messenger/`
