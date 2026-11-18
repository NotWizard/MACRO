// 一次 GET /commentary，多卡共享同一响应（M4c 呈现层）。
// 模块级共享态，不进 pinia：单一消费者族、无跨组件写入（YAGNI 同 AISettings 注释先例）。
// 单飞 Promise 去重并发；每次进入都核验 CLI 等外部路径是否更改了数据。
import { ref } from 'vue'
import { api } from '@/api/client'
import type { Commentary } from '@/api/types'

const EMPTY: Commentary = {
  status: 'empty', msg: null, hint: null, stale: false, regenerating: false,
  overall: '', sections: {}, report: '', report_status: 'missing',
  report_msg: null, provenance: null,
}

const data = ref<Commentary>(EMPTY)   // 模块级：任意细分页共享同一响应
let inflight: Promise<void> | null = null
let subs = 0                          // 挂载中的消费者数 → 轮询启停
let pollTimer: ReturnType<typeof setInterval> | null = null

export function useCommentary() {
  async function load() {
    if (inflight) return inflight                       // 并发去重：共享同一请求
    inflight = (async () => {
      try { data.value = await api.getCommentary() }
      catch (e) {
        // error last-good：有文本 → 保留内容只记 msg；无文本 → 转 error 态
        const hasText = !!data.value.overall || Object.keys(data.value.sections).length > 0
        data.value = hasText
          ? { ...data.value, msg: (e as Error).message }
          : { ...data.value, status: 'error', msg: (e as Error).message }
      }
      inflight = null
      armPoll()
    })()
    return inflight
  }

  function armPoll() {                                  // generating → 2s 轮询（同 CommentaryCard 节奏）
    if (data.value.status === 'generating' && subs > 0 && !pollTimer) {
      pollTimer = setInterval(() => void load(), 2000)
    }
    if ((data.value.status !== 'generating' || subs <= 0) && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function enter() { subs++; void load() }              // onMounted
  function leave() { subs--; if (subs <= 0) armPoll() } // onUnmounted：订阅归零停轮询

  // Overview 的 CommentaryCard 有独立轮询，也同步到模块态。
  function adopt(c: Commentary) {
    data.value = c
  }

  return { data, enter, leave, adopt }
}
