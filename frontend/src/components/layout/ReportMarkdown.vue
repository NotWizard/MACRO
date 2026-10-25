<script setup lang="ts">
// Safe, deliberately small Markdown renderer for the AI report.
// Supports headings, paragraphs, unordered/ordered lists; inline **bold** is
// delegated to MdLiteText. No v-html and no arbitrary HTML execution.
import { computed } from 'vue'
import MdLiteText from './MdLiteText.vue'

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }

const props = defineProps<{ text: string }>()

const blocks = computed<Block[]>(() => {
  const out: Block[] = []
  let paragraph: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flush = () => {
    if (paragraph.length) {
      out.push({ type: 'paragraph', text: paragraph.join(' ') })
      paragraph = []
    }
    if (list?.items.length) out.push({ type: 'list', ...list })
    list = null
  }

  for (const raw of props.text.replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.trim()
    if (!line) { flush(); continue }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line)
    if (heading) {
      flush()
      out.push({ type: 'heading', level: heading[1].length, text: heading[2] })
      continue
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line)
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line)
    if (bullet || numbered) {
      if (paragraph.length) {
        out.push({ type: 'paragraph', text: paragraph.join(' ') })
        paragraph = []
      }
      const ordered = !!numbered
      if (!list || list.ordered !== ordered) {
        if (list?.items.length) out.push({ type: 'list', ...list })
        list = { ordered, items: [] }
      }
      list.items.push((bullet?.[1] ?? numbered?.[1])!)
      continue
    }
    if (list?.items.length) {
      out.push({ type: 'list', ...list })
      list = null
    }
    paragraph.push(line)
  }
  flush()
  return out
})
</script>

<template>
  <article class="text-[15px] leading-8 text-text-2">
    <template v-for="(b, i) in blocks" :key="i">
      <h2 v-if="b.type === 'heading' && b.level <= 2" :id="`report-${i}`"
        class="mt-9 mb-3 first:mt-0 text-xl font-bold tracking-tight text-text">
        {{ b.text }}
      </h2>
      <h3 v-else-if="b.type === 'heading' && b.level === 3" :id="`report-${i}`"
        class="mt-7 mb-2 text-base font-semibold text-text">
        {{ b.text }}
      </h3>
      <h4 v-else-if="b.type === 'heading'" :id="`report-${i}`"
        class="mt-5 mb-2 text-sm font-semibold text-text">
        {{ b.text }}
      </h4>
      <div v-else-if="b.type === 'paragraph'" class="my-3">
        <MdLiteText :text="b.text" />
      </div>
      <ul v-else-if="b.type === 'list' && !b.ordered" class="my-3 pl-5 list-disc space-y-1.5">
        <li v-for="(item, j) in b.items" :key="j"><MdLiteText :text="item" /></li>
      </ul>
      <ol v-else-if="b.type === 'list'" class="my-3 pl-6 list-decimal space-y-1.5">
        <li v-for="(item, j) in b.items" :key="j"><MdLiteText :text="item" /></li>
      </ol>
    </template>
  </article>
</template>
