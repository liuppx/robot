import { CandlestickChart, Play, RefreshCw, SquareTerminal } from 'lucide-react'
import { Link } from '@tanstack/react-router'
import { useMemo } from 'react'

import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Panel } from '../../components/ui/panel'
import { useRobotAction } from '../../platform/robots/mutations'
import { useRobotWorkspaceSummary } from '../../platform/robots/queries'
import { cn } from '../../platform/core/utils'
import { buildStrategyOptions, buildStrategySnapshots, formatBrowserDateTime } from './trader-helpers'

export function TraderHomePage() {
  const { data, isFetching, refetch } = useRobotWorkspaceSummary('trader')
  const runOnce = useRobotAction('trader', 'run-once')
  const start = useRobotAction('trader', 'start')
  const stop = useRobotAction('trader', 'stop')
  const strategySnapshots = useMemo(() => buildStrategySnapshots(data), [data])
  const strategyOptions = useMemo(() => buildStrategyOptions(data, strategySnapshots), [data, strategySnapshots])

  const actionError = runOnce.error?.message || start.error?.message || stop.error?.message

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-5">
        <div className="text-sm text-slate-500">机器人 / 交易员</div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">交易员</h1>
            <p className="mt-1 text-sm text-slate-500">先看机器人整体状态，再从策略列表进入具体策略。</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => runOnce.mutate()} disabled={runOnce.isPending}>
              <Play className="h-4 w-4" />
              {runOnce.isPending ? '执行中...' : '执行一次'}
            </Button>
            <Button variant="outline" onClick={() => start.mutate()} disabled={start.isPending}>
              <Play className="h-4 w-4" />
              {start.isPending ? '启动中...' : '启动'}
            </Button>
            <Button variant="outline" onClick={() => stop.mutate()} disabled={stop.isPending}>
              <SquareTerminal className="h-4 w-4" />
              {stop.isPending ? '停止中...' : '停止'}
            </Button>
            <Button variant="outline" onClick={() => void refetch()}>
              <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
              {isFetching ? '刷新中...' : '刷新'}
            </Button>
          </div>
        </div>
      </div>

      <Panel title="运行概览" description="用一行紧凑状态看清机器人当前状态，不再使用大卡片平铺。">
        <div className="flex flex-wrap gap-3">
          {[
            ['服务', data?.running ? '运行中' : '已停止'],
            ['券商', data?.broker || '-'],
            ['启用策略', String(strategyOptions.filter((item) => item.enabled).length)],
            ['持仓', String(data?.active_position_quantity ?? 0)],
            ['最近动作', data?.last_action || '-'],
            ['最近运行', formatBrowserDateTime(data?.last_run_at)],
            ['最近请求', String(data?.last_cycle_request_count ?? 0)],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div className="text-[11px] text-slate-500">{label}</div>
              <div className="mt-1 text-sm font-medium text-slate-900">{value}</div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="策略列表" description="选择策略进入详情页，再看状态、记录和配置。">
        <div className="space-y-3">
          {strategyOptions.length ? (
            strategyOptions.map((strategy) => (
              <Link
                key={strategy.id}
                to="/robots/trader/$strategyId"
                params={{ strategyId: strategy.id }}
                className="block rounded-lg border border-slate-200 bg-white p-4 hover:border-slate-300"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CandlestickChart className="h-4 w-4 text-slate-400" />
                      <div className="text-sm font-medium text-slate-950">{strategy.name}</div>
                      <Badge variant={strategy.enabled ? 'success' : 'muted'}>
                        {strategy.enabled ? '启用中' : '已停用'}
                      </Badge>
                    </div>
                    <div className="mt-2 text-sm text-slate-600">
                      {strategy.id} · {strategy.symbol}
                    </div>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {[
                      ['最近动作', strategy.lastAction],
                      ['最近观察', formatBrowserDateTime(strategy.observedAt)],
                      ['持仓数量', String(strategy.positionQuantity)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
                        <div className="text-[11px] text-slate-500">{label}</div>
                        <div className="mt-1 text-sm text-slate-900">{value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </Link>
            ))
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
              还没有策略配置。先在配置中新增策略。
            </div>
          )}
        </div>
      </Panel>

      {actionError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{actionError}</div>
      ) : null}
    </div>
  )
}
