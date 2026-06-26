# Trader

`robots/custom/trader` 是一个常驻策略交易机器人，用来验证完整的机器人执行链路：

1. 获取同花顺 iFinD 市场数据，
2. 计算策略信号，
3. 应用风控规则，
4. 生成订单意图，
5. 通过券商适配器路由订单，
6. 持久化审计日志和服务状态。

第一版实现刻意保持保守：

- 行情数据使用本地环境变量中的真实 iFinD / QuantAPI 凭证；
- 执行默认使用 `paper`；
- `eastmoney_stub` 只验证券商边界，不发送真实订单；
- 当前仓库内还不会发出任何真实券商订单。

## 当前策略范围

当前仓库里有两类策略：

- `breakout`：一个简单的区间突破示例策略。
- `auction_wave`：把 [`SKILL.md`](./SKILL.md) 中描述的思路，第一次落成常驻服务版本。

`auction_wave` 目前应当按一个完成度不对称的 MVP 来理解：

- 卖出侧规则大部分已经实现：
  - 在 `09:29` 左右锁定开盘竞价区间，
  - `< 0%`：全仓卖出，
  - `0% ~ 3%`：先卖一半，回落到 `0%` 再卖剩余仓位，
  - `> 3%`：盘中涨幅跌回 `3%` 以下时全卖，
  - `14:30` 未涨停时强制卖出。
- 买入侧规则仍然是近似实现：
  - 近几日活跃度窗口，
  - 突破确认，
  - 粗粒度压力位规避。

`SKILL.md` 里的下列部分，当前还没有落成真正的市场结构逻辑：

- 活跃板块 / 题材筛选，
- 二浪 / 三浪识别，
- “最后一档卖单消失”这类盘口触发信号，
- 东方财富真实下单链路。

## 目录结构

```text
robots/custom/trader/
  config/
  docs/
  scripts/
  src/
```

## 快速开始

```bash
cd robots/custom/trader
./scripts/bootstrap.sh
./scripts/run_once.sh
./scripts/start_bot.sh
./scripts/status_bot.sh
```

本地凭证放在 `config/trader.env`，该文件已加入 git ignore。
策略示例配置在 `config/strategies.example.yaml`。
