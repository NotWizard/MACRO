<script setup lang="ts">
import { markRaw, computed } from 'vue'
import { api } from '@/api/client'
import { useAsyncData } from '@/composables/useAsyncData'
import { useFiltersStore } from '@/stores/filters'
import { useRefreshStore } from '@/stores/refresh'
import SectionCommentary from '@/components/layout/SectionCommentary.vue'
import { buildDualAxisLine, buildScatterQuadrant, buildMultiLine } from '@/components/charts/options'
import EChart from '@/components/charts/EChart.vue'
import GraphCard from '@/components/layout/GraphCard.vue'
import { phaseColor, phaseLabel } from '@/design/phases'
import type { CycleFrame } from '@/api/types'
import { themedOption } from '@/composables/useThemedOption'
import type { ScatterSeriesOption } from 'echarts'

const filters = useFiltersStore()
const refresh = useRefreshStore()
type Rec = Record<string, string | number | null>

// useAsyncData owns loading/error/abort; `load` is still the retry handler.
const { data, errorText: error, loading, retry: load } = useAsyncData(async (signal) => {
  const [ip, cx, c, detail] = await Promise.all([
    api.getDerivedMonthly(filters.start ?? undefined, filters.end ?? undefined, 'date,pmi_official,ip_yoy,ip_cumulative', true, { signal }),
    api.getDerivedMonthly(filters.start ?? undefined, filters.end ?? undefined, 'date,pmi_official,pmi_caixin,pmi_non_mfg,pmi_caixin_svc', true, { signal }),
    api.getCycle('inventory', filters.start ?? undefined, filters.end ?? undefined, { signal }),
    api.getTable('pmi_detail', filters.start ?? undefined, filters.end ?? undefined, { signal }),
  ])
  return {
    ip: markRaw(ip.records),
    pmi: markRaw(cx.records),
    cycle: markRaw(c),
    detail: markRaw(detail.records),
  }
}, { watch: [() => filters.start, () => filters.end, () => refresh.lastRefreshedAt] })

// Per-chart groups so 财新 PMI (2012) doesn't truncate 官方 PMI + IP (2008).
const ipDm = computed<Rec[]>(() => data.value?.ip ?? [])      // date,pmi_official,ip_yoy → 2008-02
const pmiDm = computed<Rec[]>(() => data.value?.pmi ?? [])    // date,pmi_official,pmi_caixin,pmi_non_mfg,pmi_caixin_svc → 2012-04
const cycle = computed<CycleFrame | null>(() => data.value?.cycle ?? null)
const detailDm = computed<Rec[]>(() => data.value?.detail ?? [])

// Options built in computeds (not the template) → one markRaw'd object per
// rebuild that ECharts merges into the live instance (preserves zoom/legend).
const ipOpt = themedOption(() => {
  const option = buildDualAxisLine(ipDm.value, 'pmi_official', 'ip_yoy')
  const joint: ScatterSeriesOption = {
    name: '工业增加值 1–2 月合报 (%)', type: 'scatter', yAxisIndex: 1,
    symbol: 'diamond', symbolSize: 9,
    data: ipDm.value.flatMap((r): [string, number][] =>
      String(r.date).slice(5, 7) === '02' && typeof r.ip_cumulative === 'number'
        ? [[String(r.date), r.ip_cumulative]] : []),
  }
  if (Array.isArray(option.series)) option.series.push(joint)
  return option
})
const quadOpt = themedOption(() => (buildScatterQuadrant(cycle.value?.series ?? [], 'pmi_official', 'ip_yoy', 'PMI', '工业增加值同比(%)', null, 50)))
const pmiOpt = themedOption(() => (buildMultiLine(pmiDm.value, [{ col: 'pmi_official', name: '官方制造业' }, { col: 'pmi_caixin', name: 'RatingDog 制造业' }, { col: 'pmi_non_mfg', name: '官方非制造业' }, { col: 'pmi_caixin_svc', name: 'RatingDog 服务业' }], '', 50)))
const detailOpt = themedOption(() => (buildMultiLine(detailDm.value, [
  { col: 'new_orders', name: '新订单' },
  { col: 'production', name: '生产' },
  { col: 'raw_material_inventory', name: '原材料库存' },
  { col: 'finished_goods_inventory', name: '产成品库存' },
], '', 50)))
</script>

<template>
  <div class="p-6 space-y-5">
    <header><h1 class="text-xl font-bold text-text">库存周期</h1>
      <p class="text-xs text-text-3 mt-1">PMI + 工业增加值 → 主动/被动补库·去库，并拆分订单与库存分项</p>
    </header>
    <div v-if="cycle?.latest_phase" class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-card border border-border">
      <span class="w-2 h-2 rounded-full" :style="{ background: phaseColor(cycle.latest_phase) }" />
      <span class="text-xs text-text-2">当前：<b class="text-text">{{ phaseLabel(cycle.latest_phase) }}</b></span>
    </div>
    <GraphCard title="PMI vs 工业增加值同比" tip="工业折线只画已公布的单月同比。菱形为国家统计局公布的 1–2 月累计同比，不是 2 月单月数，也不参与库存周期判定。" :loading="loading" :error="error" @retry="load">
      <p class="mb-2 text-xs text-text-2">每年 1–2 月合报值以菱形单独显示；不拿合报值补单月折线。</p>
      <EChart :option="ipOpt" height="320px" />
    </GraphCard>
    <GraphCard title="库存周期四象限" tip="PMI vs 工业增加值同比的阶段分布。" :loading="loading" :error="error" @retry="load">
      <EChart :option="quadOpt" :not-merge="true" height="360px" />
    </GraphCard>
    <GraphCard title="PMI 订单、生产与库存分项" tip="NBS 制造业 PMI 分项：新订单、生产、原材料库存、产成品库存，50 为荣枯线。新订单高于生产且库存偏低通常对应主动补库；订单走弱而产成品库存上升则偏向被动累库。" :loading="loading" :error="error" @retry="load">
      <EChart :option="detailOpt" height="320px" />
    </GraphCard>
    <GraphCard title="PMI：官方与民间调查" tip="官方制造业、官方非制造业、RatingDog 制造业和服务业（旧称财新）；50 为荣枯线。不同调查样本与口径，分开比较。" :loading="loading" :error="error" @retry="load">
      <EChart :option="pmiOpt" height="300px" />
    </GraphCard>
    <SectionCommentary section="inventory" />

  </div>
</template>
