# Trader 运行手册

## 目的

这个机器人是当前仓库里第一个常驻式交易服务验证实现。它不是最终的生产级交易系统。

## 模式

- `paper`：本地模拟成交，并写入审计记录。
- `eastmoney_stub`：验证券商请求载荷，并把“待执行订单”持久化下来，供人工复核。

## 命令

```bash
./scripts/bootstrap.sh
./scripts/run_once.sh
./scripts/start_bot.sh
./scripts/stop_bot.sh
./scripts/status_bot.sh
```

## 运行时文件

运行时状态会写入 `runtime/`：

- `state.json`
- `orders.jsonl`
- `signals.jsonl`
- `logs/service.log`

## 安全边界

- 当前实现不会发送任何真实券商订单。
- 凭证从 `config/trader.env` 加载。
- 如果策略配置缺失，服务会拒绝启动。

## 策略说明

当前 `auction_wave` 的行为是有意保守的：

- 会把每个策略实例的状态持久化到 `runtime/state.json`；
- 能表达早盘竞价卖出分支和下午强制退出；
- 还没有真正的板级信号或盘口深度信号。

在把它视为生产级交易机器人之前，至少还需要补齐下面这些能力：

- 板块 / 题材活跃度排序，
- 波浪形态识别，
- 更严格的压力位判断，
- 盘中涨停与卖盘深度触发数据，
- 带显式确认和风控检查的真实券商适配器。
