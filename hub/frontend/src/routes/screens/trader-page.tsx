import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useForm } from 'react-hook-form'
import {
  Activity,
  Bot,
  ChevronRight,
  FileCode2,
  Play,
  RefreshCw,
  Save,
  SquareTerminal,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Button } from '../../components/ui/button'
import { Badge } from '../../components/ui/badge'
import { Checkbox } from '../../components/ui/checkbox'
import { Input } from '../../components/ui/input'
import { Label } from '../../components/ui/label'
import { Panel } from '../../components/ui/panel'
import { ScrollArea } from '../../components/ui/scroll-area'
import { Select } from '../../components/ui/select'
import { Separator } from '../../components/ui/separator'
import { StatCard } from '../../components/ui/stat-card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs'
import { useRobotAction, useRobotConfigUpdate } from '../../features/robots/mutations'
import { useRobotWorkspaceSummary } from '../../features/robots/queries'
import { cn } from '../../lib/utils'

type MetricCard = {
  label: string
  value: string
  hint: string
  icon: LucideIcon
}

type RecordItem = {
  id: string
  kind: 'signal' | 'order'
  title: string
  summary: string
  timestamp: string
  payload: Record<string, unknown>
}

type TraderConfigForm = {
  broker: string
  id: string
  enabled: boolean
  market: string
  symbol: string
  name: string
  timeframe: string
  strategy: string
  history_window: number
  breakout_lookback: number
  quantity: number
  position_quantity: number
  max_position: number
  stop_loss_pct: number
  take_profit_pct: number
  dry_run: boolean
  enable_buy: boolean
  active_lookback_days: number
  active_threshold_pct: number
  breakout_buffer_pct: number
  resistance_buffer_pct: number
  afternoon_exit_time: string
  limit_up_threshold_pct: number
}

function displayPath(path: string | undefined) {
  if (!path) {
    return '-'
  }
  const parts = path.split('/').filter(Boolean)
  if (parts.length <= 3) {
    return path
  }
  return `.../${parts.slice(-3).join('/')}`
}

function pickText(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) {
      return value
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value)
    }
  }
  return '-'
}

function asNumber(value: unknown, fallback: number) {
  return typeof value === 'number' ? value : fallback
}

function asBoolean(value: unknown, fallback: boolean) {
  return typeof value === 'boolean' ? value : fallback
}

function buildRecordItems(summary: ReturnType<typeof useRobotWorkspaceSummary>['data']): RecordItem[] {
  const signals = (summary?.recent_signals ?? []).map((item, index) => ({
    id: `signal-${index}-${pickText(item, ['timestamp', 'symbol'])}`,
    kind: 'signal' as const,
    title: `${pickText(item, ['symbol', 'code', 'name'])} 信号`,
    summary: `${pickText(item, ['action', 'signal', 'decision'])} · ${pickText(item, ['strategy', 'strategy_id'])}`,
    timestamp: pickText(item, ['timestamp', 'created_at', 'time']),
    payload: item,
  }))
  const orders = (summary?.recent_orders ?? []).map((item, index) => ({
    id: `order-${index}-${pickText(item, ['timestamp', 'symbol'])}`,
    kind: 'order' as const,
    title: `${pickText(item, ['symbol', 'code', 'name'])} 订单`,
    summary: `${pickText(item, ['side', 'action'])} · ${pickText(item, ['status', 'broker', 'result'])}`,
    timestamp: pickText(item, ['timestamp', 'created_at', 'time']),
    payload: item,
  }))
  return [...orders, ...signals].sort((left, right) => right.timestamp.localeCompare(left.timestamp))
}

