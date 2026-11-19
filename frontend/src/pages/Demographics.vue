<script setup lang="ts">
import { markRaw, computed } from 'vue'
import { api } from '@/api/client'
import { useAsyncData } from '@/composables/useAsyncData'
import { useFiltersStore } from '@/stores/filters'
import { useRefreshStore } from '@/stores/refresh'
import { buildMultiLine } from '@/components/charts/options'
import EChart from '@/components/charts/EChart.vue'
import GraphCard from '@/components/layout/GraphCard.vue'
import { themedOption } from '@/composables/useThemedOption'

type Rec = Record<string, string | number | null>
const filters = useFiltersStore()
const refresh = useRefreshStore()

// useAsyncData owns loading/error/abort; `load` is still the retry handler.
const { data, errorText: error, loading, retry: load } = useAsyncData(async (signal) => {
  const d = await api.getTable('demographics', filters.start ?? undefined, filters.end ?? undefined, { signal })
  return markRaw(d.records) as Rec[]
}, { watch: [() => filters.start, () => filters.end, () => refresh.lastRefreshedAt] })

const dm = computed<Rec[]>(() => data.value ?? [])
const yearRows = computed(() => dm.value.map((row) => ({ ...row, date: String(row.date).slice(0, 4) })))
const lastYear = computed(() => yearRows.value.at(-1)?.date)

function latest(col: string): number | null {
  // dm 按日期升序, 从后往前取第一个有值行 = 最新一年
  for (let i = dm.value.length - 1; i >= 0; i--) {
    const v = dm.value[i][col]
    if (typeof v === 'number') return v
  }
  return null
}

// Options built in computeds (not the template) → one markRaw'd object per
// rebuild that ECharts merges into the live instance.
const urbanOpt = themedOption(() => (buildMultiLine(yearRows.value, [{ col: 'urbanization_rate', name: '城镇化率' }], '%')))
const popOpt = themedOption(() => (buildMultiLine(yearRows.value, [{ col: 'population', name: '年末总人口' }], '万人')))
const birthOpt = themedOption(() => (buildMultiLine(yearRows.value, [{ col: 'birth_rate', name: '出生率' }, { col: 'natural_growth_rate', name: '自然增长率' }], '‰', 0, '零线')))
const ageOpt = themedOption(() => (buildMultiLine(yearRows.value, [
  { col: 'age_65plus_pct', name: '65 岁以上人口占比' },
  { col: 'dependency_ratio', name: '抚养比' },
], '%')))
</script>

<template>
  <div class="p-6 space-y-5">
    <header>
      <h1 class="text-xl font-bold text-text">人口与城镇化</h1>
      <p class="text-xs text-text-3 mt-1">年度数据<span v-if="lastYear"> · 所选区间最新为 {{ lastYear }} 年</span>。图表年份是统计所属年，不是发布日期。</p>
    </header>

    <!-- KPI -->
    <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3" v-if="dm.length">
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">城镇化率</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('urbanization_rate')?.toFixed(1) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">%</span></div>
      </div>
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">年末总人口</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('population')?.toFixed(0) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">万</span></div>
      </div>
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">出生率</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('birth_rate')?.toFixed(2) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">‰</span></div>
      </div>
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">自然增长率</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('natural_growth_rate')?.toFixed(2) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">‰</span></div>
      </div>
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">65 岁及以上占比</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('age_65plus_pct')?.toFixed(1) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">%</span></div>
      </div>
      <div class="bg-card border border-border rounded-xl p-4">
        <div class="text-xs text-text-3">抚养比</div>
        <div class="text-2xl font-bold text-text mt-1">{{ latest('dependency_ratio')?.toFixed(1) ?? '—' }}<span class="text-sm text-text-3 ml-0.5">%</span></div>
      </div>
    </div>

    <GraphCard title="常住人口城镇化率" tip="城镇常住人口占总人口比重，反映城镇化进程。NBS 年度数据。" :loading="loading" :error="error" @retry="load">
      <EChart :option="urbanOpt" height="300px" />
    </GraphCard>

    <GraphCard title="年末总人口" tip="年末常住人口（万人），近年增速趋缓。NBS 年度数据。" :loading="loading" :error="error" @retry="load">
      <EChart :option="popOpt" height="300px" />
    </GraphCard>

    <GraphCard title="出生率与自然增长率" tip="出生率（‰）与自然增长率（‰，= 出生率 − 死亡率）。0 线为人口零增长。" :loading="loading" :error="error" @retry="load">
      <EChart :option="birthOpt" height="300px" />
    </GraphCard>

    <GraphCard title="人口老龄化与抚养比" tip="65 岁以上人口占比与年龄抚养比（非劳动年龄人口 / 劳动年龄人口）。World Bank WDI 年度口径；抚养比上升意味着单位劳动人口承担更多少儿和老年人口。" :loading="loading" :error="error" @retry="load">
      <EChart :option="ageOpt" height="300px" />
    </GraphCard>
  </div>
</template>
