import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useForm } from 'react-hook-form'
import {
  Activity,
  Bot,
  ChevronRight,
  FileCode2,
  Flag,
  Layers3,
  Play,
  RefreshCw,
  Save,
  SquareTerminal,
  Wallet,
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
import { useRobotAction, useRobotConfigUpdate } from '../../platform/robots/mutations'
import { useRobotWorkspaceSummary } from '../../platform/robots/queries'
import { cn } from '../../platform/core/utils'

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

type StrategySnapshot = {
  id: string
  name: string
  symbol: string
  strategy: string
  timeframe: string
  enabled: boolean
  lastAction: string
  lastReason: string
  latestPrice: number | null
  observedAt: string | null
  riskOk: boolean | null
  riskReason: string | null
  positionQuantity: number
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

type RecordFilter = 'all' | 'signal' | 'order'

type RecordDetailField = {
  label: string
  value: string
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
    id: `signal-${index}-${pickText(item, ['ts', 'timestamp', 'symbol'])}`,
    kind: 'signal' as const,
    title: `${pickText(item, ['symbol', 'code', 'name'])} 信号`,
    summary: `${pickText(item, ['action', 'signal', 'decision'])} · ${pickText(item, ['strategyId', 'strategy', 'strategy_id'])}`,
    timestamp: pickText(item, ['ts', 'timestamp', 'created_at', 'time']),
    payload: item,
  }))
  const orders = (summary?.recent_orders ?? []).map((item, index) => ({
    id: `order-${index}-${pickText(item, ['ts', 'timestamp', 'symbol'])}`,
    kind: 'order' as const,
    title: `${pickText(item, ['symbol', 'code', 'name'])} 订单`,
    summary: `${pickText(item, ['side', 'action'])} · ${pickText(item, ['strategyId', 'status', 'broker', 'result'])}`,
    timestamp: pickText(item, ['ts', 'timestamp', 'created_at', 'time']),
    payload: item,
  }))
  return [...orders, ...signals].sort((left, right) => right.timestamp.localeCompare(left.timestamp))
}

function buildStrategySnapshots(summary: ReturnType<typeof useRobotWorkspaceSummary>['data']): StrategySnapshot[] {
  return (summary?.strategy_snapshots ?? []).map((item, index) => ({
    id: pickText(item, ['id']) === '-' ? `strategy-${index}` : pickText(item, ['id']),
    name: pickText(item, ['name']) === '-' ? '未命名策略' : pickText(item, ['name']),
    symbol: pickText(item, ['symbol']),
    strategy: pickText(item, ['strategy']),
    timeframe: pickText(item, ['timeframe']),
    enabled: asBoolean(item.enabled, true),
    lastAction: pickText(item, ['lastAction']),
    lastReason: pickText(item, ['lastReason']),
    latestPrice: typeof item.latestPrice === 'number' ? item.latestPrice : null,
    observedAt: pickText(item, ['observedAt']) === '-' ? null : pickText(item, ['observedAt']),
    riskOk: typeof item.riskOk === 'boolean' ? item.riskOk : null,
    riskReason: pickText(item, ['riskReason']) === '-' ? null : pickText(item, ['riskReason']),
    positionQuantity: asNumber(item.positionQuantity, 0),
  }))
}

