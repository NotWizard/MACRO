<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useCommentary } from '@/composables/useCommentary'
import ReportMarkdown from '@/components/layout/ReportMarkdown.vue'

const router = useRouter()
const { data, enter, leave } = useCommentary()
const titleRef = ref<HTMLElement | null>(null)

const reportReady = computed(() => !!data.value.report)
const generatedAt = computed(() => data.value.provenance?.generated_at ?? '—')
const dataAsOf = computed(() => data.value.provenance?.data_as_of?.macro_score ?? '—')

function close() {
  router.replace('/overview')
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

onMounted(async () => {
  enter()
  document.addEventListener('keydown', onKeydown)
  await nextTick()
  titleRef.value?.focus()
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  leave()
})
</script>

<template>
  <div class="min-h-full bg-bg">
    <header class="sticky top-0 z-30 border-b border-border bg-bg/95 backdrop-blur px-6 py-3">
      <div class="max-w-[980px] mx-auto flex items-center gap-4">
        <div class="min-w-0 flex-1">
          <h1 ref="titleRef" tabindex="-1" class="mt-0.5 text-base font-semibold text-text outline-none">
            {{ data.stale ? '中国宏观分析报告 · 历史版本' : '中国宏观分析报告' }}
          </h1>
        </div>
        <div class="hidden sm:flex items-center gap-3 text-[11px] text-text-3 tnum">
          <span>生成 {{ generatedAt }}</span>
          <span>评分基准月 {{ dataAsOf }}</span>
          <span v-if="data.provenance?.model">{{ data.provenance.model }}</span>
        </div>
        <button
          class="shrink-0 w-8 h-8 rounded-lg border border-border text-text-2 hover:text-text hover:border-border-hi focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          aria-label="关闭完整报告并返回综合概览"
          @click="close"
        >×</button>
      </div>
    </header>

    <main class="px-6 py-8">
      <div class="max-w-[900px] mx-auto">
        <div v-if="data.status === 'generating' && !reportReady" class="bg-card border border-border rounded-2xl p-8 text-sm text-text-2 animate-pulse">
          {{ data.msg || '完整报告生成中…' }}
        </div>
        <div v-else-if="data.status === 'empty'" class="bg-card border border-border rounded-2xl p-8">
          <div class="text-sm text-text-2">{{ data.msg || '暂无完整报告' }}</div>
          <RouterLink v-if="data.hint" :to="data.hint" class="inline-block mt-3 text-xs text-accent">前往 AI 设置</RouterLink>
        </div>
        <div v-else-if="data.status === 'error'" class="bg-card border border-border rounded-2xl p-8">
          <div class="text-sm text-down">{{ data.msg || '完整报告加载失败' }}</div>
          <button class="mt-3 text-xs text-accent" @click="close">返回综合概览</button>
        </div>
        <div v-else-if="!reportReady" class="bg-card border border-border rounded-2xl p-8">
          <div class="text-sm text-text-2">{{ data.report_msg || '本批次暂无完整报告' }}</div>
          <p class="mt-2 text-xs text-text-3">返回综合概览后重新分析，系统会同时生成首页摘要和完整报告。</p>
          <button class="mt-4 text-xs px-3 py-1.5 rounded-lg border border-border text-text-2" @click="close">返回综合概览</button>
        </div>
        <template v-else>
          <div v-if="data.status === 'generating'" class="mb-5 text-xs px-2.5 py-1 rounded bg-accent-soft text-accent">
            新版本正在生成，当前显示上一版报告。
          </div>
          <div v-if="data.stale" class="mb-5 text-sm px-3 py-2 rounded-lg border border-border bg-warn-soft text-warn" role="status">
            这份报告与当前数据或配置不一致，仅供回看。评分基准月：{{ dataAsOf }}。
          </div>
          <div class="bg-card border border-border rounded-2xl px-8 py-9 sm:px-12 sm:py-11">
            <ReportMarkdown :text="data.report" />
          </div>
          <div class="mt-4 flex justify-end">
            <button class="text-xs px-3 py-1.5 rounded-lg border border-border text-text-2 hover:border-border-hi" @click="close">
              关闭报告
            </button>
          </div>
        </template>
      </div>
    </main>
  </div>
</template>
