<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, shallowRef } from 'vue'
import { computeApi, type ComputeOverview, type ComputeSeries } from '@/api/compute'
import ComputePanel from '@/components/layout/ComputePanel.vue'
import { formatCompute } from '@/components/charts/computeOptions'

const data = shallowRef<ComputeOverview | null>(null)
const loading = ref(true)
const error = ref('')
const source = shallowRef<ComputeSeries | null>(null)
const drawer = ref<HTMLDialogElement>()
const workspace = ref<HTMLElement>()
const companies = reactive<Record<string, string | undefined>>({})
const chartChoices = reactive<Record<string, string | undefined>>({})
const controller = new AbortController()
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await computeApi.overview(controller.signal)
  } catch { if (!controller.signal.aborted) error.value = '数据暂时无法读取，请重试。' }
  finally { loading.value = false }
}
async function showSource(series: ComputeSeries) {
  source.value = series
  await nextTick()
  drawer.value?.showModal()
}
const report = computed(() => data.value?.analysis)
const summaryTitles: Record<string, string> = { earnings: '盈利现状', outlook: '盈利预期', conditions: '需求与交付' }
const summaryTargets: Record<string, string> = { earnings: 'earnings', outlook: 'earnings', conditions: 'demand' }
const tabLabels: Record<string, string> = {
  earnings: '盈利', demand: '客户投入', manufacturing: '制造交付',
  rental: '算力租价', power: '电力部署', quality: '库存质量',
}
const hash = ref(window.location.hash)
const activePanel = computed(() => data.value?.panels.find(p => hash.value === '#compute-' + p.id))
function scrollToWorkspace() { workspace.value?.scrollIntoView({ block: 'start' }) }
async function readHash() {
  hash.value = window.location.hash
  drawer.value?.close()
  await nextTick()
  if (!hash.value || hash.value.startsWith('#compute-')) scrollToWorkspace()
}
onMounted(async () => {
  window.addEventListener('hashchange', readHash)
  await load()
  await nextTick()
  if (hash.value.startsWith('#compute-')) scrollToWorkspace()
})
onUnmounted(() => {
  window.removeEventListener('hashchange', readHash)
  controller.abort()
  drawer.value?.close()
})
</script>

