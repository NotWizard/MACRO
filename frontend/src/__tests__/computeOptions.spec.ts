import { describe, it, expect } from 'vitest'
import { chartPoints, computeOption, formatCompute, preferredSeries } from '@/components/charts/computeOptions'
import type { ComputeSeries, ComputePoint } from '@/api/compute'

const point = (date: string, value: number) => ({ period_end: date, value }) as ComputePoint
const series = (changes: Partial<ComputeSeries> = {}): ComputeSeries => ({ id: 's1', entity: 'NVDA', metric: 'revenue', kind: 'actual', basis: 'US-GAAP', scope: 'consolidated', currency: 'USD', unit: 'currency', frequency: 'quarterly', points: [point('2025-03-31', 1e9), point('2025-06-30', 2e9)], ...changes })
describe('compute chart semantics', () => {
  it('scales native currency only, retaining period end dates', () => {
    expect(chartPoints(series())).toEqual([['2025-03-31', 10], ['2025-06-30', 20]])
    expect(chartPoints(series({ unit: '%', points: [point('2025-03-31', -4)] }))[0][1]).toBe(-4)
  })
  it('breaks a missing quarter rather than connecting or inventing a point', () => {
    const values = chartPoints(series({ points: [point('2025-03-31', 1e9), point('2025-09-30', 2e9)] }))
    expect(values).toHaveLength(3)
    expect(values[1][1]).toBeNull()
  })
  it('does not backfill rental history; a snapshot remains one observation', () => {
    expect(chartPoints(series({ unit: 'USD/GPU-hour', frequency: 'daily', points: [point('2026-11-17', 2.5)] }))).toEqual([['2026-11-17', 2.5]])
  })
  it('separates units and visually distinguishes guidance', () => {
    const opt = computeOption([series(), series({ id: 's2', unit: '%', kind: 'guidance' })], {})
    expect(opt.yAxis).toHaveLength(2)
    const ss = opt.series as { connectNulls: boolean; yAxisIndex: number; lineStyle: { type: string } }[]
    expect(ss[0].connectNulls).toBe(false)
    expect(ss[1].lineStyle.type).toBe('dashed')
    expect(ss[1].yAxisIndex).toBe(1)
  })
  it('shows currency and units in every formatted value', () => {
    expect(formatCompute(1e9, 'currency', 'TWD')).toBe('10 亿新台币')
    expect(formatCompute(39.367e9, 'currency', 'USD')).toBe('393.67 亿美元')
    expect(formatCompute(2.5, 'currency/share', 'USD')).toBe('2.5 美元/股')
    expect(formatCompute(2.5, 'USD/GPU-hour')).toBe('2.5 美元/GPU时')
    expect(formatCompute(3, 'days')).toContain('天')
  })
  it('uses the same converted amount and currency in axes and hover values', () => {
    const opt = computeOption([series({ points: [point('2026-09-30', 39.367e9)] })], {})
    expect((opt.yAxis as { name: string }[])[0].name).toBe('亿美元')
    const line = (opt.series as { data: number[]; tooltip: { valueFormatter: (n: number) => string } }[])[0]
    expect(line.data[0]).toBe(393.67)
    expect(line.tooltip.valueFormatter(393.67)).toBe('393.67 亿美元')
  })
  it('does not mix annual EPS and quarterly EPS', () => {
    const q = series({ metric: 'eps', basis: 'TIFRS' })
    const annual = series({ metric: 'eps', basis: 'IFRS', frequency: 'annual' })
    expect(preferredSeries([q, annual])).toEqual([q])
  })
  it('labels the separate long-term benchmark and keeps dense daily lines legible', () => {
    const opt = computeOption([series({ entity: 'SEMIANALYSIS_H100', metric: 'gpu_rental_composite', unit: 'USD/GPU-hour', frequency: 'daily' })], {})
    const s = (opt.series as { name: string; showSymbol: boolean }[])[0]
    expect(s.name).toBe('H100')
    expect(s.showSymbol).toBe(false)
  })
  it('uses weekly gaps without treating real weekly history as isolated daily points', () => {
    const weekly = series({ frequency: 'weekly', unit: 'USD/GPU-hour', points: [point('2026-11-02', 2), point('2026-11-09', 3), point('2026-11-16', 4)] })
    expect(chartPoints(weekly)).toHaveLength(3)
    expect(chartPoints(weekly).every(p => p[1] !== null)).toBe(true)
  })
})
