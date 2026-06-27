import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { Play, RefreshCw, Save, SquareTerminal } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useForm } from 'react-hook-form'

import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Input } from '../../components/ui/input'
import { Panel } from '../../components/ui/panel'
import { Select } from '../../components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs'
import { useRobotAction, useRobotConfigUpdate } from '../../platform/robots/mutations'
import { useRobotWorkspaceSummary } from '../../platform/robots/queries'
import { cn } from '../../platform/core/utils'
import {
  buildConfigDefaults,
  buildCopiedStrategyDefaults,
  buildNewStrategyDefaults,
  buildRecordItems,
  buildRecentStrategyRecords,
  buildStrategyOptions,
  buildStrategySnapshots,
  filterRecordsByStrategy,
  formatBrowserDateTime,
  normalizeStrategyPayload,
} from './trader-helpers'
import { ConfigField, StrategyBooleanToggles } from './trader-form-components'
import type { RecordFilter, TraderConfigForm } from './trader-helpers'

function CompactFieldList({
  items,
}: {
  items: Array<{ label: string; value: string; valueClassName?: string }>
}) {
  return (
    <div className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
      {items.map((item) => (
        <div
          key={item.label}
          className="grid gap-1 px-4 py-3 sm:grid-cols-[120px_minmax(0,1fr)] sm:items-center sm:gap-4"
        >
          <div className="text-xs text-slate-500">{item.label}</div>
          <div className={cn('min-w-0 break-all text-sm text-slate-900', item.valueClassName)}>{item.value}</div>
        </div>
      ))}
    </div>
  )
}

