<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import type { ComputePanel, ComputeEntity, ComputeMetric, ComputeSeries, ComputeInterpretation } from '@/api/compute'
import EChart from '@/components/charts/EChart.vue'
import ChartTip from '@/components/controls/ChartTip.vue'
import { computeOption, formatCompute, preferredSeries } from '@/components/charts/computeOptions'
import { themedOption } from '@/composables/useThemedOption'
const props = defineProps<{ panel: ComputePanel; entities: Record<string, ComputeEntity>; metrics: Record<string, ComputeMetric>; analysis?: ComputeInterpretation; analysisStale?: boolean }>()
const emit = defineEmits<{ source: [series: ComputeSeries] }>()
const company = defineModel<string>('company')
const isRental = computed(() => props.panel.id === 'rental')
const primaryChart = computed(() => ['manufacturing', 'demand'].includes(props.panel.id) ? props.panel.charts[0]?.[0] : undefined)
const capexMetrics = new Set(['cash_capex', 'reported_capex', 'finance_lease_additions', 'finance_lease_principal'])
const chartChoice = defineModel<string>('chartChoice')
const companyRail = ref<HTMLElement>()
const companyDetails = ref<HTMLDetailsElement>()
const featuredIds = ['DELL', 'HPE', 'TW6669', 'TW2382', 'TW2317', 'TW3231', 'TSM', 'SKHYNIX']
const watchedMetrics = computed(() => new Set(props.panel.charts.flatMap(([, metrics]) => metrics)))
const trends = computed(() => preferredSeries(props.panel.series.filter(s =>
  s.kind === 'actual' && watchedMetrics.value.has(s.metric)
  && (props.panel.id !== 'demand' || !capexMetrics.has(s.metric) || s.frequency === 'quarterly')))
  .filter(s => s.points.length >= 2))
const available = computed(() => new Set(trends.value.map(s => s.entity)))
const selected = computed(() => company.value && available.value.has(company.value)
  ? company.value : props.panel.entities.find(e => available.value.has(e)) ?? '')
const isDeployment = computed(() => props.panel.id === 'power' && props.entities[selected.value]?.group === '部署')
const featured = computed(() => props.panel.id === 'manufacturing'
  ? featuredIds.filter(e => props.panel.entities.includes(e)) : props.panel.entities)
const quickCompanies = computed(() => selected.value && !featured.value.includes(selected.value)
  ? [...featured.value, selected.value] : featured.value)
const moreCompanies = computed(() => {
  const byGroup = new Map<string, string[]>()
  for (const id of props.panel.entities) {
    if (featured.value.includes(id)) continue
    const group = props.entities[id]?.group ?? '其他'
    const list = byGroup.get(group) ?? []
    list.push(id)
    byGroup.set(group, list)
  }
  return [...byGroup]
})
const charts = computed(() => props.panel.charts.flatMap(([title, wanted]) => {
  const rows = trends.value.filter(s =>
    (isRental.value ? /^(GETDEPLOYING|SEMIANALYSIS)_/.test(s.entity) : s.entity === selected.value)
    && wanted.includes(s.metric))
    .sort((a, b) => isRental.value
      ? props.panel.entities.indexOf(a.entity) - props.panel.entities.indexOf(b.entity)
      : wanted.indexOf(a.metric) - wanted.indexOf(b.metric))
  if (!rows.length && title !== primaryChart.value) return []
  const units = [...new Set(rows.map(s => s.unit + ':' + s.currency))].slice(0, 2)
  return [{
    title: isDeployment.value && title === '设备交付' ? '公司收入' : title,
    series: rows.filter(s => units.includes(s.unit + ':' + s.currency)),
  }]
}))
const leadMetric: Record<string, string> = { earnings: 'eps', power: 'orders_yoy', quality: 'inventory_total' }
const focusMetric = computed(() => isDeployment.value ? 'it_capacity_mw' : leadMetric[props.panel.id])
const activeChart = computed(() => charts.value.find(chart => chart.title === chartChoice.value)
  ?? charts.value.find(chart => chart.series.some(s => s.metric === focusMetric.value))
  ?? charts.value[0])
const chartOption = themedOption(() => activeChart.value?.series.length ? computeOption(activeChart.value.series, props.metrics) : null)
const readings = computed(() => (activeChart.value?.series ?? []).filter((s, i, rows) =>
  s.frequency !== 'annual' && rows.findIndex(x => isRental.value ? x.entity === s.entity : x.metric === s.metric) === i).slice(0, 4))
const point = (s: ComputeSeries) => s.points[s.points.length - 1]!
async function chooseGroupedCompany(id: string) {
  company.value = id
  if (companyDetails.value) companyDetails.value.open = false
  await nextTick()
  companyRail.value?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')?.focus()
}
</script>