<template>
  <div class="compute-page">
    <header class="compute-page-header">
      <div><h1>AI 算力全景</h1><p class="compute-muted">跟踪盈利变化、资本投入和交付能否兑现</p></div>
    </header>
    <div v-if="error" role="alert" class="compute-empty">{{ error }} <button class="compute-link" @click="load">重新读取</button></div>
    <p v-if="loading" role="status" class="compute-empty">正在读取算力数据…</p>
    <div v-if="data" id="compute-directory" ref="workspace" class="compute-layout" tabindex="-1">
      <nav class="compute-nav" aria-label="算力研究板块">
        <ul>
          <li><a href="#compute-directory" :aria-current="!activePanel ? 'location' : undefined">经营总览</a></li>
          <li v-for="p in data.panels" :key="p.id">
            <a :href="'#compute-' + p.id" :aria-label="p.title" :aria-current="activePanel?.id === p.id ? 'location' : undefined">
              {{ tabLabels[p.id] ?? p.title }}
            </a>
          </li>
        </ul>
      </nav>
      <section v-if="!activePanel" class="compute-overview" aria-labelledby="compute-summary-title">
        <header class="compute-summary-header"><h2 id="compute-summary-title">经营判断</h2><span class="compute-muted">{{ report ? report.payload.as_of + ' · ' + report.payload.agent : '待本地 Agent 更新' }}</span></header>
        <template v-if="report">
          <p v-if="report.stale" class="compute-stale">这份解读之后有新数据入库，尚待本地 Agent 复核。</p>
          <p class="compute-overall">{{ report.payload.overall }}</p>
        </template>
        <p v-else class="compute-muted leading-7">结论区预留给本地 Agent。运行 ai-compute-collect skill 并发布报告后显示；页面本身不生成结论。</p>
        <template v-if="report">
          <div class="compute-summary-grid">
            <article v-for="item in report.payload.summary" :key="item.id">
              <a class="compute-summary-category" :href="'#compute-' + summaryTargets[item.id]">{{ summaryTitles[item.id] }}</a>
              <h3>{{ item.headline }}</h3><p>{{ item.text }}</p>
            </article>
          </div>
          <details v-if="report.payload.sources.length" class="compute-report-sources">
            <summary>核验原文（{{ report.payload.sources.length }}）</summary>
            <div class="compute-source-links"><a v-for="item in report.payload.sources" :key="item.url" :href="item.url" target="_blank" rel="noopener noreferrer" class="compute-link">{{ item.title }} ↗</a></div>
          </details>
        </template>
      </section>
      <ComputePanel v-else :key="activePanel.id" :panel="activePanel" :entities="data.entities" :metrics="data.metrics"
        v-model:company="companies[activePanel.id]" v-model:chart-choice="chartChoices[activePanel.id]"
        :analysis="report?.payload.panels[activePanel.id]" :analysis-stale="report?.stale" @source="showSource" />
      <details class="compute-footnotes">
        <summary>数据口径与覆盖</summary>
        <p class="compute-muted mt-3">{{ data.observation_count.toLocaleString() }} 项原始观察 · {{ data.as_of }} 可见信息。各公司财季不同；缺失值保留为空，不跨口径拼接。</p>
        <ul class="mt-3 space-y-2"><li v-for="s in data.sources" :key="s.id"><a :href="s.url" target="_blank" rel="noopener noreferrer" class="compute-link">{{ s.name }}</a><span class="compute-muted"> · {{ s.history }}</span></li></ul>
      </details>
    </div>
    <dialog ref="drawer" class="compute-dialog" aria-labelledby="compute-source-title">
      <header><h2 id="compute-source-title">原始数据与口径</h2><button class="compute-close" autofocus @click="drawer?.close()">关闭 ×</button></header>
      <template v-if="source && data">
        <h3 class="font-semibold mt-5">{{ data.entities[source.entity]?.name }} · {{ data.metrics[source.metric]?.label }}</h3>
        <p class="compute-muted mt-2 leading-6">{{ data.metrics[source.metric]?.description }}<br>{{ source.scope }} · {{ source.basis }}</p>
        <article v-for="p in source.points.slice().reverse()" :key="p.period_end" class="compute-source-row">
          <p class="font-medium">{{ p.period_end }} · {{ formatCompute(p.value, p.unit, p.currency) }}</p>
          <p class="compute-muted">发布 {{ p.published_at.slice(0, 10) }} · {{ p.frequency }} · {{ p.method }}</p>
          <p class="compute-muted break-words">{{ p.source_locator }}</p><p class="compute-muted">{{ p.source_excerpt }}</p>
          <a v-for="url in p.source_urls ?? [p.source_url]" :key="url" :href="url" target="_blank" rel="noopener noreferrer" class="compute-link block break-all">{{ url }} ↗</a>
        </article>
      </template>
    </dialog>
  </div>
</template>