function buildConfigDefaults(
  summary: ReturnType<typeof useRobotWorkspaceSummary>['data'],
  strategyId?: string | null,
): TraderConfigForm {
  const strategies = (summary?.strategies ?? []) as Array<Record<string, unknown>>
  const matchedStrategy =
    strategies.find((item) => pickText(item, ['id']) === strategyId) ??
    strategies[0] ??
    ({} as Record<string, unknown>)
  const strategy = matchedStrategy
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

function buildNewStrategyDefaults(broker: string): TraderConfigForm {
  return {
    broker,
    id: '',
    enabled: true,
    market: 'a-share',
    symbol: '',
    name: '',
    timeframe: '1d',
    strategy: 'breakout',
    history_window: 20,
    breakout_lookback: 5,
    quantity: 100,
    position_quantity: 0,
    max_position: 500,
    stop_loss_pct: 0.03,
    take_profit_pct: 0.08,
    dry_run: true,
    enable_buy: false,
    active_lookback_days: 10,
    active_threshold_pct: 4,
    breakout_buffer_pct: 0.2,
    resistance_buffer_pct: 1,
    afternoon_exit_time: '14:30',
    limit_up_threshold_pct: 9.8,
  }
}

function buildCopiedStrategyDefaults(source: TraderConfigForm): TraderConfigForm {
  const nextId = source.id ? `${source.id}-copy` : ''
  return {
    ...source,
    id: nextId,
    name: source.name ? `${source.name} 副本` : '',
    enabled: false,
  }
}

function buildRecordDetailFields(record: RecordItem): RecordDetailField[] {
  const payload = record.payload
  if (record.kind === 'signal') {
    return [
      { label: '策略 ID', value: pickText(payload, ['strategyId', 'strategy_id', 'strategy']) },
      { label: '动作', value: pickText(payload, ['action', 'signal', 'decision']) },
      { label: '标的', value: pickText(payload, ['symbol', 'code', 'name']) },
      { label: '价格', value: pickText(payload, ['price', 'lastPrice', 'latestPrice']) },
      { label: '时间', value: pickText(payload, ['ts', 'timestamp', 'created_at', 'time']) },
      { label: '原因', value: pickText(payload, ['reason', 'message', 'note']) },
    ]
  }

  return [
    { label: '策略 ID', value: pickText(payload, ['strategyId', 'strategy_id', 'strategy']) },
    { label: '方向', value: pickText(payload, ['side', 'action']) },
    { label: '状态', value: pickText(payload, ['status', 'result']) },
    { label: '标的', value: pickText(payload, ['symbol', 'code', 'name']) },
    { label: '数量', value: pickText(payload, ['quantity', 'qty', 'filledQuantity']) },
    { label: '券商', value: pickText(payload, ['broker', 'account', 'accountId']) },
    { label: '订单号', value: pickText(payload, ['orderId', 'order_id', 'id']) },
    { label: '时间', value: pickText(payload, ['ts', 'timestamp', 'created_at', 'time']) },
    { label: '原因', value: pickText(payload, ['reason', 'message', 'note']) },
  ]
}

function pickRecordStrategyId(record: RecordItem | null): string | null {
  if (!record) {
    return null
  }
  const strategyId = pickText(record.payload, ['strategyId', 'strategy_id', 'strategy'])
  return strategyId === '-' ? null : strategyId
}

function buildRecordContextFields(record: RecordItem): RecordDetailField[] {
  const payload = record.payload
  const ignoredKeys = new Set([
    'strategyId',
    'strategy_id',
    'strategy',
    'action',
    'signal',
    'decision',
    'side',
    'status',
    'result',
    'symbol',
    'code',
    'name',
    'price',
    'lastPrice',
    'latestPrice',
    'quantity',
    'qty',
    'filledQuantity',
    'broker',
    'account',
    'accountId',
    'orderId',
    'order_id',
    'id',
    'ts',
    'timestamp',
    'created_at',
    'time',
    'reason',
    'message',
    'note',
  ])

  return Object.entries(payload)
    .filter(([key]) => !ignoredKeys.has(key))
    .slice(0, 8)
    .map(([key, value]) => ({
      label: key,
      value: typeof value === 'string' ? value : JSON.stringify(value),
    }))
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
  const strategySnapshots = useMemo(() => buildStrategySnapshots(data), [data])
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(null)
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(null)
  const [draftStrategy, setDraftStrategy] = useState<TraderConfigForm | null>(null)
  const [recordFilter, setRecordFilter] = useState<RecordFilter>('all')
  const [tab, setTab] = useState('overview')
  const filteredRecords = useMemo(() => {
    if (recordFilter === 'all') {
      return records
    }
    return records.filter((item) => item.kind === recordFilter)
  }, [recordFilter, records])
  const strategyOptions = useMemo(
    () =>
      ((data?.strategies ?? []) as Array<Record<string, unknown>>).map((item, index) => ({
        id: pickText(item, ['id']) === '-' ? `strategy-${index}` : pickText(item, ['id']),
        name: pickText(item, ['name']) === '-' ? `策略 ${index + 1}` : pickText(item, ['name']),
        symbol: pickText(item, ['symbol']),
        enabled: asBoolean(item.enabled, true),
      })),
    [data],
  )
  const effectiveStrategyId = selectedStrategyId ?? strategyOptions[0]?.id ?? null
  const defaults = useMemo(
    () => draftStrategy ?? buildConfigDefaults(data, effectiveStrategyId),
    [data, draftStrategy, effectiveStrategyId],
  )
  const selectedRecord = filteredRecords.find((item) => item.id === selectedRecordId) ?? filteredRecords[0] ?? null
  const selectedRecordStrategyId = useMemo(() => pickRecordStrategyId(selectedRecord), [selectedRecord])
  const selectedRecordStrategy = useMemo(
    () => strategySnapshots.find((item) => item.id === selectedRecordStrategyId) ?? null,
    [selectedRecordStrategyId, strategySnapshots],
  )
  const selectedRecordFields = useMemo(
    () => (selectedRecord ? buildRecordDetailFields(selectedRecord) : []),
    [selectedRecord],
  )
  const selectedRecordContextFields = useMemo(
    () => (selectedRecord ? buildRecordContextFields(selectedRecord) : []),
    [selectedRecord],
  )
  const form = useForm<TraderConfigForm>({
    defaultValues: defaults,
  })

  useEffect(() => {
    if (!strategyOptions.length) {
      if (selectedStrategyId !== null) {
        setSelectedStrategyId(null)
      }
      return
    }
    if (!selectedStrategyId || !strategyOptions.some((item) => item.id === selectedStrategyId)) {
      setSelectedStrategyId(strategyOptions[0].id)
    }
  }, [selectedStrategyId, strategyOptions])

  useEffect(() => {
    setSelectedRecordId((currentId) => {
      if (!filteredRecords.length) {
        return null
      }
      if (currentId && filteredRecords.some((item) => item.id === currentId)) {
        return currentId
      }
      return filteredRecords[0].id
    })
  }, [filteredRecords])

  useEffect(() => {
    form.reset(defaults)
  }, [defaults, form])

  function selectSavedStrategy(strategyId: string) {
    setDraftStrategy(null)
    setSelectedStrategyId(strategyId)
  }

  function createStrategyDraft() {
    setSelectedStrategyId(null)
    setDraftStrategy(buildNewStrategyDefaults(data?.broker ?? 'paper'))
  }

  function duplicateCurrentStrategy() {
    setSelectedStrategyId(null)
    setDraftStrategy(buildCopiedStrategyDefaults(form.getValues()))
  }

  const cards: MetricCard[] = [
    { label: '可用性', value: data?.available ? '可用' : '缺失', hint: '机器人与运行目录状态', icon: Bot },
    { label: '运行状态', value: data?.running ? `运行中 (${data.pid ?? '-'})` : '已停止', hint: '常驻服务进程', icon: Activity },
    { label: '策略数', value: String(data?.strategy_count ?? 0), hint: '当前已加载策略数量', icon: Layers3 },
    { label: '持仓数量', value: String(data?.active_position_quantity ?? 0), hint: '按摘要聚合的持仓数量', icon: Wallet },
    { label: '券商通道', value: data?.broker || '-', hint: '当前下单链路', icon: FileCode2 },
    { label: '最近运行', value: data?.last_action || '-', hint: data?.last_run_at || '暂无最近运行时间', icon: Flag },
  ]

  async function onSubmit(values: TraderConfigForm) {
    await saveConfig.mutateAsync({
      broker: values.broker,
      strategy_id: draftStrategy ? null : effectiveStrategyId,
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
    setDraftStrategy(null)
    setSelectedStrategyId(values.id)
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
            <p className="mt-1 text-sm text-slate-500">先看策略当前状态，再决定执行动作和查看记录详情。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant={data?.running ? 'success' : 'muted'}>{data?.running ? '服务运行中' : '服务已停止'}</Badge>
            <Button variant="outline" onClick={() => void refetch()}>
              <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
              {isFetching ? '刷新中...' : '刷新'}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => (
          <StatCard key={card.label} icon={card.icon} label={card.label} value={card.value} hint={card.hint} />
        ))}
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
          <div className="space-y-4">
            <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
              <Panel title="策略概览" description="按策略查看最近动作、观察价格、持仓和风险结果。">
                <div className="space-y-3">
                  {strategySnapshots.length ? (
                    strategySnapshots.map((strategy) => (
                      <div key={strategy.id} className="rounded-lg border border-slate-200 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <div className="text-sm font-semibold text-slate-950">{strategy.name}</div>
                              <Badge variant={strategy.enabled ? 'success' : 'muted'}>
                                {strategy.enabled ? '启用中' : '已停用'}
                              </Badge>
                              <Badge variant={strategy.lastAction === 'buy' || strategy.lastAction === 'sell' ? 'default' : 'muted'}>
                                {strategy.lastAction}
                              </Badge>
                            </div>
                            <div className="mt-1 text-sm text-slate-500">
                              {strategy.symbol} · {strategy.strategy} · {strategy.timeframe}
                            </div>
                          </div>
                          <div className="text-right">
                            <div className="text-xs text-slate-500">最近观察</div>
                            <div className="mt-1 text-sm font-medium text-slate-900">{strategy.observedAt ?? '-'}</div>
                          </div>
                        </div>
                        <div className="mt-4 grid gap-3 md:grid-cols-4">
                          {[
                            ['最新价格', strategy.latestPrice == null ? '-' : strategy.latestPrice.toFixed(3)],
                            ['持仓数量', String(strategy.positionQuantity)],
                            ['风险检查', strategy.riskOk == null ? '-' : strategy.riskOk ? '通过' : '未通过'],
                            ['风险说明', strategy.riskReason ?? '-'],
                          ].map(([label, value]) => (
                            <div key={label} className="rounded-lg bg-slate-50 p-3">
                              <div className="text-xs text-slate-500">{label}</div>
                              <div className="mt-1 text-sm font-medium text-slate-900">{value}</div>
                            </div>
                          ))}
                        </div>
                        <div className="mt-4 rounded-lg bg-slate-50 p-3">
                          <div className="text-xs text-slate-500">最近结论</div>
                          <div className="mt-1 text-sm text-slate-900">{strategy.lastReason}</div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                      还没有策略摘要。先配置策略并执行一次。
                    </div>
                  )}
                </div>
              </Panel>

              <Panel title="运行摘要" description="先看最近一轮运行、当前记录数量和关键路径。">
                <div className="grid gap-3 md:grid-cols-2">
                  {[
                    ['最近执行动作', data?.last_action || '-'],
                    ['最近运行时间', data?.last_run_at || '-'],
                    ['信号条数', String(data?.signal_count ?? 0)],
                    ['订单条数', String(data?.order_count ?? 0)],
                    ['最近信号时间', data?.last_signal_at || '-'],
                    ['最近订单时间', data?.last_order_at || '-'],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg bg-slate-50 p-4">
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className="mt-2 text-sm font-medium text-slate-900">{value}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {[
                    ['运行目录', displayPath(data?.runtime_dir), data?.runtime_dir || '-'],
                    ['策略文件', displayPath(data?.strategy_file), data?.strategy_file || '-'],
                    ['状态文件', displayPath(data?.state_file), data?.state_file || '-'],
                    ['日志文件', displayPath(data?.service_log_path), data?.service_log_path || '-'],
                  ].map(([label, short, full]) => (
                    <div key={label} className="rounded-lg border border-slate-200 p-4">
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className="mt-2 text-sm font-medium text-slate-900" title={full}>
                        {short}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 rounded-lg bg-slate-50 p-4">
                  <div className="text-xs text-slate-500">最近执行原因</div>
                  <div className="mt-2 text-sm text-slate-900">{data?.last_reason || '暂无最近执行原因'}</div>
                </div>
                {isLoading ? <div className="mt-4 text-sm text-slate-500">正在读取工作台摘要...</div> : null}
              </Panel>
            </div>

            <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
              <Panel title="执行反馈" description="展示最近一次动作输出和服务日志尾部，便于确认动作是否实际生效。">
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

              <Panel title="近期动态" description="先看最近发生的信号和订单，再进入记录详情。">
                <div className="space-y-3">
                  {records.slice(0, 6).length ? (
                    records.slice(0, 6).map((record) => (
                      <button
                        key={record.id}
                        type="button"
                        onClick={() => {
                          setSelectedRecordId(record.id)
                          setTab('records')
                        }}
                        className="block w-full rounded-lg border border-slate-200 p-4 text-left hover:border-slate-300"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="text-sm font-medium text-slate-900">{record.title}</div>
                          <Badge variant={record.kind === 'order' ? 'default' : 'muted'}>
                            {record.kind === 'order' ? '订单' : '信号'}
                          </Badge>
                        </div>
                        <div className="mt-2 text-sm text-slate-600">{record.summary}</div>
                        <div className="mt-2 text-xs text-slate-500">{record.timestamp}</div>
                      </button>
                    ))
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                      还没有近期动态。先执行一次，或等待常驻服务写入数据。
                    </div>
                  )}
                </div>
              </Panel>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="config">
          <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
            <Panel title="策略列表" description="先选择要编辑的策略，再在右侧修改参数。">
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  <Button type="button" size="sm" onClick={createStrategyDraft}>
                    新建策略
                  </Button>
                  <Button type="button" size="sm" variant="outline" onClick={duplicateCurrentStrategy}>
                    复制当前
                  </Button>
                </div>
                {draftStrategy ? (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-medium text-amber-950">
                        {draftStrategy.id ? `新草稿 · ${draftStrategy.id}` : '新草稿'}
                      </div>
                      <Badge variant="muted">未保存</Badge>
                    </div>
                    <div className="mt-2 text-xs text-amber-800">当前右侧表单处于新增或复制状态，保存后会追加到策略列表。</div>
                  </div>
                ) : null}
                {strategyOptions.length ? (
                  strategyOptions.map((strategy) => (
                    <button
                      key={strategy.id}
                      type="button"
                      onClick={() => selectSavedStrategy(strategy.id)}
                      className={cn(
                        'block w-full rounded-lg border p-4 text-left transition',
                        !draftStrategy && effectiveStrategyId === strategy.id
                          ? 'border-sky-300 bg-sky-50'
                          : 'border-slate-200 bg-white hover:border-slate-300',
                      )}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-medium text-slate-900">{strategy.name}</div>
                        <Badge variant={strategy.enabled ? 'success' : 'muted'}>
                          {strategy.enabled ? '启用' : '停用'}
                        </Badge>
                      </div>
                      <div className="mt-2 text-sm text-slate-600">{strategy.id}</div>
                      <div className="mt-1 text-xs text-slate-500">{strategy.symbol}</div>
                    </button>
                  ))
                ) : (
                  <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                    还没有策略配置。先补充 `strategies.yaml`。
                  </div>
                )}
              </div>
            </Panel>

            <Panel
              title="策略配置"
              description={draftStrategy ? '当前是新增草稿。保存后会作为新策略写入，不影响已有策略。' : '按当前选中的策略保存，不覆盖其他策略。'}
            >
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
                        onCheckedChange={(checked) =>
                          form.setValue(name as keyof TraderConfigForm, Boolean(checked) as never)
                        }
                      />
                      {label}
                    </label>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <Button type="submit" disabled={saveConfig.isPending}>
                    <Save className="h-4 w-4" />
                    {saveConfig.isPending ? '保存中...' : draftStrategy ? '保存为新策略' : '保存配置'}
                  </Button>
                  <Button type="button" variant="outline" onClick={() => form.reset(defaults)}>
                    重置
                  </Button>
                  {draftStrategy ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setDraftStrategy(null)
                        if (strategyOptions[0]) {
                          setSelectedStrategyId(strategyOptions[0].id)
                        }
                      }}
                    >
                      取消草稿
                    </Button>
                  ) : null}
                  {saveConfig.data?.saved ? (
                    <span className="text-sm text-emerald-700">
                      已保存到 {saveConfig.data.broker}，策略数 {saveConfig.data.strategyCount}
                    </span>
                  ) : null}
                </div>
              </form>
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="records">
          <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <Panel title="运行记录" description="按全部、信号、订单切换，再查看单条记录详情。">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <Tabs value={recordFilter} onValueChange={(value) => setRecordFilter(value as RecordFilter)}>
                  <TabsList className="h-10">
                    <TabsTrigger value="all" className="min-w-[72px]">
                      全部 {records.length}
                    </TabsTrigger>
                    <TabsTrigger value="signal" className="min-w-[72px]">
                      信号 {records.filter((item) => item.kind === 'signal').length}
                    </TabsTrigger>
                    <TabsTrigger value="order" className="min-w-[72px]">
                      订单 {records.filter((item) => item.kind === 'order').length}
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
                <div className="text-xs text-slate-500">当前列表 {filteredRecords.length} 条</div>
              </div>
              <ScrollArea className="h-[560px] pr-3">
                <div className="space-y-3">
                  {filteredRecords.length ? (
                    filteredRecords.map((record) => (
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
                      当前筛选下还没有记录。先执行一次，或切换到其他记录类型。
                    </div>
                  )}
                </div>
              </ScrollArea>
            </Panel>

            <Panel title="记录详情" description="先看关键信息和关联策略，再查看上下文与原始 JSON。">
              {selectedRecord ? (
                <div className="space-y-4">
                  <div className="rounded-lg bg-slate-50 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="text-base font-semibold text-slate-950">{selectedRecord.title}</div>
                          <Badge variant={selectedRecord.kind === 'order' ? 'default' : 'muted'}>
                            {selectedRecord.kind === 'order' ? '订单' : '信号'}
                          </Badge>
                        </div>
                        <div className="mt-1 text-sm text-slate-600">{selectedRecord.summary}</div>
                      </div>
                      <div className="text-xs text-slate-500">{selectedRecord.timestamp}</div>
                    </div>
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
                    <div className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2">
                        {selectedRecordFields.map((item) => (
                          <div key={item.label} className="rounded-lg border border-slate-200 p-4">
                            <div className="text-xs text-slate-500">{item.label}</div>
                            <div className="mt-2 break-all text-sm font-medium text-slate-900">{item.value}</div>
                          </div>
                        ))}
                      </div>

                      <div className="rounded-lg border border-slate-200 p-4">
                        <div className="text-sm font-medium text-slate-950">上下文字段</div>
                        {selectedRecordContextFields.length ? (
                          <div className="mt-3 grid gap-3 md:grid-cols-2">
                            {selectedRecordContextFields.map((item) => (
                              <div key={item.label} className="rounded-lg bg-slate-50 p-3">
                                <div className="text-xs text-slate-500">{item.label}</div>
                                <div className="mt-1 break-all text-sm text-slate-900">{item.value}</div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="mt-3 text-sm text-slate-500">当前记录没有额外上下文字段。</div>
                        )}
                      </div>
                    </div>

                    <div className="space-y-4">
                      <div className="rounded-lg border border-slate-200 p-4">
                        <div className="text-sm font-medium text-slate-950">关联策略</div>
                        {selectedRecordStrategy ? (
                          <div className="mt-3 space-y-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <div className="text-sm font-medium text-slate-900">{selectedRecordStrategy.name}</div>
                              <Badge variant={selectedRecordStrategy.enabled ? 'success' : 'muted'}>
                                {selectedRecordStrategy.enabled ? '启用中' : '已停用'}
                              </Badge>
                            </div>
                            <div className="text-sm text-slate-600">
                              {selectedRecordStrategy.id} · {selectedRecordStrategy.symbol} · {selectedRecordStrategy.strategy}
                            </div>
                            <div className="grid gap-3">
                              {[
                                ['最近动作', selectedRecordStrategy.lastAction],
                                ['最近观察', selectedRecordStrategy.observedAt ?? '-'],
                                ['持仓数量', String(selectedRecordStrategy.positionQuantity)],
                                ['风险结果', selectedRecordStrategy.riskOk == null ? '-' : selectedRecordStrategy.riskOk ? '通过' : '未通过'],
                              ].map(([label, value]) => (
                                <div key={label} className="rounded-lg bg-slate-50 p-3">
                                  <div className="text-xs text-slate-500">{label}</div>
                                  <div className="mt-1 text-sm text-slate-900">{value}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ) : (
                          <div className="mt-3 text-sm text-slate-500">当前记录没有匹配到策略摘要，可能是旧记录或策略已变更。</div>
                        )}
                      </div>

                      <div className="rounded-lg bg-slate-950 p-4">
                        <div className="mb-2 text-xs text-slate-400">原始记录</div>
                        <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-slate-100">
                          {JSON.stringify(selectedRecord.payload, null, 2)}
                        </pre>
                      </div>
                    </div>
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