<template>
  <section :id="'compute-' + panel.id" class="compute-section" :aria-labelledby="'compute-title-' + panel.id" tabindex="-1">
    <header class="compute-section-header">
      <h2 :id="'compute-title-' + panel.id">{{ panel.title }}</h2>
      <p class="compute-section-question">{{ panel.question }}</p>
      <p v-if="analysis" class="compute-section-finding">
        <span>板块判断</span> {{ analysis.headline }}
        <small v-if="analysisStale">待复核</small>
      </p>
    </header>
    <section v-if="!isRental" class="compute-companies" :aria-label="panel.title + '观察公司'">
      <p class="compute-control-label">{{ moreCompanies.length ? '常看公司' : '观察公司' }}</p>
      <div ref="companyRail" class="compute-picks" role="group" :aria-label="moreCompanies.length ? '常看公司' : '观察公司'">
        <button v-for="e in quickCompanies" :key="e" type="button" :aria-pressed="selected === e" :disabled="!available.has(e)" @click="company = e">
          {{ entities[e]?.name ?? e }}<small v-if="!available.has(e)">无连续数据</small>
        </button>
      </div>
      <details v-if="moreCompanies.length" ref="companyDetails" class="compute-company-details">
        <summary>更多公司（{{ panel.entities.length - featured.length }} 家）</summary>
        <div v-for="[group, ids] in moreCompanies" :key="group" class="compute-company-group">
          <span>{{ group }}</span>
          <div class="compute-picks" role="group" :aria-label="group">
            <button v-for="e in ids" :key="e" type="button" :aria-pressed="selected === e" :disabled="!available.has(e)" @click="chooseGroupedCompany(e)">
              {{ entities[e]?.name ?? e }}<small v-if="!available.has(e)">无连续数据</small>
            </button>
          </div>
        </div>
      </details>
    </section>
    <div class="compute-workspace">
      <article v-if="activeChart" class="compute-chart">
        <header class="compute-chart-heading">
          <h3 aria-live="polite"><template v-if="!isRental && selected">{{ entities[selected]?.name ?? selected }} · </template>{{ activeChart.title }}</h3>
        </header>
        <div v-if="charts.length > 1" class="compute-picks compute-chart-picks" role="group" aria-label="切换图表">
          <button v-for="chart in charts" :key="chart.title" type="button" :aria-pressed="activeChart.title === chart.title" @click="chartChoice = chart.title">{{ chart.title }}</button>
        </div>
        <p v-if="panel.id === 'demand' && activeChart.title === primaryChart" class="compute-attribution">现金资本开支看本季实际付了多少钱；公司披露口径可能计入租赁。两种口径不相加，也不等同于纯 AI 开支。</p>
        <div v-if="readings.length" class="compute-readings" aria-label="当前图表最新读数">
          <div v-for="s in readings" :key="s.id" class="compute-reading">
            <span class="compute-reading-label">{{ isRental ? entities[s.entity]?.name : metrics[s.metric]?.label }}<ChartTip :text="metrics[s.metric]?.description ?? ''" /></span>
            <button type="button" class="compute-reading-trigger" :aria-label="'查看' + (isRental ? entities[s.entity]?.name : metrics[s.metric]?.label) + '来源'" @click="emit('source', s)">
              <span class="compute-reading-value tnum">{{ formatCompute(point(s).value, s.unit, s.currency) }}</span>
              <span class="compute-reading-date">{{ point(s).period_end }}{{ s.stale ? ' · 更新滞后' : '' }}</span>
            </button>
          </div>
        </div>
        <template v-if="chartOption">
          <EChart :option="chartOption" height="310px" :not-merge="true" />
          <div class="compute-chart-source" aria-label="查看图表来源">
            <button v-for="s in activeChart.series" :key="s.id" type="button" @click="emit('source', s)">{{ isRental ? entities[s.entity]?.name : metrics[s.metric]?.label }} · {{ s.points.length }} 期 · 来源</button>
          </div>
        </template>
        <p v-else class="compute-empty">{{ panel.id === 'demand'
          ? '暂未取得这家公司的可比单季资本开支。租赁、指引和全年金额不用于补齐。'
          : '暂未取得这家公司的可比订单序列。已核实的交付窗口见板块解读；月营收不代替订单。' }}</p>
        <p v-if="activeChart.series.some(s => s.metric === 'gpu_hour_price')" class="compute-attribution"><a href="https://getdeploying.com/dataset/gpu-prices" target="_blank" rel="noopener noreferrer" class="compute-link">GetDeploying</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer" class="compute-link">CC BY 4.0</a> · 同型号按需挂牌周中位数，非成交价；本周暂定。</p>
        <p v-if="activeChart.series.some(s => s.metric === 'gpu_rental_composite')" class="compute-attribution"><a href="https://gpu-index.semianalysis.com/" target="_blank" rel="noopener noreferrer" class="compute-link">SemiAnalysis Spot-Contract Composite</a> · 部分历史可能重建或修订，不与挂牌周价拼接。</p>
      </article>
      <p v-else class="compute-empty">本板块暂未取得可比的连续数据。</p>
      <aside class="compute-analysis" :aria-label="panel.title + '板块解读'">
        <h3>{{ analysis ? '下一步看什么' : '板块解读' }}</h3>
        <p v-if="analysis">{{ analysis.watch }}</p>
        <p v-else>板块解读待本地 Agent 更新。</p>
        <details class="compute-analysis-evidence">
          <summary>{{ analysis ? '展开判断依据与口径' : '展开板块口径' }}</summary>
          <p v-if="analysis">{{ analysis.evidence }}</p>
          <p v-if="panel.caveat" class="compute-muted">{{ panel.caveat }}</p>
        </details>
      </aside>
    </div>
    <a class="compute-directory-link" href="#compute-directory">返回全景总览</a>
  </section>
</template>
