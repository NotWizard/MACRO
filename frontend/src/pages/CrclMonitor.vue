<script setup lang="ts">
// CRCL 监控 — 储备金主业与金融服务转型；估值和组合告警以公司整体为口径。
//
// 刷新状态住在 stores/refresh（FE-H4）：本页曾自持 refreshing/progress + 裸 fetch
// SSE，导致顶栏 🔄 与日期预设在本页是死控件、离开页面时 reader 循环继续写已销毁
// 组件的 ref、返回本页还能再启动一次并发采集。现在统一走 refresh.stream(false,'crcl')，
// 顶栏按 route.meta.refreshKind 呈现，本页只读状态。
import { ref, markRaw, computed, watch } from 'vue'
import EChart from '@/components/charts/EChart.vue'
import GraphCard from '@/components/layout/GraphCard.vue'
import MetricTile from '@/components/layout/MetricTile.vue'
import PageState from '@/components/layout/PageState.vue'
import { buildMultiLine } from '@/components/charts/options'
import { applyTheme, baseAxis, chartTheme } from '@/design/echarts.theme'
import { api } from '@/api/client'
import { useAsyncData } from '@/composables/useAsyncData'
import { themedOption } from '@/composables/useThemedOption'
import { useRefreshStore } from '@/stores/refresh'
import type { CrclMetric, CrclPoint } from '@/api/types'

const refresh = useRefreshStore()
const logsOpen = ref(false)
const eventsExpanded = ref(false)

const METRIC_KEYS = 'crcl_close,usdc_circ,stablecoin_total,treasury_3m,treasury_6m,treasury_1y,arc_tvl,arc_stablecoins,arc_dex_volume'

// ---------- data loading ----------
const { state, data, error, errorText, loading, retry: load } = useAsyncData(async (signal) => {
  const [ov, mt, ev, al, lg, fu] = await Promise.all([
    api.getCrclOverview({ signal }),
    api.getCrclMetrics(METRIC_KEYS, { signal }),
    api.getCrclEvents({ signal }),
    api.getCrclAlerts({ signal }),
    api.getCrclLogs(60, { signal }),
    api.getCrclFundamentals({ signal }),
  ])
  return {
    overview: ov,
    metrics: markRaw(mt.metrics),
    events: ev.events,
    eventsUpdatedAt: ev.updated_at,
    rules: al.rules,
    logs: lg.logs,
    fundamentals: fu,
  }
}, { watch: [() => refresh.crclRefreshedAt] })   // 采集完成 → 自动重取

// 直接打开书签时，浏览器的第一次锚点定位可能早于异步渲染。
watch(data, () => {
  const id = location.hash.slice(1)
  if (['crcl-curves', 'crcl-reserve', 'crcl-transition'].includes(id)) {
    document.getElementById(id)?.scrollIntoView()
  }
}, { flush: 'post', once: true })

const overview = computed(() => data.value?.overview ?? null)
const metrics = computed<Record<string, CrclMetric>>(() => data.value?.metrics ?? {})
const events = computed(() => data.value?.events ?? [])
const reserveEvents = computed(() => events.value.filter((event) => event.category === '宏观' || event.category === '财报').slice().reverse())
const transitionEvents = computed(() => events.value.filter((event) => event.category !== '宏观' && event.category !== '财报'))
const pastEvents = computed(() => transitionEvents.value.filter((event) => event.status !== '计划').slice().reverse())
const upcomingEvents = computed(() => transitionEvents.value.filter((event) => event.status === '计划'))
const eventGroups = computed(() => [
  { title: '最近进展', items: eventsExpanded.value ? pastEvents.value : pastEvents.value.slice(0, 5) },
  { title: '未来节点', items: upcomingEvents.value },
].filter((group) => group.items.length))
const eventsUpdatedAt = computed(() => data.value?.eventsUpdatedAt ?? null)
const rules = computed(() => data.value?.rules ?? [])
const logs = computed(() => data.value?.logs ?? [])
const fundamentals = computed(() => data.value?.fundamentals ?? null)

// 采集状态只读自 store：跨路由持久，回到本页不会再启一次并发采集
const collecting = computed(() => refresh.running && refresh.kind === 'crcl')
// 后端可达但库里还没有任何采集结果（≠ 后端不可达）
const isEmpty = computed(() =>
  !overview.value?.last_run && !fundamentals.value?.quarters?.length && !events.value.length &&
  Object.values(metrics.value).every((m) => !m.points?.length))

// ---------- formatting ----------
const fmtNum = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toFixed(digits)
const fmtTs = (ts: string | null | undefined) =>
  ts ? ts.replace('T', ' ').replace('Z', '') : '—'

// ---------- 同比 / 环比 delta ----------
interface TileDelta { text: string; dir?: 'up' | 'down' | 'flat' }