function RecordTable({
  strategyId,
  records,
}: {
  strategyId: string
  records: Array<{
    id: string
    kind: 'signal' | 'order'
    title: string
    summary: string
    timestamp: string
  }>
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <div className="hidden grid-cols-[168px_minmax(0,1fr)_96px_176px] gap-4 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500 md:grid">
        <div>时间</div>
        <div>记录</div>
        <div>类型</div>
        <div>操作</div>
      </div>
      <div className="divide-y divide-slate-200">
        {records.map((record) => (
          <div key={record.id} className="px-4 py-3">
            <div className="grid gap-3 md:grid-cols-[168px_minmax(0,1fr)_96px_176px] md:items-center md:gap-4">
              <div className="text-xs text-slate-500 md:text-sm">{formatBrowserDateTime(record.timestamp)}</div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-slate-900">{record.title}</div>
                <div className="mt-1 truncate text-sm text-slate-600">{record.summary}</div>
              </div>
              <div>
                <Badge variant={record.kind === 'order' ? 'default' : 'muted'}>
                  {record.kind === 'order' ? '订单' : '信号'}
                </Badge>
              </div>
              <div>
                <Link
                  to="/robots/trader/$strategyId/records/$recordId"
                  params={{ strategyId, recordId: record.id }}
                  className="text-sm font-medium text-sky-700 hover:text-sky-800"
                >
                  查看详情
                </Link>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function TraderStrategyPage() {
  const { strategyId } = useParams({ strict: false }) as { strategyId?: string }
  const navigate = useNavigate()
  const { data, isLoading, isFetching, refetch } = useRobotWorkspaceSummary('trader')
  const runOnce = useRobotAction('trader', 'run-once')
  const start = useRobotAction('trader', 'start')
  const stop = useRobotAction('trader', 'stop')
  const saveConfig = useRobotConfigUpdate('trader')
  const strategySnapshots = useMemo(() => buildStrategySnapshots(data), [data])
  const strategyOptions = useMemo(() => buildStrategyOptions(data, strategySnapshots), [data, strategySnapshots])
  const effectiveStrategyId = strategyId ?? strategyOptions[0]?.id ?? null
  const selectedStrategySnapshot = useMemo(
    () => strategySnapshots.find((item) => item.id === effectiveStrategyId) ?? null,
    [effectiveStrategyId, strategySnapshots],
  )
  const selectedStrategyOption = useMemo(
    () => strategyOptions.find((item) => item.id === effectiveStrategyId) ?? null,
    [effectiveStrategyId, strategyOptions],
  )
  const [draftStrategy, setDraftStrategy] = useState<TraderConfigForm | null>(null)
  const [tab, setTab] = useState('status')
  const [recordFilter, setRecordFilter] = useState<RecordFilter>('all')
  const records = useMemo(() => buildRecordItems(data), [data])
  const currentStrategyRecords = useMemo(() => filterRecordsByStrategy(records, effectiveStrategyId), [effectiveStrategyId, records])
  const filteredCurrentStrategyRecords = useMemo(() => {
    if (recordFilter === 'all') {
      return currentStrategyRecords
    }
    return currentStrategyRecords.filter((item) => item.kind === recordFilter)
  }, [currentStrategyRecords, recordFilter])
  const recentSelectedStrategyRecords = useMemo(
    () => buildRecentStrategyRecords(records, effectiveStrategyId),
    [effectiveStrategyId, records],
  )

  useEffect(() => {
    if (!strategyId && strategyOptions[0]) {
      void navigate({ to: '/robots/trader/$strategyId', params: { strategyId: strategyOptions[0].id }, replace: true })
    }
  }, [navigate, strategyId, strategyOptions])

  const defaults = useMemo(
    () => draftStrategy ?? buildConfigDefaults(data, effectiveStrategyId),
    [data, draftStrategy, effectiveStrategyId],
  )
  const form = useForm<TraderConfigForm>({ defaultValues: defaults })

  useEffect(() => {
    form.reset(defaults)
  }, [defaults, form])

  function createStrategyDraft() {
    setDraftStrategy(buildNewStrategyDefaults(data?.broker ?? 'paper'))
    setTab('config')
  }

  function duplicateCurrentStrategy() {
    setDraftStrategy(buildCopiedStrategyDefaults(form.getValues()))
    setTab('config')
  }

  async function toggleStrategyEnabled() {
    if (!selectedStrategyOption || saveConfig.isPending) {
      return
    }
    await saveConfig.mutateAsync({
      broker: data?.broker ?? 'paper',
      strategy_id: selectedStrategyOption.id,
      strategy: normalizeStrategyPayload(selectedStrategyOption.payload, !selectedStrategyOption.enabled),
    })
  }

  async function onSubmit(values: TraderConfigForm) {
    await saveConfig.mutateAsync({
      broker: values.broker,
      strategy_id: draftStrategy ? null : effectiveStrategyId,
      strategy: values,
    })
    setDraftStrategy(null)
    if (values.id !== effectiveStrategyId) {
      await navigate({ to: '/robots/trader/$strategyId', params: { strategyId: values.id } })
    }
  }

  const actionError = runOnce.error?.message || start.error?.message || stop.error?.message || saveConfig.error?.message

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
          <Link to="/robots">机器人</Link>
          <span>/</span>
          <Link to="/robots/trader">交易员</Link>
          <span>/</span>
          <span className="text-slate-900">{selectedStrategySnapshot?.name ?? effectiveStrategyId ?? '策略'}</span>
        </div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">{selectedStrategySnapshot?.name ?? '策略详情'}</h1>
            <p className="mt-1 text-sm text-slate-500">围绕单个策略查看状态、记录和配置。</p>
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

      <Panel title="当前策略" description="这里不再重复放大一排大卡片，只保留单策略当前最关键的信息。">
        {selectedStrategySnapshot ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <CompactFieldList
              items={[
                { label: '策略名称', value: selectedStrategySnapshot.name },
                { label: '策略 ID', value: selectedStrategySnapshot.id },
                { label: '标的', value: selectedStrategySnapshot.symbol },
                { label: '策略类型', value: selectedStrategySnapshot.strategy },
                { label: '周期', value: selectedStrategySnapshot.timeframe },
                { label: '最近观察', value: formatBrowserDateTime(selectedStrategySnapshot.observedAt) },
                {
                  label: '最新价格',
                  value:
                    selectedStrategySnapshot.latestPrice == null ? '-' : selectedStrategySnapshot.latestPrice.toFixed(3),
                },
              ]}
            />
            <div className="space-y-3">
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                <div className="text-xs text-slate-500">状态</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge variant={selectedStrategySnapshot.enabled ? 'success' : 'muted'}>
                    {selectedStrategySnapshot.enabled ? '启用中' : '已停用'}
                  </Badge>
                  <Badge
                    variant={
                      selectedStrategySnapshot.lastAction === 'buy' || selectedStrategySnapshot.lastAction === 'sell'
                        ? 'default'
                        : 'muted'
                    }
                  >
                    {selectedStrategySnapshot.lastAction}
                  </Badge>
                </div>
              </div>
              <CompactFieldList
                items={[
                  { label: '持仓数量', value: String(selectedStrategySnapshot.positionQuantity) },
                  {
                    label: '风险结果',
                    value:
                      selectedStrategySnapshot.riskOk == null ? '-' : selectedStrategySnapshot.riskOk ? '通过' : '未通过',
                  },
                  { label: '风险说明', value: selectedStrategySnapshot.riskReason ?? '-' },
                ]}
              />
            </div>
          </div>
        ) : (
          <div className="text-sm text-slate-500">{isLoading ? '正在读取策略...' : '未找到该策略。'}</div>
        )}
      </Panel>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="status">状态</TabsTrigger>
          <TabsTrigger value="records">记录</TabsTrigger>
          <TabsTrigger value="config">配置</TabsTrigger>
        </TabsList>

        <TabsContent value="status">
          <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
            <Panel title="运行摘要" description="这里保留全局运行摘要，帮助判断本轮 run_once 的范围和结果。">
              <CompactFieldList
                items={[
                  { label: '最近执行动作', value: data?.last_action || '-' },
                  { label: '最近运行时间', value: formatBrowserDateTime(data?.last_run_at) },
                  { label: '最近评估策略', value: String(data?.last_cycle_strategy_count ?? 0) },
                  { label: '最近数据请求', value: String(data?.last_cycle_request_count ?? 0) },
                  { label: '最近信号', value: formatBrowserDateTime(data?.last_signal_at) },
                  { label: '最近订单', value: formatBrowserDateTime(data?.last_order_at) },
                ]}
              />
            </Panel>

            <Panel title="当前策略影响" description="固定聚焦当前策略，而不是在大工作台里和其他策略混看。">
              {selectedStrategySnapshot ? (
                <div className="space-y-4">
                  <CompactFieldList
                    items={[
                      { label: '最近动作', value: selectedStrategySnapshot.lastAction },
                      { label: '最近观察', value: formatBrowserDateTime(selectedStrategySnapshot.observedAt) },
                      { label: '持仓数量', value: String(selectedStrategySnapshot.positionQuantity) },
                      { label: '关联记录', value: String(currentStrategyRecords.length) },
                      { label: '最近结论', value: selectedStrategySnapshot.lastReason },
                    ]}
                  />
                  <div>
                    <div className="text-sm font-medium text-slate-950">最近记录</div>
                    {recentSelectedStrategyRecords.length ? (
                      <div className="mt-3">
                        <RecordTable strategyId={effectiveStrategyId ?? ''} records={recentSelectedStrategyRecords} />
                      </div>
                    ) : (
                      <div className="mt-3 text-sm text-slate-500">当前策略还没有最近记录。</div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-sm text-slate-500">未选择策略。</div>
              )}
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="records">
          <Panel title="记录列表" description="先看当前策略的记录列表，再下钻到记录详情页。">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <Tabs value={recordFilter} onValueChange={(value) => setRecordFilter(value as RecordFilter)}>
                <TabsList className="h-10">
                  <TabsTrigger value="all" className="min-w-[72px]">
                    全部 {currentStrategyRecords.length}
                  </TabsTrigger>
                  <TabsTrigger value="signal" className="min-w-[72px]">
                    信号 {currentStrategyRecords.filter((item) => item.kind === 'signal').length}
                  </TabsTrigger>
                  <TabsTrigger value="order" className="min-w-[72px]">
                    订单 {currentStrategyRecords.filter((item) => item.kind === 'order').length}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <div className="text-xs text-slate-500">当前筛选 {filteredCurrentStrategyRecords.length} 条</div>
            </div>
            <div className="space-y-3">
              {filteredCurrentStrategyRecords.length ? (
                <RecordTable strategyId={effectiveStrategyId ?? ''} records={filteredCurrentStrategyRecords} />
              ) : (
                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                  当前策略在所选筛选下没有记录。
                </div>
              )}
            </div>
          </Panel>
        </TabsContent>

        <TabsContent value="config">
          <Panel
            title="策略配置"
            description={draftStrategy ? '当前是新增草稿。保存后会作为新策略写入，不影响已有策略。' : '这里只编辑当前策略。'}
          >
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" onClick={createStrategyDraft}>
                  新建策略
                </Button>
                <Button type="button" size="sm" variant="outline" onClick={duplicateCurrentStrategy}>
                  复制当前
                </Button>
                {selectedStrategyOption ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => void toggleStrategyEnabled()}>
                    {selectedStrategyOption.enabled ? '停用当前策略' : '启用当前策略'}
                  </Button>
                ) : null}
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-950">基础信息</div>
                <div className="mt-1 text-xs text-slate-500">先确认策略身份、标的和运行模式。</div>
                <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
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
                  <ConfigField label="市场">
                    <Input {...form.register('market')} />
                  </ConfigField>
                  <ConfigField label="观察窗口">
                    <Input type="number" {...form.register('history_window', { valueAsNumber: true })} />
                  </ConfigField>
                </div>
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-950">仓位与风控</div>
                <div className="mt-1 text-xs text-slate-500">这组参数决定下单规模和风险边界。</div>
                <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <ConfigField label="下单数量">
                    <Input type="number" {...form.register('quantity', { valueAsNumber: true })} />
                  </ConfigField>
                  <ConfigField label="持仓数量">
                    <Input type="number" {...form.register('position_quantity', { valueAsNumber: true })} />
                  </ConfigField>
                  <ConfigField label="最大仓位">
                    <Input type="number" {...form.register('max_position', { valueAsNumber: true })} />
                  </ConfigField>
                  <ConfigField label="止损比例">
                    <Input type="number" step="0.001" {...form.register('stop_loss_pct', { valueAsNumber: true })} />
                  </ConfigField>
                  <ConfigField label="止盈比例">
                    <Input type="number" step="0.001" {...form.register('take_profit_pct', { valueAsNumber: true })} />
                  </ConfigField>
                </div>
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-950">高级策略参数</div>
                <div className="mt-1 text-xs text-slate-500">只在需要微调行为时修改，平时可以保持默认。</div>
                <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <ConfigField label="突破回看">
                    <Input type="number" {...form.register('breakout_lookback', { valueAsNumber: true })} />
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
              </div>

              <StrategyBooleanToggles
                formWatch={(name) => form.watch(name) as boolean}
                formSetValue={(name, value) => form.setValue(name, value as never)}
              />

              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={saveConfig.isPending}>
                  <Save className="h-4 w-4" />
                  {saveConfig.isPending ? '保存中...' : draftStrategy ? '保存为新策略' : '保存配置'}
                </Button>
                <Button type="button" variant="outline" onClick={() => form.reset(defaults)}>
                  重置
                </Button>
                {draftStrategy ? (
                  <Button type="button" variant="outline" onClick={() => setDraftStrategy(null)}>
                    取消草稿
                  </Button>
                ) : null}
              </div>
            </form>
          </Panel>
        </TabsContent>
      </Tabs>

      {actionError ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{actionError}</div>
      ) : null}
    </div>
  )
}