<style>
.compute-page { max-width: 1680px; margin: 0 auto; padding: 28px; color: var(--text); }
.compute-page-header, .compute-summary-header { display: flex; align-items: center; justify-content: space-between; gap: 12px 20px; flex-wrap: wrap; }
.compute-page-header { margin-bottom: 16px; }
.compute-page h1 { font-size: 24px; font-weight: 700; }
.compute-page h2 { font-size: 17px; font-weight: 650; }
.compute-page h3 { font-size: 14px; font-weight: 600; }
.compute-muted { color: var(--text-3); font-size: 12px; }
.compute-page-header p { margin-top: 4px; }
.compute-overview { margin-bottom: 20px; padding: 22px 24px; border: 1px solid var(--border); background: var(--card); border-radius: 12px; }
.compute-overall { margin-top: 12px; font-size: 16px; font-weight: 550; line-height: 1.8; white-space: pre-wrap; max-width: 76ch; }
.compute-summary-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 20px; margin-top: 18px; }
.compute-summary-grid article { border-top: 1px solid var(--border); padding-top: 12px; }
.compute-summary-category { color: var(--accent); font-size: 12px; text-decoration: underline; text-underline-offset: 3px; }
.compute-summary-grid h3 { margin-top: 6px; line-height: 1.55; }
.compute-summary-grid p { font-size: 13px; line-height: 1.75; margin-top: 6px; color: var(--text-2); }
.compute-stale { font-size: 13px; color: var(--warn); margin-top: 10px; }
.compute-report-sources { margin-top: 14px; color: var(--text-2); font-size: 12px; }
.compute-report-sources summary { width: fit-content; cursor: pointer; min-height: 32px; line-height: 32px; }
.compute-source-links { display: flex; flex-wrap: wrap; gap: 8px 16px; padding-top: 6px; }
.compute-layout { scroll-margin-top: 60px; }
.compute-nav { position: sticky; top: 52px; z-index: 30; margin-bottom: 20px; padding: 5px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.compute-nav ul { display: grid; grid-template-columns: repeat(7,minmax(0,1fr)); gap: 4px; }
.compute-nav a {
  display: flex; align-items: center; justify-content: center; padding: 6px; height: 100%; min-height: 44px;
  border: 1px solid transparent; border-radius: 7px; text-align: center;
  color: var(--text-2); font-size: 13px; font-weight: 600; line-height: 1.45; transition: border-color .15s, background-color .15s;
}
.compute-nav a:hover, .compute-picks button:not(:disabled):hover { border-color: var(--accent); }
.compute-nav a[aria-current], .compute-picks button[aria-pressed=true] { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.compute-page :is(a, button, summary):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; scroll-margin-top: 124px; }
.compute-section { margin-bottom: 12px; scroll-margin-top: 124px; }
.compute-section-header { margin-bottom: 14px; }
.compute-section-question { margin-top: 4px; font-size: 14px; color: var(--text-2); }
.compute-section-finding { margin-top: 8px; font-size: 14px; line-height: 1.6; font-weight: 600; }
.compute-section-finding span { color: var(--text-3); font-size: 12px; font-weight: 500; margin-right: 5px; }
.compute-section-finding small { color: var(--warn); font-size: 11px; margin-left: 4px; }
.compute-directory-link { min-height: 44px; display: inline-flex; align-items: center; color: var(--accent); font-size: 12px; text-decoration: underline; text-underline-offset: 3px; }
.compute-companies { margin-bottom: 14px; }
.compute-control-label { margin-bottom: 6px; font-size: 12px; font-weight: 550; color: var(--text-3); }
.compute-picks { display: flex; flex-wrap: wrap; gap: 6px; }
.compute-picks button { min-height: 44px; color: var(--text-2); font-size: 13px; text-align: left; padding: 5px 12px; border: 1px solid var(--border); border-radius: 7px; background: var(--card); }
.compute-picks button:disabled { color: var(--text-3); cursor: not-allowed; border-style: dashed; }
.compute-picks button small { display: block; font-size: 10px; }
.compute-company-details summary, .compute-analysis-evidence summary { width: fit-content; min-height: 44px; line-height: 44px; color: var(--accent); font-size: 12px; cursor: pointer; }
.compute-company-details[open] { margin-top: 4px; padding: 0 12px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.compute-company-group { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px 12px; margin-top: 6px; }
.compute-company-group > span { min-width: 80px; font-size: 12px; color: var(--text-3); }
.compute-workspace { display: grid; grid-template-columns: minmax(0,1fr) minmax(220px, 27%); gap: 14px; align-items: start; }
.compute-readings { display: grid; grid-template-columns: repeat(auto-fit,minmax(min(100%, 138px),1fr)); gap: 6px 14px; margin: 0 0 4px; padding: 8px 0 2px; }
.compute-reading { min-width: 0; }
.compute-reading-label { display: block; color: var(--text-2); font-size: 12px; }
.compute-reading-trigger { text-align: left; min-height: 44px; }
.compute-reading-trigger:hover .compute-reading-value { color: var(--accent); }
.compute-reading-value { display: block; margin-top: 4px; font-size: 18px; font-weight: 650; }
.compute-reading-date { display: block; margin-top: 3px; font-size: 12px; color: var(--text-3); }
.compute-chart { min-width: 0; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px 8px; }
.compute-chart-heading { margin-bottom: 8px; }
.compute-chart-heading h3 { font-size: 15px; }
.compute-chart-picks { margin-bottom: 4px; }
.compute-chart-source { display: flex; flex-wrap: wrap; gap: 0 14px; }
.compute-chart-source button { min-height: 44px; font-size: 12px; color: var(--text-2); text-align: left; text-decoration: underline; text-decoration-color: var(--text-3); text-underline-offset: 3px; }
.compute-chart-source button:hover { color: var(--accent); }
.compute-analysis { min-width: 0; padding: 14px 16px 6px; border-radius: 12px; border: 1px solid var(--border); background: var(--surface); color: var(--text-2); }
.compute-analysis h3 { font-size: 15px; line-height: 1.55; color: var(--text); }
.compute-analysis p { margin-top: 6px; font-size: 13px; line-height: 1.8; white-space: pre-wrap; max-width: 72ch; }
.compute-analysis-evidence { margin-top: 8px; border-top: 1px solid var(--border); }
.compute-analysis-evidence[open] { padding-bottom: 8px; }
.compute-footnotes { border-top: 1px solid var(--border); padding-top: 10px; font-size: 13px; color: var(--text-2); }
.compute-footnotes summary { cursor: pointer; width: fit-content; min-height: 44px; line-height: 44px; }
.compute-link { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.compute-empty { font-size: 13px; padding: 18px 0; color: var(--text-2); }
.compute-dialog { position: fixed; inset: 0; margin: 0 0 0 auto; width: min(780px,100vw); max-width: 100vw; height: 100dvh; max-height: 100dvh; background: var(--card); color: var(--text); padding: 20px; border-left: 1px solid var(--border); overflow-y: auto; }
.compute-dialog::backdrop { background: rgb(0 0 0 / .35); }
.compute-dialog header { display: flex; justify-content: space-between; align-items: center; gap: 12px; background: var(--card); position: sticky; top: -20px; padding: 12px 0; }
.compute-close { min-height: 44px; font-size: 13px; color: var(--text-2); padding: 6px 12px; border: 1px solid var(--border); border-radius: 7px; }
.compute-source-row { padding: 14px 0; border-top: 1px solid var(--border); font-size: 13px; line-height: 1.75; }
.compute-attribution { font-size: 12px; line-height: 1.65; color: var(--text-2); margin: 4px 0 6px; }
@media(max-width: 1050px) { .compute-workspace { grid-template-columns: minmax(0,1fr); } }
@media(max-width: 870px) {
  .compute-nav { position: static; }
  .compute-nav ul { grid-template-columns: repeat(4,minmax(0,1fr)); }
  .compute-section, .compute-page :is(a, button, summary):focus-visible { scroll-margin-top: 60px; }
}
@media(max-width: 700px) {
  .compute-page { padding: 16px 12px; }
  .compute-overview { padding: 16px; }
  .compute-summary-grid { grid-template-columns: minmax(0,1fr); gap: 12px; }
  .compute-nav a { padding: 5px 2px; font-size: 12px; }
  .compute-picks button { padding-inline: 8px; font-size: 12px; }
  .compute-readings { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }
  .compute-reading-value, .compute-overall { font-size: 15px; }
  .compute-analysis, .compute-chart { padding: 12px; }
}
@media (prefers-reduced-motion: reduce) { .compute-nav a { transition: none; } }
</style>