function buildConfigDefaults(summary: ReturnType<typeof useRobotWorkspaceSummary>['data']): TraderConfigForm {
  const strategy = (summary?.strategies?.[0] as Record<string, unknown> | undefined) ?? {}
  return {
    broker: summary?.broker ?? 'paper',
    id: pickText(strategy, ['id']) === '-' ? 'etf-breakout-demo' : pickText(strategy, ['id']),
    enabled: asBoolean(strategy.enabled, true),
    market: pickText(strategy, ['market']) === '-' ? 'a-share' : pickText(strategy, ['market']),
    symbol: pickText(strategy, ['symbol']) === '-' ? '' : pickText(strategy, ['symbol']),
    name: pickText(strategy, ['name']) === '-' ? '' : pickText(strategy, ['name']),
    timeframe: pickText(strategy, ['timeframe']) === '-' ? '1d' : pickText(strategy, ['timeframe']),
    strategy: pickText(strategy, ['strategy']) === '-' ? 'breakout' : pickText(strategy, ['strategy']),
    history_window: asNumber(strategy.history_window, 20),
    breakout_lookback: asNumber(strategy.breakout_lookback, 5),
    quantity: asNumber(strategy.quantity, 100),
    position_quantity: asNumber(strategy.position_quantity, 0),
    max_position: asNumber(strategy.max_position, 500),
    stop_loss_pct: asNumber(strategy.stop_loss_pct, 0.03),
    take_profit_pct: asNumber(strategy.take_profit_pct, 0.08),
    dry_run: asBoolean(strategy.dry_run, true),
    enable_buy: asBoolean(strategy.enable_buy, false),
    active_lookback_days: asNumber(strategy.active_lookback_days, 10),
    active_threshold_pct: asNumber(strategy.active_threshold_pct, 4),
    breakout_buffer_pct: asNumber(strategy.breakout_buffer_pct, 0.2),
    resistance_buffer_pct: asNumber(strategy.resistance_buffer_pct, 1),
    afternoon_exit_time:
      pickText(strategy, ['afternoon_exit_time']) === '-' ? '14:30' : pickText(strategy, ['afternoon_exit_time']),
    limit_up_threshold_pct: asNumber(strategy.limit_up_threshold_pct, 9.8),
  }
}

function ConfigField({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      {children}
      {hint ? <div className="text-xs text-slate-500">{hint}</div> : null}
    </div>
  )
}

