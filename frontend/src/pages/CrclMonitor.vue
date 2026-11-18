<script setup lang="ts">
// CRCL 监控 — 公司经营与估值、CPN 与 Arc 产品、告警和事件。
//
// 刷新状态住在 stores/refresh（FE-H4）：本页曾自持 refreshing/progress + 裸 fetch
// SSE，导致顶栏 🔄 与日期预设在本页是死控件、离开页面时 reader 循环继续写已销毁
// 组件的 ref、返回本页还能再启动一次并发采集。现在统一走 refresh.stream(false,'crcl')，
// 顶栏按 route.meta.refreshKind 呈现，本页只读状态。
import { ref, markRaw, computed } from 'vue'
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

const overview = computed(() => data.value?.overview ?? null)
const metrics = computed<Record<string, CrclMetric>>(() => data.value?.metrics ?? {})
const events = computed(() => data.value?.events ?? [])
const pastEvents = computed(() => events.value.filter((event) => event.status !== '计划').slice().reverse())
const upcomingEvents = computed(() => events.value.filter((event) => event.status === '计划'))
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
  !overview.value?.last_run && Object.values(metrics.value).every((m) => !m.points?.length))

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
const reserveShareDeltas = computed<TileDelta[]>(() => [
  pctChip('储备收入同比', num(latestQ.value, 'reserve_revenue_yoy_pct')),
])
const adjustedShare = computed(() => num(latestQ.value, 'adjusted_nonreserve_share_pct'))
const oneOffRevenue = computed(() => num(latestQ.value, 'nonreserve_one_off_m'))
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
    xAxis: { type: 'category', data: dates, ...baseAxis({ boundaryGap: false }) },
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
const usdcOpt = themedOption(() => lineOption(metrics.value.usdc_circ, { area: true, unit: '亿美元', divisor: 1e8 }))
const totalOpt = themedOption(() => lineOption(metrics.value.stablecoin_total, { area: true, unit: '亿美元', divisor: 1e8 }))
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
          看 Circle 的盈利、估值与稳定币基本盘，再看 CPN 和 Arc 的产品进展。
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
      <section aria-labelledby="crcl-company" class="mb-7">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-3">
        <h2 id="crcl-company" class="text-base font-semibold text-text">公司经营与估值</h2>
        <span v-if="latestQ" class="text-xs text-text-3">最新财报 · {{ latestQ.period }}</span>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <MetricTile label="稀释 EPS" :value="num(latestQ, 'eps_actual')" :digits="2" suffix="美元"
          :deltas="[{ text: consensusEPS == null ? '一致预期未核实' : `一致预期 ${consensusEPS.toFixed(2)} 美元` }]"
          tip="GAAP 稀释每股收益。只有可核验且口径可比时才展示一致预期。" />
        <MetricTile label="季度总收入" :deltas="revenueDeltas" :value="latestQ?.total_revenue_m != null ? Number(latestQ.total_revenue_m) / 100 : null" suffix="亿美元" :digits="2"
          :tip="`含储备收入。\n\n来源：${latestQ?.source ?? '—'}`" />
        <MetricTile label="CRCL 收盘价" :deltas="priceDeltas" :value="priceSeries.at(-1)?.value ?? null" suffix="美元" :digits="2"
          :tip="`最近收盘：${priceSeries.at(-1)?.date ?? '—'}。日环比指较上一个交易日。`" />
        <MetricTile label="市值" :value="valuation.market_cap != null ? valuation.market_cap / 1e8 : null" suffix="亿美元" :digits="1"
          tip="Yahoo Finance 市值快照。" />
        <MetricTile label="前瞻 − TTM P/E" :deltas="peDeltas" :value="peSpread" suffix="倍" :digits="1"
          tip="Yahoo Finance 前瞻与过去 12 个月市盈率之差。一次性项目会影响 TTM，差值不单独构成盈利下滑的证据。" />
        <MetricTile label="储备收入占比" :deltas="reserveShareDeltas" :value="latestQ?.reserve_revenue_m != null && latestQ?.total_revenue_m != null ? +(100 * Number(latestQ.reserve_revenue_m) / Number(latestQ.total_revenue_m)).toFixed(1) : null" suffix="%"
          :tip="`储备（利息）收入占总收入比——越高越像“货币基金”。\n\n来源：季报拆解。`" />
        <MetricTile label="非储备收入占比（披露）" :value="num(latestQ, 'nonreserve_share_pct')" suffix="%" :digits="2"
          :deltas="oneOffRevenue == null ? [] : [{ text: `含已标注一次性收入 ${(oneOffRevenue / 100).toFixed(3)} 亿美元` }]"
          tip="财报披露的其他收入 ÷ 总收入。包含已确认的一次性项目，不能直接代表持续经营趋势。" />
        <MetricTile label="非储备收入占比（调整后）" :value="adjustedShare" suffix="%" :digits="2" accent
          tip="告警口径：从其他收入和总收入同时剔除已标注的一次性收入。它不等于经审计的经常性收入占比；没有已标注项目的季度沿用披露值。" />
        <MetricTile label="分发与交易成本率" :value="num(latestQ, 'distribution_cost_ratio_pct')" suffix="%" :digits="2"
          tip="季报中的分发、交易及其他成本 ÷ 总收入；不等同于支付给单个渠道的分成。" />
        <MetricTile label="USDC 流通量" :deltas="usdcDeltas" :value="stableSnap.usdc_circ != null ? Number(stableSnap.usdc_circ) / 1e8 : null" suffix="亿美元" :digits="1"
          tip="DefiLlama 的 USDC 全链流通量，不是 Arc 或 CPN 专属指标。" />
        <MetricTile label="稳定币总市值" :deltas="totalDeltas" :value="stableSnap.stablecoin_total != null ? Number(stableSnap.stablecoin_total) / 1e8 : null" suffix="亿美元" :digits="0"
          tip="DefiLlama 全链行业口径，用于比较 USDC 与市场整体增长。" />
        <MetricTile label="EURC 流通量" :value="stableSnap.eurc_circ != null ? Number(stableSnap.eurc_circ) / 1e8 : null" suffix="亿欧元" :digits="2"
          :deltas="eurcDeltas" tip="卡片为 DefiLlama 最新流通量；环比比较来自最近两期可比财报。" />
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GraphCard title="CRCL 收盘价" height="280px" :loading="loading" :error="errorText" @retry="load"
          :tip="`IPO 以来日线收盘价。\n\n取数：AKShare stock_us_daily('CRCL')（主源）→ yfinance history（备用源），日频。`">
          <EChart v-if="priceOpt" :option="priceOpt" height="280px" />
        </GraphCard>
        <GraphCard title="USDC 流通量" height="280px" :loading="loading" :error="errorText" @retry="load"
          :tip="`2018 年至今完整历史。同比增速是黄色警报规则 y_usdc_growth 的输入（阈值 15%）。\n\n取数：DefiLlama，日频。`">
          <EChart v-if="usdcOpt" :option="usdcOpt" height="280px" />
        </GraphCard>
        <GraphCard title="稳定币总市值（行业水位）" height="280px" :loading="loading" :error="errorText" @retry="load"
          :tip="`全部稳定币合计市值——决定 Circle 增长的行业水位。\n\n取数：DefiLlama stablecoincharts/all，日频。`">
          <EChart v-if="totalOpt" :option="totalOpt" height="280px" />
        </GraphCard>
        <GraphCard title="短端美债收益率（储备收入）" height="280px" :loading="loading" :error="errorText" @retry="load"
          tip="短端收益率影响储备利息。实际影响还取决于 USDC 流通量、资产组合和分发成本。来源：美国财政部。">
          <EChart v-if="treasuryOpt" :option="treasuryOpt" height="280px" />
        </GraphCard>
      </div>
      </section>

      <section aria-labelledby="crcl-products" class="mb-7">
        <h2 id="crcl-products" class="text-base font-semibold text-text mb-3">CPN 与 Arc 产品</h2>
        <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h3 class="text-sm font-medium text-text-2">CPN · 支付网络</h3>
          <span v-if="latestQ" class="text-xs text-text-3">{{ latestQ.period }} 披露</span>
        </div>
        <div class="grid grid-cols-2 gap-3 mb-5">
          <MetricTile label="CPN 年化支付量" :value="annualizedCpnTpv" suffix="亿美元" :digits="0"
            :deltas="[{ text: '季末前 30 天年化' }]"
            tip="将季末前 30 天 CPN 支付量折算为年率，不是本季实际支付量，也不代表全部 USDC 的链上转账量。" />
          <MetricTile label="CPN 接入机构" :deltas="cpnInstDeltas" :value="num(latestQ, 'cpn_institutions')" suffix="家" :digits="0"
            tip="财报披露的接入机构数，不等于活跃交易机构数。" />
        </div>

        <div class="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h3 class="text-sm font-medium text-text-2">Arc · 公链</h3>
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

      <!-- ③ 告警规则状态 -->
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

      <!-- ④ 宏观事件与里程碑 -->
      <div class="mb-5">
        <GraphCard title="宏观事件与里程碑" height="0px" :loading="loading" :error="errorText" @retry="load">
          <div class="text-xs text-text-3 mb-3">更新于 {{ eventsUpdatedAt ?? '—' }} · 共 {{ events.length }} 条</div>
          <div class="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] items-start gap-5">
          <section v-for="group in eventGroups" :key="group.title" class="min-w-0 space-y-2">
            <h4 class="text-xs font-semibold text-text-2">{{ group.title }}</h4>
            <div v-for="ev in group.items" :key="`${ev.date}:${ev.title}`"
              class="flex flex-col sm:flex-row gap-2 sm:gap-3 px-3.5 py-3 rounded-xl border border-border bg-surface/60">
              <div class="flex items-center gap-2 sm:block shrink-0 sm:w-[86px] pt-0.5">
                <div class="text-xs font-semibold text-text tabular-nums">{{ ev.date }}</div>
                <span class="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px]" :class="CATEGORY_CLS[ev.category] ?? 'bg-slate-500/15 text-slate-400'">{{ ev.category }}</span>
              </div>
              <div class="min-w-0">
                <div class="text-[13px] font-medium text-text">
                  {{ ev.title }}
                  <span class="ml-2 text-[10px]" :class="EVENT_STATUS_CLS[ev.status] ?? 'text-text-3'">● {{ ev.status }}</span>
                </div>
                <div class="text-xs text-text-3 mt-1 leading-relaxed">{{ ev.detail }}</div>
                <a v-if="ev.source.startsWith('https://')" :href="ev.source" target="_blank" rel="noopener noreferrer"
                  class="inline-block mt-1 text-xs text-accent underline underline-offset-2" :aria-label="`来源原文：${ev.title}`">来源原文 ↗</a>
                <div v-else class="text-[11px] text-text-3 mt-1 break-words">来源：{{ ev.source }}</div>
              </div>
            </div>
            <button v-if="group.title === '最近进展' && pastEvents.length > 5" type="button"
              class="min-h-9 px-2 text-xs text-accent hover:underline" :aria-expanded="eventsExpanded" @click="eventsExpanded = !eventsExpanded">
              {{ eventsExpanded ? '收起早期事件' : `查看其余 ${pastEvents.length - 5} 条事件` }}
            </button>
          </section>
          </div>
        </GraphCard>
      </div>

      <!-- ⑤ 采集与告警日志（默认折叠） -->
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
