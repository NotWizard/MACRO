<script setup lang="ts">
// AI 评论要点文本渲染：按行分段，「**核心判断**」渲染为 <strong>。
// 刻意不用 v-html——模型输出不可信，字符串拼接渲染是 XSS 面；
// 正则分段 + 文本插值则零风险。不成对的 ** 原样降级为普通文本。
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

interface Seg { bold: boolean; text: string }

const lines = computed<Seg[][]>(() =>
  props.text
    .split('\n')
    .map((line) => {
      // split 带捕获组：奇数索引 = **bold** 内容，偶数索引 = 普通文本
      const segs: Seg[] = []
      line.split(/\*\*([^*]+)\*\*/g).forEach((part, i) => {
        if (part) segs.push({ bold: i % 2 === 1, text: part })
      })
      return segs
    })
    .filter((l) => l.length > 0))   // 空行不占位
</script>

<template>
  <div>
    <p v-for="(line, i) in lines" :key="i" class="mb-1.5 last:mb-0">
      <template v-for="(seg, j) in line" :key="j">
        <strong v-if="seg.bold" class="text-text font-semibold">{{ seg.text }}</strong>
        <template v-else>{{ seg.text }}</template>
      </template>
    </p>
  </div>
</template>
