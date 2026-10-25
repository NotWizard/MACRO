<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '@/api/client'
import { useAsyncData } from '@/composables/useAsyncData'
import { useFiltersStore } from '@/stores/filters'
import { useRefreshStore } from '@/stores/refresh'
import MetricTile, { type TileDelta } from '@/components/layout/MetricTile.vue'
import CommentaryCard from '@/components/layout/CommentaryCard.vue'
import PageState from '@/components/layout/PageState.vue'
import ChartTip from '@/components/controls/ChartTip.vue'
import { phaseColor, phaseLabel } from '@/design/phases'
import type { SignalHistoryRow, SignalSummary } from '@/api/types'

type Rec = Record<string, string | number | null>
const filters = useFiltersStore()
const refresh = useRefreshStore()

const KPI_COLS = 'date,m2_yoy,cpi_yoy,pmi_official,pmi_caixin,m2_m1_spread,m0_yoy'

// One fetcher, one state machine, one abort signal — PageState renders the
// error branch, so it can no longer be assigned and forgotten.
const { state, data, error, retry, loading } = useAsyncData(async (signal) => {
  const st = filters.start ?? undefined, en = filters.end ?? undefined
  const [kpi, s, h] = await Promise.all([
    api.getDerivedMonthly(st, en, KPI_COLS, undefined, { signal }),
    api.getSignals({ signal }),
    api.getSignalHistory(undefined, { signal }),
  ])
  return {
    kpi: kpi.records.slice().reverse() as Rec[],   // latest first for latest()
    signals: s as SignalSummary,
    history: h.items as SignalHistoryRow[],
  }
}, { watch: [() => filters.start, () => filters.end, () => refresh.lastRefreshedAt] })

const kpiDm = computed<Rec[]>(() => data.value?.kpi ?? [])
const signals = computed<SignalSummary | null>(() => data.value?.signals ?? null)
const history = computed<SignalHistoryRow[]>(() => data.value?.history ?? [])
// Backend answered, but there is genuinely nothing to show yet (fresh DB) —
// deliberately distinct from an unreachable backend.
const isEmpty = computed(() => !kpiDm.value.length && !signals.value)
const onlyFlips = ref(false)

/** 数据截止：各 KPI 列最新非空日期的最大者（YYYY-MM）。
 *  不能用末行日期——outer join 后末行可能只有 bond_10y（KPI 全空）。 */
const dataAsOf = computed(() => {
  let best: string | null = null
  for (const t of tiles) {
    for (const r of kpiDm.value) {
      if (typeof r[t.col] === 'number' && typeof r.date === 'string') {
        if (!best || r.date > best) best = r.date
        break
      }
    }
  }
  return best ? best.slice(0, 7) : null
})

// ── KPI：最新值 + 较上期 delta ───────────────────────────────────────────────
function latestWithDelta(col: string, suffix: string): { value: number | null; delta: TileDelta | null } {
  let latest: number | null = null
  let prev: number | null = null
  for (const r of kpiDm.value) {           // kpiDm is latest-first
    const v = r[col]
    if (typeof v !== 'number') continue
    if (latest === null) latest = v
    else { prev = v; break }
  }
  if (latest === null || prev === null) return { value: latest, delta: null }
  const d = latest - prev
  if (Math.abs(d) < 1e-9) return { value: latest, delta: { text: `较上期 持平`, dir: 'flat' } }
  // 方向只表达数值增减，不预设好坏（CPI 升≠利好；剪刀差升≠利空）——避免误导
  return { value: latest, delta: { text: `较上期 ${d > 0 ? '+' : ''}${d.toFixed(1)}${suffix}`, dir: d > 0 ? 'up' : 'down' } }
}

const FRAMEWORKS = ['merrill', 'credit', 'inventory', 'debt'] as const
type Fw = typeof FRAMEWORKS[number]
const FW: Record<Fw, string> = { merrill: '美林', credit: '信用', inventory: '库存', debt: '债务' }

