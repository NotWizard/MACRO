import type { EChartsOption } from 'echarts'
import type { ComputeSeries, ComputeMetric } from '@/api/compute'
import { chartTheme } from '@/design/echarts.theme'

const currencies: Record<string, string> = { USD: '美元', TWD: '新台币', EUR: '欧元', KRW: '韩元' }
function unitLabel(unit: string, currency?: string | null): string {
  const money = currencies[currency ?? ''] ?? currency ?? ''
  return ({ currency: `亿${money}`, 'currency/share': `${money}/股`, 'USD/GPU-hour': '美元/GPU时', days: '天', ratio: '倍' } as Record<string, string>)[unit] ?? unit
}

export function formatCompute(value: number, unit: string, currency?: string | null): string {
  const scaled = value / (unit === 'currency' ? 1e8 : 1)
  return `${scaled.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${unit === '%' ? '' : ' '}${unitLabel(unit, currency)}`
}

export function chartPoints(series: ComputeSeries): [string, number | null][] {
  const result: [string, number | null][] = []
  let previous: string | undefined
  for (const point of series.points) {
    if (previous) {
      const days = (Date.parse(point.period_end) - Date.parse(previous)) / 86400000
      const limit = series.frequency === 'weekly' ? 10 : series.frequency === 'monthly' ? 45 : series.frequency === 'quarterly' ? 115 : series.frequency === 'daily' ? 2 : 410
      if (days > limit) result.push([new Date(Date.parse(previous) + 86400000).toISOString().slice(0, 10), null])
    }
    result.push([point.period_end, point.value / (series.unit === 'currency' ? 1e8 : 1)])
    previous = point.period_end
  }
  return result
}

export function computeOption(series: ComputeSeries[], metrics: Record<string, ComputeMetric>): EChartsOption {
  const theme = chartTheme()
  const t = { palette: theme.palette, textMuted: theme.colors.text3, border: theme.colors.grid }
  const units = [...new Set(series.map(s => `${s.unit}:${s.currency ?? ''}`))]
  const dates = [...new Set(series.flatMap(s => s.points.map(p => p.period_end)))]
  const singleDate = dates.length === 1
  return {
    animation: false,
    aria: { enabled: true },
    color: t.palette,
    tooltip: { trigger: 'axis', confine: true, renderMode: 'richText', valueFormatter: v => typeof v === 'number' ? v.toLocaleString('zh-CN', { maximumFractionDigits: 3 }) : String(v ?? '未披露') },
    legend: { type: 'plain', bottom: 0, itemGap: 10, textStyle: { color: t.textMuted, fontSize: 10 } },
    grid: { left: 14, right: 16, top: 30, bottom: 75, containLabel: true },
    xAxis: { type: singleDate ? 'category' : 'time', ...(singleDate ? { data: dates } : {}), axisLabel: { color: t.textMuted, fontSize: 10, hideOverlap: true }, axisLine: { lineStyle: { color: t.border } }, splitLine: { show: false } },
    yAxis: units.slice(0, 2).map((key, i) => {
      const [unit, currency] = key.split(':')
      return { type: 'value' as const, name: unitLabel(unit!, currency),
        position: i === 0 ? 'left' as const : 'right' as const, scale: true,
        nameTextStyle: { color: t.textMuted, fontSize: 10, align: 'left' }, axisLabel: { color: t.textMuted, fontSize: 10 },
        splitLine: { show: i === 0, lineStyle: { color: t.border, type: 'dashed' as const } } }
    }),
    series: series.map(s => ({ id: s.id, name: /^(GETDEPLOYING|SEMIANALYSIS)_/.test(s.entity) ? s.entity.replace(/^(GETDEPLOYING|SEMIANALYSIS)_/, '') : `${metrics[s.metric]?.label ?? s.metric}${shortScope(s)}${s.kind === 'actual' ? '' : s.kind === 'guidance' ? ' · 指引' : ' · 共识'}`,
      type: 'line' as const, showSymbol: s.frequency !== 'daily', symbolSize: 5, connectNulls: false,
      yAxisIndex: units.indexOf(`${s.unit}:${s.currency ?? ''}`),
      tooltip: { valueFormatter: v => typeof v === 'number' ? formatCompute(v * (s.unit === 'currency' ? 1e8 : 1), s.unit, s.currency) : '未披露' },
      lineStyle: { width: 2, type: s.kind === 'actual' ? 'solid' as const : 'dashed' as const },
      data: singleDate ? chartPoints(s).map(p => p[1]) : chartPoints(s),
    })),
  }
}

function shortScope(s: ComputeSeries): string {
  const scope = s.scope
  if (scope.includes('Electrical Americas')) return ` · 美洲${scope.includes('rolling') ? 'TTM' : '期末'}`
  if (scope.includes('Electrical Global')) return ` · 全球${scope.includes('rolling') ? 'TTM' : '期末'}`
  if (scope.includes('Combined Electrical')) return ' · 电气合计TTM'
  if (scope.includes('100% share')) return ' · 100%组合'
  if (s.metric === 'gpu_hour_price') return ''
  const basis = s.basis === 'non-GAAP' ? ' · non-GAAP' : ''
  if (scope === 'consolidated') return `${basis}${s.frequency === 'annual' ? ' · 全年' : ''}`
  const short = scope.split(/[;；]/)[0]!.trim()
  return `${basis} · ${short.length > 18 ? short.slice(0, 17) + '…' : short}`
}

export function preferredSeries(series: ComputeSeries[]): ComputeSeries[] {
  return series.filter(s => s.frequency !== 'annual' || !series.some(q => q.entity === s.entity && q.metric === s.metric && q.scope === s.scope && q.frequency === 'quarterly'))
}
