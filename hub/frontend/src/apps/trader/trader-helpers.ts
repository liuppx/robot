export type RecordItem = {
  id: string
  kind: 'signal' | 'order'
  title: string
  summary: string
  timestamp: string
  payload: Record<string, unknown>
}

export type StrategySnapshot = {
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

export type StrategyOption = {
  id: string
  name: string
  symbol: string
  enabled: boolean
  lastAction: string
  observedAt: string | null
  positionQuantity: number
  payload: Record<string, unknown>
}

export type TraderConfigForm = {
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

export type RecordFilter = 'all' | 'signal' | 'order'

export type RecordDetailField = {
  label: string
  value: string
}

function parseTimestamp(value: string): Date | null {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '-') {
    return null
  }

  const direct = new Date(trimmed)
  if (!Number.isNaN(direct.getTime())) {
    return direct
  }

  const normalized = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')
  const localDate = new Date(normalized)
  if (!Number.isNaN(localDate.getTime())) {
    return localDate
  }

  return null
}

export function formatBrowserDateTime(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  const parsed = parseTimestamp(value)
  if (!parsed) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

export function displayPath(path: string | undefined) {
  if (!path) {
    return '-'
  }
  const parts = path.split('/').filter(Boolean)
  if (parts.length <= 3) {
    return path
  }
  return `.../${parts.slice(-3).join('/')}`
}

export function pickText(record: Record<string, unknown>, keys: string[]) {
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

export function asNumber(value: unknown, fallback: number) {
  return typeof value === 'number' ? value : fallback
}

export function asBoolean(value: unknown, fallback: boolean) {
  return typeof value === 'boolean' ? value : fallback
}

export function buildRecordItems(summary: { recent_signals?: Array<Record<string, unknown>>; recent_orders?: Array<Record<string, unknown>> } | undefined): RecordItem[] {
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

export function buildStrategySnapshots(summary: { strategy_snapshots?: Array<Record<string, unknown>> } | undefined): StrategySnapshot[] {
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

export function buildStrategyOptions(
  summary: { strategies?: Array<Record<string, unknown>> } | undefined,
  strategySnapshots: StrategySnapshot[],
): StrategyOption[] {
  return ((summary?.strategies ?? []) as Array<Record<string, unknown>>).map((item, index) => {
    const id = pickText(item, ['id']) === '-' ? `strategy-${index}` : pickText(item, ['id'])
    const snapshot = strategySnapshots.find((entry) => entry.id === id) ?? null
    return {
      id,
      name: pickText(item, ['name']) === '-' ? `策略 ${index + 1}` : pickText(item, ['name']),
      symbol: pickText(item, ['symbol']),
      enabled: asBoolean(item.enabled, true),
      lastAction: snapshot?.lastAction ?? '-',
      observedAt: snapshot?.observedAt ?? null,
      positionQuantity: snapshot?.positionQuantity ?? 0,
      payload: item,
    }
  })
}

export function buildConfigDefaults(
  summary: { broker?: string; strategies?: Array<Record<string, unknown>> } | undefined,
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

export function buildNewStrategyDefaults(broker: string): TraderConfigForm {
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

export function buildCopiedStrategyDefaults(source: TraderConfigForm): TraderConfigForm {
  const nextId = source.id ? `${source.id}-copy` : ''
  return {
    ...source,
    id: nextId,
    name: source.name ? `${source.name} 副本` : '',
    enabled: false,
  }
}

export function buildRecordDetailFields(record: RecordItem): RecordDetailField[] {
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

export function pickRecordStrategyId(record: RecordItem | null): string | null {
  if (!record) {
    return null
  }
  const strategyId = pickText(record.payload, ['strategyId', 'strategy_id', 'strategy'])
  return strategyId === '-' ? null : strategyId
}

export function buildRecordContextFields(record: RecordItem): RecordDetailField[] {
  const payload = record.payload
  const ignoredKeys = new Set([
    'strategyId', 'strategy_id', 'strategy', 'action', 'signal', 'decision', 'side', 'status', 'result', 'symbol',
    'code', 'name', 'price', 'lastPrice', 'latestPrice', 'quantity', 'qty', 'filledQuantity', 'broker', 'account',
    'accountId', 'orderId', 'order_id', 'id', 'ts', 'timestamp', 'created_at', 'time', 'reason', 'message', 'note',
  ])

  return Object.entries(payload)
    .filter(([key]) => !ignoredKeys.has(key))
    .slice(0, 8)
    .map(([key, value]) => ({
      label: key,
      value: typeof value === 'string' ? value : JSON.stringify(value),
    }))
}

export function normalizeStrategyPayload(strategy: Record<string, unknown>, enabled: boolean) {
  return {
    ...strategy,
    id: pickText(strategy, ['id']) === '-' ? '' : pickText(strategy, ['id']),
    enabled,
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

export function buildRecentStrategyRecords(records: RecordItem[], strategyId: string | null) {
  if (!strategyId) {
    return []
  }
  return records.filter((record) => pickText(record.payload, ['strategyId', 'strategy_id', 'strategy']) === strategyId).slice(0, 4)
}

export function filterRecordsByStrategy(records: RecordItem[], strategyId: string | null) {
  if (!strategyId) {
    return []
  }
  return records.filter((record) => pickText(record.payload, ['strategyId', 'strategy_id', 'strategy']) === strategyId)
}
