<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import { useAsyncData } from '@/composables/useAsyncData'
import { useRefreshStore } from '@/stores/refresh'
import GraphCard from '@/components/layout/GraphCard.vue'
import EChart from '@/components/charts/EChart.vue'
import ChartTip from '@/components/controls/ChartTip.vue'
import { buildMultiLine, buildSpreadChart } from '@/components/charts/options'
import { themedOption } from '@/composables/useThemedOption'
import type { IndexDividendSummary, PercentileResult } from '@/api/types'
import type { EChartsOption } from 'echarts'

type Rec = Record<string, string | number | null>

const refresh = useRefreshStore()

// ── 分位窗口选择（成立以来/1/3/5/10 年/自定义）─────────────────────────────
type WinKey = 'inception' | '1y' | '3y' | '5y' | '10y' | 'custom'
const WIN_LABEL: Record<WinKey, string> = {
  inception: '成立以来', '1y': '1年', '3y': '3年', '5y': '5年', '10y': '10年', custom: '自定义',
}
const win = ref<WinKey>('inception')
const customStart = ref('')
const customEnd = ref('')
const customPct = ref<Record<string, PercentileResult | null>>({})

// 基础数据：摘要（含全预设分位）+ 序列 + 健康。刷新完成后自动重载。
const { data, errorText: error, loading, retry: load } = useAsyncData(async (signal) => {
  const [s, series, health] = await Promise.all([
    api.getIndexDividendSummary(undefined, undefined, { signal }),
    api.getIndexDividendSeries(undefined, undefined, { signal }),
    api.getIndexDividendHealth({ signal }),
  ])
  return { summary: s, records: series.records, health }
}, { watch: [() => refresh.lastRefreshedAt] })

const summary = computed<IndexDividendSummary | null>(() => data.value?.summary ?? null)
const records = computed<Rec[]>(() => (data.value?.records ?? []) as Rec[])
const health = computed(() => data.value?.health ?? null)

// 自定义窗口：两端日期齐备才查询（查询时计算，后端毫秒级）
watch([win, customStart, customEnd], async ([w, s, e]) => {
  if (w !== 'custom' || !s || !e || s > e) return
  try {
    const r = await api.getIndexDividendSummary(s, e)
    const out: Record<string, PercentileResult | null> = {}
    for (const [k, m] of Object.entries(r.metrics)) out[k] = m.custom
    customPct.value = out
  } catch { customPct.value = {} }
})

// ── 指标瓦片 ─────────────────────────────────────────────────────────────
interface TileDef { key: string; label: string; unit: string; digits: number; tip: string }
const TILES: TileDef[] = [
  { key: 'dy', label: '股息率', unit: '%', digits: 2, tip: 'TR/PR 法重建（全收益/价格比日增量还原分红，365日滚动），经官方 DP2 口径常数校准' },
  { key: 'spread', label: '股债利差', unit: 'pp', digits: 2, tip: '股息率 − 10年期国债收益率。越高股票相对债券越便宜' },
  { key: 'pe', label: '市盈率 PE', unit: '倍', digits: 2, tip: '中证 index-perf 的 peg 字段（推定为 PE，G5 门持续监控），经官方 PE2 重叠区校准' },
  { key: 'pb', label: '市净率 PB', unit: '倍', digits: 2, tip: '蛋卷指数估值（2016-09 起周频，推定口径）——官方仅在月度 PDF 发布 PB' },
]

function pctOf(key: string): PercentileResult | null {
  const m = summary.value?.metrics?.[key]
  if (!m) return null
  if (win.value === 'custom') return customPct.value[key] ?? null
  return m.presets?.[win.value] ?? null
}

/** 分位 → 估值判定。方向相反：股息率/利差越高越便宜；PE/PB 越高越贵。 */
function verdict(key: string, pct: number | null): { text: string; cls: string } {
  if (pct == null) return { text: '样本不足', cls: 'text-text-4' }
  const dir = summary.value?.metrics?.[key]?.direction
  const cheapAtHigh = dir === 'higher_is_cheap'
  if (pct >= 70) return cheapAtHigh ? { text: '偏便宜', cls: 'text-up' } : { text: '偏贵', cls: 'text-down' }
  if (pct <= 30) return cheapAtHigh ? { text: '偏贵', cls: 'text-down' } : { text: '偏便宜', cls: 'text-up' }
  return { text: '中性', cls: 'text-text-3' }
}