// ── 宏观扩张度 hero ────────────────────────────────────────────────────────
// 主分数使用 versioned 0–100 体系；旧 [-4,+4] 求和模型已移除。
const macro = computed(() => signals.value?.macro_score ?? null)
const macroScore = computed(() => macro.value?.score ?? null)
const scoreTone = computed(() => {
  const s = macroScore.value
  if (s == null) return 'text-text'
  return s >= 55 ? 'text-up' : s <= 45 ? 'text-down' : 'text-text'
})
const scorePos = computed(() => macroScore.value ?? 50)
const macroComponents = computed(() =>
  Object.values(macro.value?.components ?? {}).sort((a, b) => b.weight - a.weight),
)
const COMPONENT_TIPS: Record<string, string> = {
  growth: `实体增长（总权重 30%）：衡量国内生产与需求的扩张速度，是宏观扩张度的主锚。

子项权重：
• GDP 相对潜在增长 35%：实际 GDP 同比减去 5 期趋势，判断经济是否跑在自身产能趋势之上。
• 官方制造业 PMI 35%：50 为荣枯线，高于 50 表示制造业环比扩张。
• 工业增加值相对 6 个月趋势 30%：观察生产端增速是在加速还是减速。

得分口径：各子项线性映射到 0–100，50=中性；按可用子项权重归一。子项缺失不会当作中性，而是降低覆盖率和置信度。
贡献：(维度分 − 50) × 30%。`,
  credit: `信用条件（总权重 25%）：衡量融资供给与资金活化程度，以及信用扩张向实体需求传导的条件。

子项权重：
• M2 信贷脉冲 40%：M2 增量相对一年前的变化，反映新增流动性。
• M2−M1 剪刀差 20%：反向计分；剪刀差越小，说明资金越偏向活期、企业交易与投资意愿越强。
• 12 个月社融增量同比 20%：居民、企业与政府合计融资需求。
• 12 个月新增贷款同比 20%：银行体系向实体投放的信贷强度。

得分口径与其余维度一致：50=中性，越高越宽松。贡献：(维度分 − 50) × 25%。`,
  housing: `房地产（总权重 20%）：同时观察居民资产负债表和地产价格周期，避免把房价上涨直接等同于健康增长。

子项权重：
• 新房环比 12 个月均值 35%：平滑季节性，判断新房价格的持续动能。
• 二手房环比 12 个月均值 35%：二手市场更贴近真实成交与流动性。
• 新房环比上涨城市占比 30%：衡量价格改善的广度，避免只看少数核心城市。

当前价格广度基于项目追踪的 10 个重点城市，并非全国 70 城全样本。50=中性；贡献：(维度分 − 50) × 20%。`,
  external: `外需（总权重 15%）：衡量跨境制造业需求对中国生产与出口链条的拉动。

子项权重：
• 出口同比近 3 月均值 55%：平滑单月订单和基数扰动，反映出口趋势。
• 美国 ISM 制造业 PMI 45%：美国制造业景气的领先指标，50 为荣枯线。

得分口径：50=中性，越高表示外需越强；贡献：(维度分 − 50) × 15%。`,
  fiscal: `财政姿态（总权重 10%）：观察财政支出相对收入的变化，判断财政政策是在净加力还是相对收缩。

子项：
• 财政支出累计同比 − 财政收入累计同比：正值表示支出增长快于收入，通常对应更积极的财政姿态；负值表示相对克制。

得分口径：映射到 0–100，50=中性；贡献：(维度分 − 50) × 10%。`,
}
const componentTip = (key: string, weight: number) =>
  (COMPONENT_TIPS[key] ?? `${key}：当前权重 ${(weight * 100).toFixed(0)}%。`)
const componentTone = (score: number | null) =>
  score == null ? 'text-text-4' : score >= 55 ? 'text-up' : score <= 45 ? 'text-down' : 'text-text-2'
const fwChips = computed(() => {
  const s = signals.value
  if (!s) return []
  return FRAMEWORKS.map((k) => {
    const f = s[k] as { phase?: string } | undefined
    const ph = f?.phase ?? ''
    return { key: k, label: FW[k], phaseZh: phaseLabel(ph), color: phaseColor(ph) }
  })
})

const historyRows = computed(() => onlyFlips.value ? history.value.filter(r => r.flips.length) : history.value)
const isFlipped = (r: SignalHistoryRow, f: Fw) => r.flips.some(fl => fl.framework === f)
const historyScore = (r: SignalHistoryRow) =>
  r.macro_score != null ? r.macro_score.toFixed(1) : '—'
const rowAria = (r: SignalHistoryRow) =>
  `${r.ts.slice(0, 10)} 宏观扩张度 ${historyScore(r)}` +
  (r.flips.length ? '；' + r.flips.map(fl =>
    `${FW[fl.framework as Fw]}相位 ${phaseLabel(fl.prev)} → ${phaseLabel(fl.curr)}`).join('；') : '')