/** 序列最新值相对 ~daysBack 天前的变化 %；数据不足返回 null。 */
function seriesDelta(pts: CrclPoint[] | undefined, daysBack: number): number | null {
  if (!pts || pts.length < 2) return null
  const last = pts[pts.length - 1]
  const target = new Date(last.date).getTime() - daysBack * 86400000
  // Parse each point once and carry the running best's distance, instead of
  // re-parsing best.date every iteration (this loops the full ~3187-pt series).
  let best = pts[0]
  let bestDiff = Math.abs(new Date(best.date).getTime() - target)
  for (const pt of pts) {
    const diff = Math.abs(new Date(pt.date).getTime() - target)
    if (diff < bestDiff) {
      best = pt
      bestDiff = diff
    }
  }
  if (best.value <= 0) return null
  return (last.value / best.value - 1) * 100
}
const pctChip = (label: string, v: number | null): TileDelta =>
  v == null
    ? { text: `${label} —` }
    : { text: `${label} ${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, dir: v > 0.05 ? 'up' : v < -0.05 ? 'down' : 'flat' }

const priceSeries = computed(() => metrics.value.crcl_close?.points ?? [])
const priceDod = computed(() => {
  const s = priceSeries.value
  if (s.length < 2) return null
  return (s[s.length - 1].value / s[s.length - 2].value - 1) * 100
})
const priceDeltas = computed<TileDelta[]>(() => [
  pctChip('日环比', priceDod.value),
  pctChip('同比', seriesDelta(priceSeries.value, 365)),
])
const usdcDeltas = computed<TileDelta[]>(() => [
  pctChip('同比', seriesDelta(metrics.value.usdc_circ?.points, 365)),
  pctChip('季环比', seriesDelta(metrics.value.usdc_circ?.points, 90)),
])
const totalDeltas = computed<TileDelta[]>(() => [
  pctChip('同比', seriesDelta(metrics.value.stablecoin_total?.points, 365)),
  pctChip('季环比', seriesDelta(metrics.value.stablecoin_total?.points, 90)),
])
const peDeltas = computed<TileDelta[]>(() => [
  { text: `构成：TTM ${fmtNum(valuation.value.trailing_pe, 1)} / 前瞻 ${fmtNum(valuation.value.forward_pe, 1)}` },
])
const prevQ = computed(() => {
  const qs = fundamentals.value?.quarters ?? []
  return qs.length >= 2 ? qs[qs.length - 2] : null
})
const num = (q: Record<string, number | string | null> | null, k: string): number | null => {
  const v = q?.[k]
  return typeof v === 'number' ? v : null
}
// 注意：num() 返回 number|null，必须显式 `!= null` —— 直接当布尔用会把合法的 0
// 当成缺失（0 是有意义的基数/份额值）。
const revenueDeltas = computed<TileDelta[]>(() => [
  pctChip('同比', num(latestQ.value, 'total_revenue_yoy_pct')),
  pctChip('环比', num(latestQ.value, 'total_revenue_qoq_pct') ??
    (num(latestQ.value, 'total_revenue_m') != null && num(prevQ.value, 'total_revenue_m') != null
      ? (num(latestQ.value, 'total_revenue_m')! / num(prevQ.value, 'total_revenue_m')! - 1) * 100
      : null)),
])
function quarterMoney(key: string): number | null {
  const amount = num(latestQ.value, key)
  return amount == null ? null : amount / 100
}
const reserveShare = computed(() => {
  const reserve = quarterMoney('reserve_revenue_m'), total = quarterMoney('total_revenue_m')
  return reserve != null && total != null && total > 0 ? reserve / total * 100 : null
})
const adjustedShare = computed(() => num(latestQ.value, 'adjusted_nonreserve_share_pct'))
const filingBreakdownSource = computed(() => {
  const source = latestQ.value?.revenue_breakdown_source
  return typeof source === 'string' && source.startsWith('https://') ? source : null
})
const subscriptionRevenueWan = computed(() => {
  const amount = num(latestQ.value, 'subscription_services_revenue_m')
  return amount == null ? null : amount * 100
})
const transactionRevenueWan = computed(() => {
  const amount = num(latestQ.value, 'transaction_revenue_m')
  return amount == null ? null : amount * 100
})
const adjustedOtherIncome = computed(() => {
  const other = quarterMoney('other_revenue_m'), oneOff = quarterMoney('nonreserve_one_off_m') ?? 0
  return other != null && (oneOff === 0 || oneOff <= other) ? other - oneOff : null
})
const adjustedShareTip = computed(() => {
  const other = quarterMoney('other_revenue_m'), total = quarterMoney('total_revenue_m')
  const oneOff = quarterMoney('nonreserve_one_off_m') ?? 0
  const remaining = adjustedOtherIncome.value, share = adjustedShare.value
  if (share == null) return '其他收入和总收入（含储备收入）各扣除同一笔已标注的一次性收入，再计算占比。本期暂无可用结果。'

  const money = (amount: number) => amount.toLocaleString('zh-CN', { maximumFractionDigits: 5, useGrouping: false })
  const period = latestQ.value?.period
  const heading = `${period} · 金额单位：亿美元`
  if (oneOff === 0) {
    const inputs = other != null && total != null ? `\n其他收入 ${money(other)} ÷ 总收入（含储备收入）${money(total)}。` : ''
    return `${heading}\n本期未标注一次性收入，不作扣除。${inputs}\n沿用财报披露占比 ${fmtNum(share)}%。`
  }
  if (other == null || total == null || remaining == null) return '本期缺少展示计算过程所需的金额；卡片沿用已有占比。'

  const denominator = money(total - oneOff)
  return [
    heading,
    `分子：其他收入 ${money(other)} − 一次性收入 ${money(oneOff)} = ${money(remaining)}`,
    `分母：总收入（含储备收入）${money(total)} − 同一笔 ${money(oneOff)} = ${denominator}`,
    `占比：${money(remaining)} ÷ ${denominator} × 100% ≈ ${fmtNum(share)}%`,
    '',
    '这笔一次性收入原本计入了其他收入，也包含在总收入里，所以两边都扣除。这不是经常性服务收入占比。',
  ].join('\n')
})
const nonreserveDeltas = computed<TileDelta[]>(() => {
  const share = num(latestQ.value, 'nonreserve_share_pct'), oneOff = quarterMoney('nonreserve_one_off_m')
  return [
    ...(share == null ? [] : [{ text: `披露占比 ${fmtNum(share)}%` }]),
    ...(oneOff != null && oneOff > 0 ? [{ text: `含一次性收入 ${fmtNum(oneOff, 3)} 亿美元` }] : []),
  ]
})
const consensusEPS = computed(() => num(latestQ.value, 'eps_consensus'))
const annualizedCpnTpv = computed(() => {
  const value = num(latestQ.value, 'cpn_tpv_annualized_b')
  return value == null ? null : value * 10
})
const cpnInstDeltas = computed<TileDelta[]>(() => {
  const v = num(latestQ.value, 'cpn_institutions_qoq') ??
    (num(latestQ.value, 'cpn_institutions') != null && num(prevQ.value, 'cpn_institutions') != null
      ? num(latestQ.value, 'cpn_institutions')! - num(prevQ.value, 'cpn_institutions')!
      : null)
  return [v == null ? { text: '环比 —' } : { text: `环比 ${v >= 0 ? '+' : ''}${v.toFixed(0)} 家`, dir: v > 0 ? 'up' : v < 0 ? 'down' : 'flat' }]
})
const arcDisclosure = computed(() => fundamentals.value?.arc)
const secondCurve = computed(() => fundamentals.value?.second_curve)
const arcGas = computed(() => secondCurve.value?.arc_gas?.at(-1))
const arcGasReference = computed(() => {
  const ref = secondCurve.value?.arc_gas_reference
  return ref && !secondCurve.value?.arc_gas?.some(row => row.month === ref.month) ? ref : null
})
const arcRwa = computed(() => secondCurve.value?.arc_rwa)
const b2bPayments = computed(() => secondCurve.value?.b2b_payments)
const institutionEvidence = computed(() => (secondCurve.value?.institutions ?? []).slice().sort((a, b) => b.as_of.localeCompare(a.as_of)))
const arcCards = [
  { key: 'arc_tvl', label: 'DeFi 锁仓量', tip: 'DefiLlama 计入 Arc 上已跟踪 DeFi 协议的锁仓价值，不是全链资产。' },
  { key: 'arc_stablecoins', label: '链上稳定币市值', tip: 'DefiLlama 统计的 Arc 上美元锚定稳定币市值；与锁仓量有交集，不应相加。' },
  { key: 'arc_dex_volume', label: 'DEX 日成交量', tip: '按 UTC 已结束自然日统计的 Arc 去中心化交易所成交额，不等同于全链转账量。' },
]
const arcPoint = (key: string) => metrics.value[key]?.points.at(-1)
function arcValue(key: string) {
  const p = arcPoint(key)
  return p ? p.value / 1e8 : null
}
const eurcDeltas = computed<TileDelta[]>(() => [
  pctChip('环比', num(latestQ.value, 'eurc_circ_qoq_pct') ??
    (num(latestQ.value, 'eurc_circ_m') != null && num(prevQ.value, 'eurc_circ_m') != null
      ? (num(latestQ.value, 'eurc_circ_m')! / num(prevQ.value, 'eurc_circ_m')! - 1) * 100
      : null)),
])

// ---------- KPI ----------
const valuation = computed(() => (overview.value?.snapshots?.valuation ?? {}) as Record<string, number | null>)
const stableSnap = computed(() => (overview.value?.snapshots?.stablecoins ?? {}) as Record<string, number | string | null>)
const peSpread = computed(() => {
  const t = valuation.value.trailing_pe, f = valuation.value.forward_pe
  return t != null && f != null ? f - t : null
})
const triggeredRules = computed(() => overview.value?.alert_summary.triggered ?? [])

// ---------- chart options ----------
function lineOption(m: CrclMetric | undefined, opts: { area?: boolean; unit?: string; divisor?: number; markLineY?: number } = {}) {
  if (!m?.points.length) return null
  const dates = m.points.map((p) => p.date)
  const div = opts.divisor ?? 1
  const vals = m.points.map((p) => +(p.value / div).toFixed(4))
  return markRaw(applyTheme({
    xAxis: { type: 'category', data: dates, ...baseAxis({ boundaryGap: false, splitLine: { show: false } }) },
    yAxis: {
      type: 'value', ...baseAxis({ name: opts.unit ?? m.unit, scale: true }),
    },
    series: [{
      name: `${m.label} (${opts.unit ?? m.unit})`, type: 'line', symbol: 'none', smooth: false,
      data: vals,
      itemStyle: { color: themeColors().accent },
      lineStyle: { color: themeColors().accent, width: 2 },
      ...(opts.area ? { areaStyle: { opacity: 0.1 } } : {}),
      ...(opts.markLineY != null ? {
        markLine: {
          silent: true, symbol: 'none',
          data: [{ yAxis: opts.markLineY }],
          lineStyle: { color: themeColors().warn, type: 'dashed', width: 1.5 },
          label: { formatter: String(opts.markLineY), color: themeColors().warn, fontSize: 10 },
        },
      } : {}),
    }],
  }))
}

const priceOpt = themedOption(() => lineOption(metrics.value.crcl_close, { unit: '美元' }))
const usdcOpt = themedOption(() => lineOption(metrics.value.usdc_circ, { unit: '亿美元', divisor: 1e8 }))
const totalOpt = themedOption(() => lineOption(metrics.value.stablecoin_total, { unit: '亿美元', divisor: 1e8 }))
const arcSizeOpt = themedOption(() => {
  const byDate = new Map<string, Record<string, string | number>>()
  for (const key of ['arc_tvl', 'arc_stablecoins']) {
    for (const p of metrics.value[key]?.points ?? []) {
      const row = byDate.get(p.date) ?? { date: p.date }
      row[key] = p.value / 1e8
      byDate.set(p.date, row)
    }
  }
  const rows = [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, row]) => row)
  return rows.length ? buildMultiLine(rows, [
    { col: 'arc_tvl', name: 'DeFi 锁仓量' },
    { col: 'arc_stablecoins', name: '链上稳定币市值' },
  ], '亿美元') : null
})
const arcDexOpt = themedOption(() => lineOption(metrics.value.arc_dex_volume, { area: true, unit: '亿美元', divisor: 1e8 }))
const reserveRevenueOpt = themedOption(() => {
  const points = (fundamentals.value?.quarters ?? []).flatMap(q => {
    const value = num(q, 'reserve_revenue_m')
    return value == null ? [] : [{ date: String(q.period), value: value / 100 }]
  })
  if (!points.length) return null
  return markRaw(applyTheme({
    ...(points.length <= 8 ? { dataZoom: [], grid: { left: 42, right: 18, top: 32, bottom: 28 } } : {}),
    xAxis: { type: 'category', data: points.map(p => p.date), ...baseAxis({ splitLine: { show: false } }) },
    yAxis: { type: 'value', ...baseAxis({ name: '亿美元' }) },
    series: [{ type: 'bar', name: '储备收入（亿美元）', data: points.map(p => p.value), barMaxWidth: 46,
      itemStyle: { color: themeColors().accent } }],
  }))
})
// 6M / 1Y 曲线配色（3M 用 accent）——treasuryOpt 内引用，声明须在其之前（否则一旦
// 该 computed 变为 eager 求值就会命中 TDZ ReferenceError）。
// 主题化：1 号色 = accent，2 号色 = warn（暗亮两套自动跟换）
const themeColors = () => chartTheme().colors
const treasuryOpt = themedOption(() => {
  const m3 = metrics.value.treasury_3m, m6 = metrics.value.treasury_6m, m12 = metrics.value.treasury_1y
  if (!m3 || !m6 || !m12) return null
  const dates = m3.points.map((p) => p.date)
  const byDate = (m: CrclMetric) => new Map(m.points.map((p) => [p.date, p.value]))
  const v6 = byDate(m6), v12 = byDate(m12)
  const mk = (name: string, data: Array<number | null>, color: string) => ({
    name: `${name} (%)`, type: 'line', symbol: 'none', data,
    itemStyle: { color }, lineStyle: { color, width: 1.8 },
  })
  return markRaw(applyTheme({
    legend: { show: true },
    xAxis: { type: 'category', data: dates, ...baseAxis({ boundaryGap: false }) },
    yAxis: { type: 'value', ...baseAxis({ name: '%', scale: true }) },
    series: [
      mk('3M', m3.points.map((p) => p.value), themeColors().accent),
      mk('6M', dates.map((d) => v6.get(d) ?? null), themeColors().warn),
      mk('1Y', dates.map((d) => v12.get(d) ?? null), themeColors().up),
    ],
  }))
})

// ---------- alerts ----------
const LEVEL_STYLE: Record<string, { ring: string; badge: string; label: string }> = {
  yellow: { ring: 'border-warn', badge: 'bg-amber-500/15 text-warn', label: '黄色警报' },
  red: { ring: 'border-down', badge: 'bg-red-500/15 text-down', label: '红色警报' },
  confirm: { ring: 'border-up', badge: 'bg-up-soft text-up', label: '确认信号' },
}
const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  triggered: { text: '已触发', cls: 'bg-red-500/20 text-red-300' },
  ok: { text: '正常', cls: 'bg-up-soft text-up' },
  insufficient_data: { text: '数据不足', cls: 'bg-slate-500/20 text-slate-400' },
  not_evaluated: { text: '未评估', cls: 'bg-slate-500/20 text-slate-400' },
}
const CATEGORY_CLS: Record<string, string> = {
  '财报': 'bg-indigo-500/15 text-indigo-300',
  '监管': 'bg-amber-500/15 text-warn',
  '宏观': 'bg-cyan-500/15 text-cyan-300',
  '里程碑': 'bg-up-soft text-up',
  '合作': 'bg-purple-500/15 text-purple-300',
  '检查点': 'bg-red-500/15 text-red-300',
}
const EVENT_STATUS_CLS: Record<string, string> = {
  '已发生': 'text-text-4', '进行中': 'text-info', '待观察': 'text-warn',
  '待验证': 'text-emerald-400', '计划': 'text-text-3',
}

const latestQ = computed(() => {
  const qs = fundamentals.value?.quarters ?? []
  return qs.length ? qs[qs.length - 1] : null
})
</script>

<template>
  <div class="p-4 sm:p-6">
    <!-- header -->
    <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-5">
      <div class="min-w-0">
        <h1 class="text-xl font-bold text-text">CRCL 监控体系</h1>
        <p class="text-xs text-text-3 mt-1 leading-relaxed">
          储备金主业靠什么赚钱？金融服务新业务有没有形成持续收入？
        </p>
      </div>
      <!-- 采集入口只有一个：顶栏「🔄 采集 CRCL」（route.meta.refreshKind='crcl'）。
           这里只做只读状态显示，不再是第二个语义不同的刷新按钮。 -->
      <div class="sm:shrink-0 sm:text-right" role="status" aria-live="polite">
        <div class="text-xs text-text-3">
          {{ collecting ? `采集中 ${(refresh.progress * 100).toFixed(0)}%` : `最近采集（UTC） ${overview?.last_run ? fmtTs(overview.last_run.ts) : '—'}` }}
        </div>
        <div v-if="collecting" class="w-28 h-1 bg-border rounded mt-1.5 overflow-hidden sm:ml-auto">
          <div class="h-full bg-accent transition-all" :style="{ width: `${refresh.progress * 100}%` }" />
        </div>
      </div>
    </div>

    <PageState
      :state="state"
      :error="error"
      :has-data="data != null"
      :empty="isEmpty"
      empty-title="CRCL 库暂无采集记录"
      empty-hint="后端已连接，但 crcl_monitor.db 里还没有指标。点击顶栏「🔄 采集 CRCL」执行一次采集。"
      min-height="320px"
      @retry="load"
    >
      <p v-if="fundamentals?.error" role="alert" class="mb-4 rounded-xl border border-border bg-card p-3 text-sm text-down">
        Circle 财报或第二曲线资料暂不可用：{{ fundamentals.error }}
      </p>
      <nav id="crcl-curves" tabindex="-1" aria-label="Circle 业务曲线" class="grid gap-2 sm:grid-cols-2 mb-6 scroll-mt-20">
        <a href="#crcl-reserve" class="flex items-center justify-between gap-3 rounded-xl border border-border bg-card p-3.5 text-sm font-semibold hover:bg-card-hover hover:border-accent focus-visible:outline-2 focus-visible:outline-accent">
          <span><span class="text-accent mr-2">第一曲线</span>储备金主业</span>
          <svg aria-hidden="true" class="size-4 shrink-0 text-text-3" viewBox="0 0 16 16" fill="none"><path d="M8 3v10m-4-4 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </a>
        <a href="#crcl-transition" class="flex items-center justify-between gap-3 rounded-xl border border-border bg-card p-3.5 text-sm font-semibold hover:bg-card-hover hover:border-accent focus-visible:outline-2 focus-visible:outline-accent">
          <span><span class="text-accent mr-2">第二曲线</span>金融服务转型</span>
          <svg aria-hidden="true" class="size-4 shrink-0 text-text-3" viewBox="0 0 16 16" fill="none"><path d="M8 3v10m-4-4 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </a>
      </nav>

      <section id="crcl-reserve" tabindex="-1" aria-labelledby="crcl-reserve-title" class="scroll-mt-20 mb-8 space-y-5">
        <header class="border-b border-border pb-3">
          <h2 id="crcl-reserve-title" class="text-lg font-bold text-text">第一曲线 · 储备金主业</h2>
          <p class="mt-1 text-sm text-text-3">看稳定币流通规模、储备收益率与分发成本如何支撑当期收入。</p>
        </header>
        <div class="space-y-4">
          <section aria-labelledby="crcl-income">
            <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
              <h3 id="crcl-income" class="text-sm font-semibold text-text">储备收入与规模</h3>
              <span v-if="latestQ" class="text-xs text-text-3">财报 · {{ latestQ.period }}</span>
            </div>
            <div class="grid grid-cols-1 min-[460px]:grid-cols-2 xl:grid-cols-4 gap-3">
              <MetricTile label="季度储备收入" :value="quarterMoney('reserve_revenue_m')" suffix="亿美元" :digits="2" accent
                :deltas="[pctChip('同比', num(latestQ, 'reserve_revenue_yoy_pct')), { text: `占总收入 ${reserveShare == null ? '—' : `${reserveShare.toFixed(1)}%`}` }]"
                tip="财报单季 Reserve Income，未扣分发与交易成本。" />
              <MetricTile label="USDC 最新流通量" :deltas="usdcDeltas" :value="stableSnap.usdc_circ != null ? Number(stableSnap.usdc_circ) / 1e8 : null" suffix="亿美元" :digits="1"
                :period="metrics.usdc_circ?.points.at(-1)?.date" tip="DefiLlama 最新全链日值，非财报季末余额或季度日均。" />
              <MetricTile label="季度平均 USDC 流通量" :value="num(latestQ, 'usdc_circ_avg_b') == null ? null : num(latestQ, 'usdc_circ_avg_b')! * 10" suffix="亿美元" :digits="1"
                tip="使用公司披露的单季日均，不用期末或最新一日的数值替代。" />
              <MetricTile label="季度储备收益率" :value="num(latestQ, 'reserve_return_rate_pct')" suffix="%" :digits="1"
                tip="Circle 财报披露的储备收益率；不同于美债报价，也不是扣除分发费用后的净收益率。" />
            </div>
          </section>

          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <GraphCard title="季度储备收入" height="240px" tip="每期取 Circle 财报单季实绩，单位亿美元。">
              <EChart v-if="reserveRevenueOpt" :option="reserveRevenueOpt" height="240px" />
              <p v-else class="py-4 text-sm text-text-3">暂无已核实的财报数据。</p>
            </GraphCard>
            <GraphCard title="USDC 流通量" height="240px" :loading="loading" :error="errorText" @retry="load"
              tip="DefiLlama 全链日频序列。流通量同比是告警规则 y_usdc_growth 的输入（阈值 15%）。">
              <EChart v-if="usdcOpt" :option="usdcOpt" height="240px" />
            </GraphCard>
          </div>

          <section aria-labelledby="crcl-valuation">
            <h3 id="crcl-valuation" class="text-sm font-semibold text-text mb-2">公司整体 · 估值、盈利与成本</h3>
            <div class="grid grid-cols-1 min-[460px]:grid-cols-2 xl:grid-cols-3 gap-3">
              <MetricTile label="CRCL 收盘价" :deltas="priceDeltas" :value="priceSeries.at(-1)?.value ?? null" suffix="美元" :digits="2"
                :period="priceSeries.at(-1)?.date" tip="日环比指较上一个交易日。" />
              <MetricTile label="市值" :value="valuation.market_cap != null ? valuation.market_cap / 1e8 : null" suffix="亿美元" :digits="1"
                tip="Yahoo Finance 市值快照，不一定与最近收盘价同一时点。" />
              <MetricTile label="P/E（前瞻 − TTM）" :deltas="peDeltas" :value="peSpread" suffix="倍" :digits="1"
                tip="Yahoo Finance 前瞻与过去 12 个月市盈率之差。一次性项目会影响 TTM，差值不单独构成盈利下滑的证据。" />
              <MetricTile label="稀释 EPS" :value="num(latestQ, 'eps_actual')" :digits="2" suffix="美元"
                :period="latestQ?.period == null ? null : String(latestQ.period)"
                :deltas="[{ text: consensusEPS == null ? '一致预期未核实' : `一致预期 ${consensusEPS.toFixed(2)} 美元` }]"
                tip="公司整体 GAAP 稀释每股收益，不是储备金业务单独 EPS。" />
              <MetricTile label="季度总收入" :deltas="revenueDeltas" :value="quarterMoney('total_revenue_m')" suffix="亿美元" :digits="2"
                :period="latestQ?.period == null ? null : String(latestQ.period)"
                :tip="`含储备收入。\n\n来源：${latestQ?.source ?? '—'}`" />
              <MetricTile label="分发与交易成本率" :value="num(latestQ, 'distribution_cost_ratio_pct')" suffix="%" :digits="2"
                tip="季报中的分发、交易及其他成本 ÷ 总收入；不等同于支付给单个渠道的分成。" />
            </div>
          </section>
          <section aria-labelledby="crcl-stablecoin-context">
            <h3 id="crcl-stablecoin-context" class="text-sm font-semibold text-text mb-2">流通规模对照</h3>
            <div class="grid grid-cols-1 min-[460px]:grid-cols-2 gap-3">
              <MetricTile label="EURC 流通量" :value="stableSnap.eurc_circ != null ? Number(stableSnap.eurc_circ) / 1e8 : null" suffix="亿欧元" :digits="2"
                :deltas="eurcDeltas" tip="卡片为 DefiLlama 最新流通量；环比比较来自最近两期可比财报。" />
              <MetricTile label="稳定币总市值" :deltas="totalDeltas" :value="stableSnap.stablecoin_total != null ? Number(stableSnap.stablecoin_total) / 1e8 : null" suffix="亿美元" :digits="0"
                tip="全链美元锚定稳定币市值，作为行业对照，不是 Circle 收入。" />
            </div>
          </section>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 min-[1440px]:grid-cols-3 gap-4 mt-4">
          <GraphCard title="CRCL 收盘价" height="280px" :loading="loading" :error="errorText" @retry="load"
            :tip="`IPO 以来日线收盘价。\n\n取数：AKShare stock_us_daily('CRCL')（主源）→ yfinance history（备用源），日频。`">
            <EChart v-if="priceOpt" :option="priceOpt" height="280px" />
          </GraphCard>
          <GraphCard title="短端美债收益率（储备收入）" height="280px" :loading="loading" :error="errorText" @retry="load"
            tip="短端收益率影响储备利息。实际影响还取决于 USDC 流通量、资产组合和分发成本。来源：美国财政部。">
            <EChart v-if="treasuryOpt" :option="treasuryOpt" height="280px" />
          </GraphCard>
          <GraphCard title="稳定币总市值（行业水位）" height="280px" :loading="loading" :error="errorText" @retry="load"
            class="lg:col-span-2 min-[1440px]:col-span-1"
            tip="全链美元锚定稳定币总市值；包含 USDC，不能与 USDC 流通量相加。来源：DefiLlama，日频。">
            <EChart v-if="totalOpt" :option="totalOpt" height="280px" />
          </GraphCard>
        </div>
        <details v-if="reserveEvents.length" class="rounded-xl border border-border bg-card px-4">
          <summary class="py-3 text-sm font-medium text-text-2 cursor-pointer">财报与利率事件 · {{ reserveEvents.length }} 条</summary>
          <ul class="divide-y divide-border pb-2 text-sm">
            <li v-for="ev in reserveEvents" :key="`${ev.date}:${ev.title}`" class="py-2">
              <div class="flex flex-wrap gap-x-3 gap-y-1"><time class="text-text-3 tabular-nums" :datetime="ev.date">{{ ev.date }}</time><span class="font-medium text-text">{{ ev.title }}</span><span class="text-text-3">{{ ev.status }}</span></div>
              <p class="mt-1 text-xs leading-relaxed text-text-3">{{ ev.detail }}</p>
              <a v-if="ev.source.startsWith('https://')" :href="ev.source" target="_blank" rel="noopener noreferrer" class="text-xs text-accent underline">来源原文 ↗</a>
              <p v-else-if="ev.source" class="mt-1 text-xs text-text-3">来源：{{ ev.source }}</p>
            </li>
          </ul>
        </details>
      </section>

      <section id="crcl-transition" tabindex="-1" aria-labelledby="crcl-transition-title" class="scroll-mt-20 mb-8">
        <header class="flex flex-wrap items-start justify-between gap-x-3 border-b border-border pb-3 mb-5">
          <div>
            <h2 id="crcl-transition-title" class="text-lg font-bold text-text">第二曲线 · 金融服务转型</h2>
            <p class="mt-1 text-sm text-text-3">先看收入兑现，再看支付量、链上资产和机构有没有持续使用。</p>
          </div>
          <a href="#crcl-curves" class="inline-flex min-h-11 items-center text-xs text-accent underline underline-offset-2">返回业务目录</a>
        </header>
        <section aria-labelledby="crcl-monetization" class="mb-6">
          <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
            <h3 id="crcl-monetization" class="text-sm font-semibold text-text">收入兑现</h3>
            <span v-if="latestQ" class="text-xs text-text-3">财报 · {{ latestQ.period }}</span>
          </div>
          <div class="grid grid-cols-1 min-[460px]:grid-cols-2 xl:grid-cols-4 gap-3">
            <MetricTile label="其他收入（披露）" :value="quarterMoney('other_revenue_m')" suffix="亿美元" :digits="2"
              :deltas="nonreserveDeltas" tip="Circle 财报直接列出的其他收入，不含储备收入，可能包含代币预售等一次性收入。卡片上的披露占比以财报原始总收入为分母。" />
            <MetricTile label="其他收入（剔除已知一次性）" :value="adjustedOtherIncome" suffix="亿美元" :digits="3" accent
              tip="从财报的其他收入里扣掉已确认并标注的一次性项目。剩下的钱仍可能包含不能重复的收入，不等于经常性服务收入，也不是利润。" />
            <MetricTile label="剔除已知一次性后的非储备收入占比" :value="adjustedShare" suffix="%" :digits="2"
              :tip="adjustedShareTip" />
            <MetricTile label="已披露经常性服务收入" :value="quarterMoney('recurring_service_revenue_m')" suffix="亿美元" :digits="3"
              :deltas="quarterMoney('recurring_service_revenue_m') == null ? [{ text: '公司未单独披露' }] : []"
              tip="只显示 Circle 当期单独披露的经常性服务收入。显示「—」是没有单独披露，不是零；不能用扣掉一次性项目后的其他收入代填。" />
          </div>
          <div class="mt-4 mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h4 class="text-xs font-semibold text-text-2">财报附注 · 其他收入明细</h4>
            <a v-if="filingBreakdownSource" :href="filingBreakdownSource" target="_blank" rel="noopener noreferrer"
              class="text-xs text-accent underline underline-offset-2">其他收入拆分原表 ↗</a>
          </div>
          <div class="grid grid-cols-1 min-[460px]:grid-cols-2 gap-3">
            <MetricTile label="订阅与服务收入" :value="subscriptionRevenueWan" suffix="万美元" :digits="1"
              :deltas="subscriptionRevenueWan == null ? [{ text: '暂无当季附注数据' }] : []"
              tip="Circle 财报附注的单季科目。除订阅、维护等收入，还可包含交付时确认的集成收入，不能当作经常性服务收入。" />
            <MetricTile label="交易收入" :value="transactionRevenueWan" suffix="万美元" :digits="1"
              :deltas="transactionRevenueWan == null ? [{ text: '暂无当季附注数据' }] : []"
              tip="Circle 财报附注的单季科目，包括赎回、跨链等活动产生的收入，不是 CPN 单独披露的手续费。" />
          </div>
        </section>

        <section aria-labelledby="crcl-payments" class="mb-6">
        <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h3 id="crcl-payments" class="text-sm font-semibold text-text">机构支付与复用</h3>
          <span v-if="latestQ" class="text-xs text-text-3">{{ latestQ.period }} 披露</span>
        </div>
        <div class="grid grid-cols-1 min-[460px]:grid-cols-2 xl:grid-cols-4 gap-3 mb-3">
          <MetricTile label="CPN 年化支付量" :value="annualizedCpnTpv" suffix="亿美元" :digits="0"
            :deltas="[{ text: '季末前 30 天年化' }]"
            tip="将季末前 30 天 CPN 支付量折算为年率，不是本季实际支付量，也不代表全部 USDC 的链上转账量。" />
          <MetricTile label="CPN 累计登记机构" :deltas="cpnInstDeltas" :value="num(latestQ, 'cpn_institutions')" suffix="家" :digits="0"
            tip="财报披露的累计登记机构数，不等于有生产交易或已付费的机构数。" />
          <MetricTile label="CPN 季度实际支付量" :value="num(latestQ, 'cpn_quarter_tpv_b') == null ? null : num(latestQ, 'cpn_quarter_tpv_b')! * 10" suffix="亿美元" :digits="2"
            :deltas="num(latestQ, 'cpn_quarter_tpv_b') == null ? [{ text: '未披露整季实绩' }] : []"
            tip="同一完整财季内的实际支付量。不能用季末 30 天年化数字倒推。" />
          <MetricTile label="Circle Mint 活跃企业（90 天）" :value="num(latestQ, 'mint_active_enterprises_90d')" suffix="家" :digits="0"
            :deltas="num(latestQ, 'mint_active_enterprises_90d') == null ? [{ text: '无可核实的披露' }] : []"
            tip="去重的企业主体，有实际生产活动；不用钱包地址数、报名数或合作公告估算。" />
        </div>
        <p v-if="b2bPayments" class="mb-3 text-xs text-text-3">
          B2B 贸易与商户结算占已分类样本 {{ fmtNum(b2bPayments.verified_b2b_m / b2bPayments.classified_payments_m * 100, 1) }}% · {{ b2bPayments.period }} · {{ b2bPayments.coverage }}。
          <a v-if="b2bPayments.source.startsWith('https://')" :href="b2bPayments.source" target="_blank" rel="noopener noreferrer" class="text-accent underline">支付样本口径 ↗</a>
        </p>
        <p v-else class="mb-3 text-xs text-text-3">Circle 未披露同口径的 B2B 贸易与商户结算占比。全部 USDC 链上转账不能作为 CPN 商业结算量的分母。</p>
        <ul v-if="institutionEvidence.length" class="divide-y divide-border rounded-xl border border-border bg-card px-4">
          <li v-for="row in institutionEvidence" :key="`${row.institution}:${row.as_of}:${row.product}`" class="py-3 grid gap-1.5 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:gap-4">
            <div>
              <div class="text-sm font-semibold text-text">{{ row.institution }} <span class="ml-1 text-xs font-medium text-text-3">{{ row.product }}</span></div>
              <div class="mt-1 text-xs text-text-3">{{ row.stage }} · {{ row.as_of }}</div>
            </div>
            <div class="min-w-0 text-xs leading-relaxed text-text-2">
              {{ row.evidence }}
              <a v-if="row.source.startsWith('https://')" :href="row.source" target="_blank" rel="noopener noreferrer" class="whitespace-nowrap text-accent underline underline-offset-2">{{ row.source_label }} ↗</a>
            </div>
          </li>
        </ul>
        </section>

        <section aria-labelledby="crcl-arc" class="mb-6">
        <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h3 id="crcl-arc" class="text-sm font-semibold text-text">Arc · 手续费与资产沉淀</h3>
          <a href="https://explorer.arc.io/" target="_blank" rel="noopener noreferrer" class="text-xs text-accent underline underline-offset-2">Arc 主网浏览器 ↗</a>
        </div>
        <div class="grid grid-cols-1 min-[460px]:grid-cols-2 xl:grid-cols-3 gap-3 mb-3">
          <MetricTile label="Arc 月度网络手续费" :value="arcGas?.network_fees_usdc ?? null" suffix="USDC" :digits="2"
            :deltas="[{ text: arcGas ? `${arcGas.month} · UTC 完整月` : '2026-10 起 · 精确月合计待核验' }]"
            tip="主网交易实际支付的 gas 合计；手续费以 USDC 计价但不销毁，不包含 DeFi 应用自身的费用。" />
          <MetricTile label="Circle 可确认收到的链上手续费" :value="arcGas?.circle_fees_usdc ?? null" suffix="USDC" :digits="2"
            :deltas="[{ text: arcGas?.circle_fees_usdc == null ? '接收方归属未核实' : `${arcGas.month} · 不是财报收入` }]"
            tip="只累计可证实由 Circle 控制的手续费接收地址，不把验证者所得或全网手续费归给 Circle。" />
          <MetricTile :label="arcRwa?.scope === 'provider_sample' ? 'Arc 非稳定币 RWA（数据商样本）' : 'Arc 非稳定币 RWA 余额'"
            :value="arcRwa == null ? null : arcRwa.balance_m * 100" suffix="万美元" :digits="1"
            :deltas="[{ text: arcRwa ? `${arcRwa.as_of} · ${arcRwa.coverage}` : '暂无核实合约的同口径余额' }]"
            :tip="arcRwa?.scope === 'provider_sample'
              ? '只显示 DefiLlama 在 Arc 上已覆盖的非稳定币资产的链上 AUM，不是全链 RWA 余额，也不是 DeFi 锁仓量。'
              : '核对过发行方合约和估值的非稳定币代币化资产；不含 USDC、EURC、BTC 映射资产及重复包装。'" />
        </div>
        <p v-if="arcGasReference" class="mb-3 rounded-lg border border-border px-3 py-2 text-xs leading-relaxed text-text-3">
          {{ arcGasReference.month }} 参考：公开日均 {{ arcGasReference.average_daily_usdc.toLocaleString('zh-CN') }} USDC，
          按 {{ arcGasReference.days_covered }} 天折算约 {{ fmtNum(arcGasReference.average_daily_usdc * arcGasReference.days_covered / 1e4) }} 万 USDC。
          日均已取整，不作为精确月额。
          <a v-if="arcGasReference.source.startsWith('https://')" :href="arcGasReference.source" target="_blank" rel="noopener noreferrer"
            class="text-accent underline underline-offset-2">Bitquery 手续费原图 ↗</a>
        </p>
        <p v-if="arcGas?.source || arcRwa?.source" class="flex flex-wrap gap-x-4 gap-y-1 mb-3 text-xs text-text-3">
          <a v-if="arcGas?.source.startsWith('https://')" :href="arcGas.source" target="_blank" rel="noopener noreferrer" class="text-accent underline">Arc 手续费数据 ↗</a>
          <a v-if="arcRwa?.source.startsWith('https://')" :href="arcRwa.source" target="_blank" rel="noopener noreferrer" class="text-accent underline">Arc RWA 资产来源 ↗</a>
        </p>
        <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h4 class="text-xs font-semibold text-text-2">链上活动参考 · 不代表收入</h4>
          <a href="https://defillama.com/chain/arc" target="_blank" rel="noopener noreferrer" class="text-xs text-accent hover:underline">DefiLlama Arc 数据 ↗</a>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
          <MetricTile v-for="card in arcCards" :key="card.key" :label="card.label" :value="arcValue(card.key)" suffix="亿美元" :digits="2"
            :deltas="[{ text: arcPoint(card.key)?.date ?? '暂无观测' }]" :tip="card.tip" />
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-3">
          <GraphCard title="Arc 资产规模" height="240px" tip="两条曲线反映不同口径，有交集，不能相加。">
            <EChart v-if="arcSizeOpt" :option="arcSizeOpt" height="240px" />
            <p v-else class="py-4 text-xs text-text-3">暂无公开历史。</p>
          </GraphCard>
          <GraphCard title="Arc 交易活跃度 · DEX 日成交量" height="240px">
            <EChart v-if="arcDexOpt" :option="arcDexOpt" height="240px" />
            <p v-else class="py-4 text-xs text-text-3">暂无公开历史。</p>
          </GraphCard>
        </div>
        <div v-if="arcDisclosure" class="rounded-2xl border border-border bg-card p-4">
          <div class="flex flex-wrap items-baseline justify-between gap-2">
            <h4 class="text-sm font-medium text-text">Arc 官方披露 · {{ arcDisclosure.as_of }}</h4>
            <a v-if="arcDisclosure.source.startsWith('https://')" :href="arcDisclosure.source" target="_blank" rel="noopener noreferrer"
              class="text-xs text-accent hover:underline">Circle 业绩演示 · Arc 主网 ↗</a>
          </div>
          <dl class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
            <div><dt class="text-xs text-text-3">链上资产（非 TVL）</dt><dd class="text-sm font-semibold">{{ arcDisclosure.assets_b == null ? '—' : `${fmtNum(arcDisclosure.assets_b * 10, 1)} 亿美元` }}</dd></div>
            <div><dt class="text-xs text-text-3">上线以来 USDC 交易量</dt><dd class="text-sm font-semibold">{{ arcDisclosure.usdc_volume_min_b == null ? '—' : `${arcDisclosure.usdc_volume_min_b * 10}+ 亿美元` }}</dd></div>
            <div><dt class="text-xs text-text-3">日交易笔数</dt><dd class="text-sm font-semibold">{{ arcDisclosure.daily_transactions_approx == null ? '—' : `约 ${arcDisclosure.daily_transactions_approx.toLocaleString('zh-CN')} 笔` }}</dd></div>
          </dl>
        </div>
        </section>

        <GraphCard title="监管与商用里程碑" height="0px" :loading="loading" :error="errorText" @retry="load">
          <p class="text-xs leading-relaxed text-text-3 mb-3">发行资格、信托银行牌照、经合作行清算、直接接入美联储支付系统分别核对；获批不等于已经商用。更新于 {{ eventsUpdatedAt ?? '—' }}。</p>
          <div class="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] items-start gap-5">
            <section v-for="group in eventGroups" :key="group.title" class="min-w-0 space-y-2">
              <h4 class="text-xs font-semibold text-text-2">{{ group.title }}</h4>
              <div v-for="ev in group.items" :key="`${ev.date}:${ev.title}`"
                class="flex flex-col sm:flex-row gap-2 sm:gap-3 px-3.5 py-3 rounded-xl border border-border bg-surface/60">
                <div class="flex items-center gap-2 sm:block shrink-0 sm:w-[86px] pt-0.5">
                  <time class="block text-xs font-semibold text-text tabular-nums" :datetime="ev.date">{{ ev.date }}</time>
                  <span class="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px]" :class="CATEGORY_CLS[ev.category] ?? 'bg-slate-500/15 text-slate-400'">{{ ev.category }}</span>
                </div>
                <div class="min-w-0">
                  <div class="text-[13px] font-medium text-text">
                    {{ ev.title }}
                    <span class="ml-2 text-xs" :class="EVENT_STATUS_CLS[ev.status] ?? 'text-text-3'">{{ ev.status }}</span>
                  </div>
                  <div class="text-xs text-text-3 mt-1 leading-relaxed">{{ ev.detail }}</div>
                  <a v-if="ev.source.startsWith('https://')" :href="ev.source" target="_blank" rel="noopener noreferrer"
                    class="inline-block mt-1 text-xs text-accent underline underline-offset-2" :aria-label="`来源原文：${ev.title}`">来源原文 ↗</a>
                  <div v-else class="text-xs text-text-3 mt-1 break-words">来源：{{ ev.source }}</div>
                </div>
              </div>
              <button v-if="group.title === '最近进展' && pastEvents.length > 5" type="button"
                class="min-h-11 px-2 text-xs text-accent hover:underline" :aria-expanded="eventsExpanded" @click="eventsExpanded = !eventsExpanded">
                {{ eventsExpanded ? '收起早期事件' : `查看其余 ${pastEvents.length - 5} 条事件` }}
              </button>
            </section>
          </div>
        </GraphCard>
      </section>

      <!-- 两条曲线共用的组合告警不归为某一条曲线的实绩。 -->
      <div class="mb-5">
        <GraphCard :title="`告警规则状态 · ${triggeredRules.length} 条触发`" height="0px" :loading="loading" :error="errorText" @retry="load"
          tip="每次更新后按同一套阈值评估；数据不足不等于正常。">
          <div class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,192px),1fr))] items-start gap-3">
            <div v-for="r in rules" :key="r.rule"
              class="px-3.5 py-3 rounded-xl border bg-surface/60 flex flex-col gap-1.5"
              :class="[LEVEL_STYLE[r.level]?.ring ?? 'border-border', r.status === 'triggered' ? 'ring-1 ring-inset ring-red-500/40' : '']">
              <div class="flex items-center justify-between gap-2">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" :class="LEVEL_STYLE[r.level]?.badge ?? 'bg-slate-500/15 text-slate-400'">
                  {{ LEVEL_STYLE[r.level]?.label ?? r.level }}
                </span>
                <span class="px-2 py-0.5 rounded-full text-[10px] font-medium" :class="STATUS_LABEL[r.status]?.cls ?? 'bg-slate-500/20 text-slate-400'">
                  {{ STATUS_LABEL[r.status]?.text ?? r.status }}
                </span>
              </div>
              <div class="text-xs font-medium text-text leading-snug">{{ r.description }}</div>
              <div class="text-[11px] text-text-3 leading-snug">{{ r.message || '—' }}</div>
              <div class="text-[10px] text-text-4 mt-auto pt-1">{{ fmtTs(r.ts) }} · {{ r.rule }}</div>
            </div>
          </div>
        </GraphCard>
      </div>

      <!-- 采集与告警日志（默认折叠） -->
      <section class="bg-card border border-border rounded-2xl p-5">
        <button class="w-full flex items-center justify-between gap-2 text-left" @click="logsOpen = !logsOpen"
          :aria-expanded="logsOpen">
          <h3 class="text-sm font-semibold text-text">
            采集与告警日志
            <span class="text-text-3 text-xs font-normal ml-2">
              {{ logs.length }} 条 · 最近 {{ logs.length ? fmtTs(logs[0].ts) : '—' }}
            </span>
          </h3>
          <span class="text-text-3 text-xs shrink-0">{{ logsOpen ? '收起 ▲' : '展开 ▼' }}</span>
        </button>
        <div v-if="logsOpen" class="mt-3 overflow-x-auto">
          <table class="w-full text-xs tabular-nums">
            <thead>
              <tr class="text-left text-text-3 border-b border-border">
                <th class="py-1.5 pr-3 font-medium">时间 (UTC)</th>
                <th class="py-1.5 pr-3 font-medium">来源</th>
                <th class="py-1.5 pr-3 font-medium">状态</th>
                <th class="py-1.5 pr-3 font-medium">信息</th>
                <th class="py-1.5 font-medium text-right">耗时</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(l, i) in logs" :key="i" class="border-b border-border/40 last:border-0">
                <td class="py-1.5 pr-3 text-text-3 whitespace-nowrap">{{ fmtTs(l.ts) }}</td>
                <td class="py-1.5 pr-3 text-text-2 font-mono text-[11px]">{{ l.source }}</td>
                <td class="py-1.5 pr-3">
                  <span class="px-1.5 py-0.5 rounded text-[10px] font-medium"
                    :class="l.status === 'ok' ? 'bg-up-soft text-up'
                      : l.status === 'error' ? 'bg-red-500/15 text-red-300'
                      : l.status === 'alert' ? 'bg-amber-500/15 text-warn'
                      : 'bg-slate-500/15 text-slate-400'">{{ l.status }}</span>
                </td>
                <td class="py-1.5 pr-3 text-text-2 max-w-[420px] truncate" :title="l.message">{{ l.message }}</td>
                <td class="py-1.5 text-right text-text-3">{{ l.duration_ms >= 0 ? l.duration_ms + 'ms' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="text-[10px] text-text-4 mt-4 leading-relaxed">
        数据源：Circle 财报与公告 · DefiLlama（稳定币与 Arc）· 美国财政部 · AKShare / Yahoo Finance。
      </div>
    </PageState>
  </div>
</template>