const WIN_KEY_LABEL: Record<string, string> = WIN_LABEL
// 当前值来源标注：官方锚（中证 indicator.xls）/ TR-PR 重建 / 蛋卷
const SRC_LABEL: Record<string, string> = {
  official: '官方', recon: '重建', danjuan: '蛋卷', derived: '重建', '': '',
}
function pctMeta(p: PercentileResult | null): string {
  if (!p || p.n_obs === 0) return ''
  return `${p.window_start} ~ ${p.window_end} · ${p.n_obs} 个观测`
}

// ── 图表 ────────────────────────────────────────────────────────────────
// 从任一给定列首个非空值起裁剪（PE 2013-12 / PB 2016-09 才有数据，
// 之前的空段不占坐标轴——与全站 align_start 同一思路）
function trimFromFirstValid(recs: Rec[], cols: string[]): Rec[] {
  const i = recs.findIndex((r) => cols.some((c) => r[c] != null))
  return i > 0 ? recs.slice(i) : recs
}

// 本页是日频密度（~4800 类目 / ~1550px）：主题默认开启的 x 轴竖向网格线
// 会以每 0.3px 一条的密度糊成一层灰色面纱盖住 plot 区（月频页面点稀不受影响）。
// 关掉 x 轴 splitLine，与其他页面的观感对齐。
const noXGrid = (opt: EChartsOption): EChartsOption => {
  const xa = (opt.xAxis ?? {}) as Record<string, unknown>
  return { ...opt, xAxis: { ...xa, splitLine: { show: false } } }
}

const dyCgbOpt = themedOption(() => noXGrid(buildMultiLine(records.value, [
  { col: 'dy', name: '股息率(%)' }, { col: 'cgb_10y', name: '10年期国债(%)' },
], '%')))
const spreadOpt = themedOption(() => noXGrid(buildSpreadChart(records.value, 'spread', '股债利差', 'pp')))
const peOpt = themedOption(() => noXGrid(buildMultiLine(trimFromFirstValid(records.value, ['pe', 'pe_dj']), [
  { col: 'pe', name: 'PE(校准)' }, { col: 'pe_dj', name: 'PE(蛋卷)' },
], '倍')))
const pbOpt = themedOption(() => noXGrid(buildMultiLine(trimFromFirstValid(records.value, ['pb']), [
  { col: 'pb', name: 'PB(蛋卷)' },
], '倍')))
const pxTrOpt = themedOption(() => noXGrid(buildMultiLine(records.value, [
  { col: 'px_close', name: '价格指数' }, { col: 'tr_close', name: '全收益指数' },
])))

// ── 数据健康 ─────────────────────────────────────────────────────────────
const GATE_LABEL: Record<string, string> = {
  G1_recon_vs_official: 'G1 重建一致性', G2_payout_probe: 'G2 派息率探针',
  G5_peg_caliber_drift: 'G5 PE口径漂移', G6_dy_range: 'G6 股息率区间',
}
const gateCls = (s: string) =>
  s === 'ok' ? 'bg-up' : s === 'warn' ? 'bg-warn' : 'bg-down'
</script>