const historyTip = `每次成功刷新记录一行：版本化宏观扩张度（0–100）与四框架最新相位。旧 [-4,+4] 兼容分数已移除；任一框架相位相对上一条变化即翻转。

取数：/api/v1/signals/history → signal_history 表（01_fetch 成功提交后由 scripts/signal_history.py 追加，ts 与 manifest 同源）。`

const tiles = [
  { label: 'M2 同比', col: 'm2_yoy', suffix: '%', tip: `广义货币供应量 M2 同比增速，反映市场整体流动性，增速上行通常对应宽货币。

取数：AKShare macro_china_supply_of_money → money_supply.m2_yoy（原始同比，无衍生计算）→ 合并入 derived_monthly 表 → 取日期范围内最近一期有效值，单位 %。` },
  { label: 'CPI 同比', col: 'cpi_yoy', suffix: '%', tip: `居民消费价格指数同比，衡量通胀/通缩，2% 为常用目标线。

取数：AKShare macro_china_cpi_yearly → cpi.cpi_yoy（原始同比）→ left join 入 derived_monthly.cpi_yoy → 取最近一期有效值，单位 %。` },
  { label: 'PMI 官方', col: 'pmi_official', tip: `国家统计局制造业采购经理指数，50 为荣枯分界线，>50 表示扩张。

取数：AKShare macro_china_pmi_yearly → pmi.pmi_official（原始）→ 合并于 derived_monthly.pmi_official → 取最近一期有效值。` },
  { label: '财新 PMI', col: 'pmi_caixin', tip: `财新/S&P 制造业 PMI，样本偏中小及沿海企业，公认领先官方 PMI，同为 50 荣枯线。

取数：AKShare macro_china_cx_pmi_yearly → pmi.pmi_caixin（原始）→ 合并于 derived_monthly.pmi_caixin → 取最近一期有效值。` },
  { label: 'M2-M1 剪刀差', col: 'm2_m1_spread', suffix: 'pp', tip: `M2 同比减 M1 同比，衡量资金活化程度。剪刀差扩大（正值）常预示企业活期存款走弱、需求疲软。

取数：衍生计算 derived_monthly.m2_m1_spread = m2_yoy − m1_yoy（脚本 02_compute_derived.py）→ 取最近一期有效值，单位 pp（百分点）。` },
  { label: 'M0 同比', col: 'm0_yoy', suffix: '%', tip: `流通中现金 M0 同比，反映现金需求与居民消费活跃度。

取数：AKShare macro_china_supply_of_money → money_supply.m0_yoy（原始同比）→ 透传至 derived_monthly.m0_yoy → 取最近一期有效值，单位 %。` },
]

const signalTip = `宏观扩张度 0–100（50=中性）由五个不重叠维度加权：实体增长 30%、信用条件 25%、房地产 20%、外需 15%、财政 10%。信用收缩、通胀失衡、债务与房价压力另算为“宏观压力”，不再把加杠杆直接当成看多。

取数：/api/v1/signals.macro_score → analysis/macro_score.py。算法带版本号，旧四相位求和模型已完全移除。`

const kpiComputed = computed(() => new Map(tiles.map((t) => [t.col, latestWithDelta(t.col, t.suffix ?? '')])))

const lagNum = (k: string): number | null => {
  const v = (signals.value?.cross_lags as Record<string, unknown> | undefined)?.[k]
  return typeof v === 'number' ? v : null
}
const fmtLag = (k: string) => lagNum(k) ?? '—'
const fmtCorr = (k: string) => lagNum(k) !== null ? lagNum(k)!.toFixed(2) : '—'
</script>