export function TraderPage() {
  const { data, isLoading, isFetching, refetch } = useRobotWorkspaceSummary('trader')
  const runOnce = useRobotAction('trader', 'run-once')
  const start = useRobotAction('trader', 'start')
  const stop = useRobotAction('trader', 'stop')
  const saveConfig = useRobotConfigUpdate('trader')
  const records = useMemo(() => buildRecordItems(data), [data])
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null)
  const [tab, setTab] = useState('overview')
  const selectedRecord = records.find((item) => item.id === selectedRecordId) ?? records[0] ?? null
  const defaults = useMemo(() => buildConfigDefaults(data), [data])
  const form = useForm<TraderConfigForm>({
    defaultValues: defaults,
  })

  useEffect(() => {
    form.reset(defaults)
  }, [defaults, form])

  const cards: MetricCard[] = [
    { label: '可用性', value: data?.available ? '可用' : '缺失', hint: '机器人与运行目录状态', icon: Bot },
    { label: '运行状态', value: data?.running ? `运行中 (${data.pid ?? '-'})` : '已停止', hint: '常驻服务进程', icon: Activity },
    { label: '券商通道', value: data?.broker || '-', hint: '当前下单链路', icon: FileCode2 },
    {
      label: '最近记录',
      value: String((data?.recent_signals?.length || 0) + (data?.recent_orders?.length || 0)),
      hint: '信号与订单条数',
      icon: SquareTerminal,
    },
  ]

  async function onSubmit(values: TraderConfigForm) {
    await saveConfig.mutateAsync({
      broker: values.broker,
      strategy: {
        id: values.id,
        enabled: values.enabled,
        market: values.market,
        symbol: values.symbol,
        name: values.name,
        timeframe: values.timeframe,
        strategy: values.strategy,
        history_window: values.history_window,
        breakout_lookback: values.breakout_lookback,
        quantity: values.quantity,
        position_quantity: values.position_quantity,
        max_position: values.max_position,
        stop_loss_pct: values.stop_loss_pct,
        take_profit_pct: values.take_profit_pct,
        dry_run: values.dry_run,
        enable_buy: values.enable_buy,
        active_lookback_days: values.active_lookback_days,
        active_threshold_pct: values.active_threshold_pct,
        breakout_buffer_pct: values.breakout_buffer_pct,
        resistance_buffer_pct: values.resistance_buffer_pct,
        afternoon_exit_time: values.afternoon_exit_time,
        limit_up_threshold_pct: values.limit_up_threshold_pct,
      },
    })
  }

  const actionError = runOnce.error?.message || start.error?.message || stop.error?.message || saveConfig.error?.message

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
          <Link to="/robots" className="hover:text-slate-900">
            机器人
          </Link>
          <ChevronRight className="h-4 w-4" />
          <span className="text-slate-900">交易员</span>
        </div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">交易员工作台</h1>
            <p className="mt-1 text-sm text-slate-500">以工作台视角管理策略、执行动作和运行记录。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span
              className="contents"
            >
              <Badge variant={data?.running ? 'success' : 'muted'}>
                {data?.running ? '服务运行中' : '服务已停止'}
              </Badge>
            </span>
            <Button variant="outline" onClick={() => void refetch()}>
              <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
              {isFetching ? '刷新中...' : '刷新'}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => {
          return <StatCard key={card.label} icon={card.icon} label={card.label} value={card.value} hint={card.hint} />
        })}
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="overview">概览</TabsTrigger>
            <TabsTrigger value="config">配置</TabsTrigger>
            <TabsTrigger value="records">记录</TabsTrigger>
          </TabsList>
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
          </div>
        </div>

        <TabsContent value="overview">
          <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
            <Panel title="运行摘要" description="保留必要环境信息，不在主视图展开整段本地绝对路径。">
              <div className="grid gap-3 md:grid-cols-2">
                {[
                  ['运行目录', displayPath(data?.runtime_dir), data?.runtime_dir || '-'],
                  ['策略文件', displayPath(data?.strategy_file), data?.strategy_file || '-'],
                  ['状态文件', displayPath(data?.state_file), data?.state_file || '-'],
                  ['日志文件', displayPath(data?.service_log_path), data?.service_log_path || '-'],
                ].map(([label, short, full]) => (
                  <div key={label} className="rounded-lg bg-slate-50 p-4">
                    <div className="text-xs text-slate-500">{label}</div>
                    <div className="mt-2 text-sm font-medium text-slate-900" title={full}>
                      {short}
                    </div>
                  </div>
                ))}
              </div>
              {data?.strategies?.length ? (
                <div className="mt-4 rounded-lg border border-slate-200 p-4">
                  <div className="text-xs text-slate-500">当前策略</div>
                  <div className="mt-2 text-sm font-semibold text-slate-950">
                    {pickText(data.strategies[0] ?? {}, ['name', 'id', 'symbol'])}
                  </div>
                  <div className="mt-1 text-sm text-slate-500">
                    {pickText(data.strategies[0] ?? {}, ['strategy', 'timeframe'])}
                  </div>
                </div>
              ) : null}
              {isLoading ? <div className="mt-4 text-sm text-slate-500">正在读取工作台摘要...</div> : null}
            </Panel>

            <Panel title="执行反馈" description="展示最近一次动作的输出，便于快速确认常驻服务状态。">
              <div className="space-y-4">
                {[
                  ['最近执行输出', runOnce.data?.stdout],
                  ['启动输出', start.data?.stdout],
                  ['停止输出', stop.data?.stdout],
                ]
                  .filter(([, value]) => value)
                  .map(([label, value]) => (
                    <div key={label} className="rounded-lg bg-slate-50 p-4">
                      <div className="text-xs text-slate-500">{label}</div>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-slate-700">{value}</pre>
                    </div>
                  ))}
                <div className="rounded-lg bg-slate-950 p-4">
                  <div className="mb-2 text-xs text-slate-400">服务日志尾部</div>
                  <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-slate-100">
                    {data?.service_log_tail || '暂无日志输出'}
                  </pre>
                </div>
              </div>
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="config">
          <Panel title="策略配置" description="当前先按单策略方式编辑，保存后同步刷新摘要与机器人列表。">
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <ConfigField label="券商通道">
                  <Select {...form.register('broker')}>
                    <option value="paper">paper</option>
                    <option value="eastmoney_stub">eastmoney_stub</option>
                  </Select>
                </ConfigField>
                <ConfigField label="策略 ID">
                  <Input {...form.register('id', { required: true })} />
                </ConfigField>
                <ConfigField label="标的代码">
                  <Input {...form.register('symbol', { required: true })} />
                </ConfigField>
                <ConfigField label="标的名称">
                  <Input {...form.register('name')} />
                </ConfigField>
                <ConfigField label="策略类型">
                  <Select {...form.register('strategy')}>
                    <option value="breakout">breakout</option>
                    <option value="auction_wave">auction_wave</option>
                  </Select>
                </ConfigField>
                <ConfigField label="周期">
                  <Input {...form.register('timeframe')} />
                </ConfigField>
                <ConfigField label="观察窗口">
                  <Input type="number" {...form.register('history_window', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="突破回看">
                  <Input type="number" {...form.register('breakout_lookback', { valueAsNumber: true })} />
                </ConfigField>
              </div>

              <Separator />

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <ConfigField label="下单数量">
                  <Input type="number" {...form.register('quantity', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="持仓数量">
                  <Input type="number" {...form.register('position_quantity', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="最大仓位">
                  <Input type="number" {...form.register('max_position', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="市场">
                  <Input {...form.register('market')} />
                </ConfigField>
                <ConfigField label="止损比例">
                  <Input type="number" step="0.001" {...form.register('stop_loss_pct', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="止盈比例">
                  <Input type="number" step="0.001" {...form.register('take_profit_pct', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="活跃回看天数">
                  <Input type="number" {...form.register('active_lookback_days', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="活跃阈值 %">
                  <Input type="number" step="0.1" {...form.register('active_threshold_pct', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="突破缓冲 %">
                  <Input type="number" step="0.1" {...form.register('breakout_buffer_pct', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="压力位缓冲 %">
                  <Input type="number" step="0.1" {...form.register('resistance_buffer_pct', { valueAsNumber: true })} />
                </ConfigField>
                <ConfigField label="午后退出时间">
                  <Input {...form.register('afternoon_exit_time')} />
                </ConfigField>
                <ConfigField label="涨停阈值 %">
                  <Input type="number" step="0.1" {...form.register('limit_up_threshold_pct', { valueAsNumber: true })} />
                </ConfigField>
              </div>

              <div className="flex flex-wrap gap-6 rounded-lg bg-slate-50 px-4 py-3">
                {[
                  ['enabled', '启用策略'],
                  ['dry_run', 'Dry Run'],
                  ['enable_buy', '允许买入'],
                ].map(([name, label]) => (
                  <label key={name} className="inline-flex items-center gap-3 text-sm text-slate-700">
                    <Checkbox
                      checked={form.watch(name as keyof TraderConfigForm) as boolean}
                      onCheckedChange={(checked) => form.setValue(name as keyof TraderConfigForm, Boolean(checked) as never)}
                    />
                    {label}
                  </label>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={saveConfig.isPending}>
                  <Save className="h-4 w-4" />
                  {saveConfig.isPending ? '保存中...' : '保存配置'}
                </Button>
                <Button type="button" variant="outline" onClick={() => form.reset(defaults)}>
                  重置
                </Button>
                {saveConfig.data?.saved ? (
                  <span className="text-sm text-emerald-700">
                    已保存到 {saveConfig.data.broker}，策略数 {saveConfig.data.strategyCount}
                  </span>
                ) : null}
              </div>
            </form>
          </Panel>
        </TabsContent>

        <TabsContent value="records">
          <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <Panel title="运行记录" description="先看列表，再查看单条记录详情。">
              <ScrollArea className="h-[620px] pr-3">
                <div className="space-y-3">
                  {records.length ? (
                    records.map((record) => (
                      <button
                        key={record.id}
                        type="button"
                        onClick={() => setSelectedRecordId(record.id)}
                        className={cn(
                          'block w-full rounded-lg border p-4 text-left transition',
                          selectedRecord?.id === record.id
                            ? 'border-sky-300 bg-sky-50'
                            : 'border-slate-200 bg-white hover:border-slate-300',
                        )}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-sm font-medium text-slate-900">{record.title}</div>
                          <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-600">
                            {record.kind === 'order' ? '订单' : '信号'}
                          </span>
                        </div>
                        <div className="mt-2 text-sm text-slate-600">{record.summary}</div>
                        <div className="mt-2 text-xs text-slate-500">{record.timestamp}</div>
                      </button>
                    ))
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                      还没有最近记录。先执行一次，或等待常驻服务写入数据。
                    </div>
                  )}
                </div>
              </ScrollArea>
            </Panel>

            <Panel title="记录详情" description="按结构化字段和原始 JSON 双视图展示。">
              {selectedRecord ? (
                <div className="space-y-4">
                  <div className="rounded-lg bg-slate-50 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-base font-semibold text-slate-950">{selectedRecord.title}</div>
                        <div className="mt-1 text-sm text-slate-600">{selectedRecord.summary}</div>
                      </div>
                      <div className="text-xs text-slate-500">{selectedRecord.timestamp}</div>
                    </div>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {Object.entries(selectedRecord.payload).slice(0, 8).map(([key, value]) => (
                      <div key={key} className="rounded-lg border border-slate-200 p-4">
                        <div className="text-xs text-slate-500">{key}</div>
                        <div className="mt-2 break-all text-sm text-slate-900">
                          {typeof value === 'string' ? value : JSON.stringify(value)}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="rounded-lg bg-slate-950 p-4">
                    <div className="mb-2 text-xs text-slate-400">原始记录</div>
                    <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-slate-100">
                      {JSON.stringify(selectedRecord.payload, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                  选择一条运行记录后，在这里查看详情。
                </div>
              )}
            </Panel>
          </div>
        </TabsContent>
      </Tabs>

      {actionError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{actionError}</div>
      ) : null}
    </div>
  )
}