<template>
  <div class="p-6 space-y-5">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-bold text-text">红利低波估值</h1>
        <p class="text-xs text-text-3 mt-1">
          {{ summary?.index_name ?? '中证红利低波动指数' }}（{{ summary?.index_code ?? 'H30269' }}）
          · 数据至 {{ summary?.latest_date ?? '—' }}
          <span v-if="summary?.caliber">· 口径 {{ summary.caliber.toUpperCase() }}</span>
          <span v-if="summary?.k_dy">· 校准系数 {{ summary.k_dy }}</span>
        </p>
      </div>
      <!-- 分位窗口选择器 -->
      <div class="flex items-center gap-1.5 text-xs">
        <span class="text-text-3 mr-1">分位窗口</span>
        <button
          v-for="w in (['inception','1y','3y','5y','10y','custom'] as WinKey[])" :key="w"
          class="px-2.5 py-1 rounded-md border transition-colors"
          :class="win === w ? 'bg-accent-soft border-accent text-text' : 'border-border text-text-3 hover:text-text-2'"
          @click="win = w"
        >{{ WIN_KEY_LABEL[w] }}</button>
        <template v-if="win === 'custom'">
          <input v-model="customStart" type="date" class="ml-2 bg-card border border-border rounded-md px-2 py-1 text-text text-xs" />
          <span class="text-text-4">~</span>
          <input v-model="customEnd" type="date" class="bg-card border border-border rounded-md px-2 py-1 text-text text-xs" />
        </template>
      </div>
    </header>

    <!-- 指标瓦片：当前值 + 所选窗口分位（方向硬编码：PE/PB 高=贵，股息率/利差高=便宜） -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <div
        v-for="t in TILES" :key="t.key"
        class="bg-card border border-border rounded-xl px-4 pt-3.5 pb-3"
      >
        <div class="text-[11px] font-medium text-text-3 tracking-wide">{{ t.label }}<ChartTip :text="t.tip" /></div>
        <div class="text-[26px] font-extrabold tnum leading-tight mt-1.5 text-text">
          {{ summary?.metrics?.[t.key]?.value?.toFixed(t.digits) ?? '—' }}
          <span class="text-[13px] font-medium text-text-3">{{ t.unit }}</span>
          <span class="text-[10px] font-normal text-text-4 ml-1">{{ SRC_LABEL[summary?.metrics?.[t.key]?.value_source ?? ''] ?? '' }}</span>
        </div>
        <div class="mt-1 text-[11px] tnum">
          <template v-if="pctOf(t.key) && pctOf(t.key)!.percentile != null">
            <span :class="verdict(t.key, pctOf(t.key)!.percentile).cls" class="font-semibold">
              分位 {{ pctOf(t.key)!.percentile!.toFixed(1) }}% · {{ verdict(t.key, pctOf(t.key)!.percentile).text }}
            </span>
          </template>
          <span v-else class="text-text-4">分位样本不足</span>
        </div>
        <div class="text-[10px] text-text-4 mt-0.5 tnum">{{ pctMeta(pctOf(t.key)) }}</div>
      </div>
    </div>

    <GraphCard title="股息率 vs 10年期国债" tip="股息率为 TR/PR 法重建并经官方 DP2 校准；利差 = 股息率 − 国债收益率。" :loading="loading" :error="error" @retry="load">
      <EChart :option="dyCgbOpt" height="300px" />
    </GraphCard>
    <GraphCard title="股债利差" tip="股息率 − 10年期国债收益率（pp）。利差走阔 = 股票相对债券更便宜。" :loading="loading" :error="error" @retry="load">
      <EChart :option="spreadOpt" height="260px" />
    </GraphCard>
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-5">
      <GraphCard title="市盈率 PE" tip="peg 字段（推定 PE）经官方 PE2 重叠区常数校准；蛋卷 PE 作交叉校验参照。" :loading="loading" :error="error" @retry="load">
        <EChart :option="peOpt" height="260px" />
      </GraphCard>
      <GraphCard title="市净率 PB" tip="蛋卷周频序列（2016-09 起，推定口径）——官方仅在月度指数单张 PDF 发布 PB。" :loading="loading" :error="error" @retry="load">
        <EChart :option="pbOpt" height="260px" />
      </GraphCard>
    </div>
    <GraphCard title="价格指数 vs 全收益指数" tip="全收益含分红再投资——两线开口即分红的长期复利效应，也是股息率重建的原料。" :loading="loading" :error="error" @retry="load">
      <EChart :option="pxTrOpt" height="280px" />
    </GraphCard>

    <!-- 数据健康：质量门 + 各源新鲜度 -->
    <div class="bg-card border border-border rounded-xl px-4 py-3.5">
      <div class="text-[11px] font-medium text-text-3 tracking-wide mb-2">数据健康</div>
      <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px]">
        <span v-for="g in health?.gates ?? []" :key="g.gate" class="inline-flex items-center gap-1.5" :title="g.detail">
          <span class="w-1.5 h-1.5 rounded-full" :class="gateCls(g.status)" />
          <span class="text-text-2">{{ GATE_LABEL[g.gate] ?? g.gate }}</span>
          <span class="text-text-4 tnum">{{ g.detail }}</span>
        </span>
      </div>
      <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 pt-2 border-t border-border text-[10px] text-text-4 tnum">
        <span v-for="s in health?.sources ?? []" :key="s.table">
          {{ s.label }} 至 {{ s.latest_date ?? '—' }}（{{ s.rows }} 行）
        </span>
      </div>
    </div>
  </div>
</template>