<template>
  <div class="p-6 space-y-5">
    <header class="flex items-end justify-between">
      <div>
        <h1 class="text-xl font-bold text-text tracking-tight">综合概览</h1>
        <p class="text-xs text-text-3 mt-1">关键宏观指标 + 综合信号</p>
      </div>
      <div v-if="dataAsOf" class="text-[11px] text-text-4">数据截至 <span class="tnum text-text-3">{{ dataAsOf }}</span></div>
    </header>

    <!-- 取数状态由 PageState 统一渲染：后端不可达 ≠ 数据库暂无数据 -->
    <PageState
      :state="state"
      :error="error"
      :has-data="data != null"
      :empty="isEmpty"
      empty-title="数据库暂无宏观数据"
      empty-hint="后端已连接，但 derived_monthly / signals 还没有记录。点击顶部「刷新数据」执行一次采集。"
      min-height="320px"
      @retry="retry"
    >
      <div class="space-y-5">
        <!-- 宏观扩张度 hero：版本化分数 + 压力/置信度 + 贡献拆解 -->
        <section class="relative overflow-hidden bg-card border border-border rounded-2xl px-6 py-5">
          <div class="flex flex-wrap items-center gap-x-10 gap-y-4">
            <!-- 分数 + 判读：一个视觉单元，结论与分数同级，副标题降级 -->
            <div>
              <div class="flex items-center gap-1 text-[11px] font-semibold tracking-[0.14em] text-text-3 select-none">
                宏观扩张度<ChartTip :text="signalTip" />
              </div>
              <div class="mt-2 flex items-baseline gap-2.5">
                <span class="text-4xl font-extrabold tnum leading-none" :class="scoreTone">
                  {{ macroScore == null ? '—' : macroScore.toFixed(1) }}
                </span>
                <span class="text-[10px] self-end pb-0.5 text-text-4">/100</span>
                <span class="text-lg font-semibold leading-none" :class="scoreTone">{{ macro?.label ?? '数据不足' }}</span>
              </div>
              <div v-if="macro" class="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-3">
                <span v-if="macro.pressure != null">压力 <b class="text-text-2 tnum">{{ macro.pressure.toFixed(1) }}</b></span>
                <span>置信度 <b class="text-text-2 tnum">{{ macro.confidence.toFixed(0) }}%</b></span>
                <span>覆盖 <b class="text-text-2 tnum">{{ macro.coverage.toFixed(0) }}%</b></span>
              </div>
            </div>
            <!-- 刻度尺：0=明显收缩，50=中性，100=强扩张 -->
            <div class="flex-1 min-w-[240px] max-w-md" aria-hidden="true">
              <div class="signal-track relative h-2 rounded-full">
                <span class="signal-tick absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-px h-3" />
                <span
                  class="signal-marker absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-bg transition-all duration-300"
                  :class="(macroScore ?? 50) >= 55 ? 'bg-up text-up' : (macroScore ?? 50) <= 45 ? 'bg-down text-down' : 'bg-text-2 text-text-2'"
                  :style="{ left: scorePos + '%' }"
                />
              </div>
              <div class="flex justify-between mt-1.5 text-[10px] tnum text-text-4">
                <span>0 收缩</span><span>50 中性</span><span>100 扩张</span>
              </div>
            </div>
          </div>
          <div class="mt-4 pt-4 space-y-3">
            <div class="grid grid-cols-1 sm:grid-cols-5 gap-2">
              <div v-for="c in macroComponents" :key="c.key" class="px-2.5 py-2 rounded-lg bg-surface/70 border border-border/70">
                <div class="flex items-center justify-between gap-1 text-[10px] text-text-3">
                  <span class="inline-flex items-center whitespace-nowrap">
                    {{ c.label }}<ChartTip
                      :text="componentTip(c.key, c.weight)"
                      :label="`${c.label}指标说明`"
                    />
                  </span>
                  <span class="tnum flex-shrink-0">{{ c.weight * 100 }}%</span>
                </div>
                <div class="mt-1 flex items-baseline justify-between gap-2">
                  <span class="text-sm font-semibold tnum" :class="componentTone(c.score)">
                    {{ c.score == null ? '—' : c.score.toFixed(1) }}
                  </span>
                  <span class="text-[10px] tnum" :class="c.contribution >= 0 ? 'text-up' : 'text-down'">
                    {{ c.contribution >= 0 ? '+' : '' }}{{ c.contribution.toFixed(1) }}
                  </span>
                </div>
              </div>
            </div>
            <div v-if="fwChips.length" class="flex flex-wrap items-center gap-2">
              <span
                v-for="c in fwChips" :key="c.key"
                class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-border bg-surface text-[11px] text-text-2"
              >
                <span class="w-1.5 h-1.5 rounded-full" :style="{ background: c.color }" />
                {{ c.label }}<span class="text-text">{{ c.phaseZh }}</span>
              </span>
              <span v-if="macro?.positives.length" class="text-[11px] text-up">支撑：{{ macro.positives.join(' · ') }}</span>
              <span v-if="macro?.negatives.length" class="text-[11px] text-down">拖累：{{ macro.negatives.join(' · ') }}</span>
            </div>
            <div v-if="macro?.warnings.length" class="text-[11px] text-warn">
              {{ macro.warnings.join('；') }}
            </div>
          </div>
        </section>

        <!-- KPI 网格：等宽数字 + 环比上期 -->
        <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <MetricTile
            v-for="t in tiles" :key="t.col" :label="t.label"
            :value="kpiComputed.get(t.col)?.value ?? null"
            :suffix="t.suffix" :tip="t.tip"
            :deltas="kpiComputed.get(t.col)?.delta ? [kpiComputed.get(t.col)!.delta!] : []"
          />
        </div>

        <CommentaryCard />

        <div v-if="signals?.cross_lags" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="px-4 py-3.5 rounded-xl bg-card border border-border">
            <div class="text-[11px] text-text-3 mb-1">M1 → PPI</div>
            <div class="text-sm text-text-2">领先约 <b class="text-text tnum text-base">{{ fmtLag('m1_ppi_best_lag') }}</b> 个月
              <span class="text-text-3 text-xs ml-1">相关 r = {{ fmtCorr('m1_ppi_max_corr') }}</span>
            </div>
          </div>
          <div class="px-4 py-3.5 rounded-xl bg-card border border-border">
            <div class="text-[11px] text-text-3 mb-1">剪刀差 → CPI</div>
            <div class="text-sm text-text-2">领先约 <b class="text-text tnum text-base">{{ fmtLag('spread_cpi_best_lag') }}</b> 个月
              <span class="text-text-3 text-xs ml-1">相关 r = {{ fmtCorr('spread_cpi_max_corr') }}</span>
            </div>
          </div>
        </div>

        <section class="bg-card border border-border rounded-2xl p-5">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-semibold text-text">信号与相位历史<ChartTip :text="historyTip" /></h3>
            <label class="flex items-center gap-1.5 text-xs text-text-2 cursor-pointer">
              <input v-model="onlyFlips" type="checkbox" class="accent-warn"> 仅看翻转
            </label>
          </div>
          <ol role="list" class="space-y-1">
            <li v-for="r in historyRows" :key="r.ts"
                class="grid grid-cols-[5.5rem_2.5rem_1fr] items-center gap-3 px-2.5 py-1.5 rounded-lg transition-colors"
                :class="r.flips.length ? 'ring-1 ring-warn/40 bg-warn/5' : 'hover:bg-white/[0.02]'"
                :tabindex="r.flips.length ? 0 : undefined"
                :aria-label="rowAria(r)">
              <span class="text-xs text-text-3 tnum">{{ r.ts.slice(0, 10) }}</span>
              <span class="text-xs font-bold text-center tnum"
                    :class="r.macro_score != null && r.macro_score >= 55 ? 'text-up' : r.macro_score != null && r.macro_score <= 45 ? 'text-down' : 'text-text-2'">
                {{ historyScore(r) }}
              </span>
              <span class="flex flex-wrap gap-1">
                <span v-for="f in FRAMEWORKS" :key="f"
                      class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border bg-surface text-[11px] text-text-2"
                      :class="isFlipped(r, f) ? 'border-warn/60 text-text' : 'border-border'">
                  <span class="w-1.5 h-1.5 rounded-full" :style="{ background: phaseColor(r[f]) }" />
                  {{ FW[f] }}·{{ phaseLabel(r[f]) }}
                </span>
              </span>
            </li>
          </ol>
          <!-- reachable only when the fetch SUCCEEDED, so "暂无历史" is now true -->
          <p v-if="!loading && !historyRows.length" class="text-xs text-text-3">
            {{ history.length ? '暂无翻转行——取消「仅看翻转」可查看全部快照。' : '暂无历史快照——每次成功刷新后记录一条。' }}
          </p>
        </section>
      </div>
    </PageState>
  </div>
</template>

<style scoped>
/* 信号刻度尺：主题色板是 var() 引用，Tailwind 的透明度修饰符（from-x/25 等）对其不生效
   （静默不生成规则，本区块曾因此亮色下不可见）；改用 color-mix 直接取主题色定比混合，
   亮/暗双主题自适应。 */
.signal-track {
  background-image: linear-gradient(
    to right,
    color-mix(in srgb, var(--down) 26%, transparent),
    color-mix(in srgb, var(--text-4) 14%, transparent),
    color-mix(in srgb, var(--up) 26%, transparent)
  );
}
.signal-tick {
  background: color-mix(in srgb, var(--text-4) 55%, transparent);
}
/* 光环用 currentColor（由 marker 的 text-up/down/text-2 类提供），与点色同相 */
.signal-marker {
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.18),
    0 0 0 4px color-mix(in srgb, currentColor 22%, transparent);
}
</style>
