import { describe, it, expect } from 'vitest'
import {
  buildCreditM2Chart, buildCreditImpulseChart, buildDualAxisLine,
  buildStackedArea, buildScatterQuadrant, buildRadar, buildBarLineCombo,
  buildMultiLine, buildSpreadChart,
} from '@/components/charts/options'
import { applyTheme } from '@/design/echarts.theme'
import type { LineSeriesOption } from 'echarts'

// options.ts 的行为断言（FE-L7 + 图例/参考线正确性）。这些是纯函数：records in →
// EChartsOption out；重点是每个 builder 的**语义**——series 数量、连接空值、以及
// FE-L7 里 markLine 必须挂到**每个 series**（挂 [0] 会在图例关掉第 0 条时把参考
// 线一起藏起来）。

const derivedMonthly = [
  { date: '2025-01-01', m2_yoy: 8.5, cpi_yoy: 0.5, ppi_yoy: -2, ip_yoy: 5, m2_m1_spread: 3, revenue_cum: 100, revenue_cum_yoy: 1 },
  { date: '2025-02-01', m2_yoy: null, cpi_yoy: 0.4, ppi_yoy: -1.8, ip_yoy: 4, m2_m1_spread: 2.9, revenue_cum: 200, revenue_cum_yoy: 2 },
  { date: '2025-03-01', m2_yoy: 8.7, cpi_yoy: 0.6, ppi_yoy: -1.5, ip_yoy: 6, m2_m1_spread: 3.1, revenue_cum: 300, revenue_cum_yoy: 3 },
]
const cycleCredit = [
  { date: '2025-01-01', phase: 'easing', m2_trend: 8.0, credit_impulse: 1.2 },
  { date: '2025-02-01', phase: 'easing', m2_trend: 8.1, credit_impulse: 0.9 },
  { date: '2025-03-01', phase: 'tightening', m2_trend: 8.2, credit_impulse: -0.3 },
]

describe('options: 结构与配色', () => {
  it('十字线数值最多显示两位小数，日期和整数保持原样', () => {
    const { formatter } = applyTheme({}).tooltip.axisPointer.label
    expect(formatter({ value: 670.2702702702703 })).toBe('670.27')
    expect(formatter({ value: -12.3456 })).toBe('-12.35')
    expect(formatter({ value: 600 })).toBe('600')
    expect(formatter({ value: '2020-10-16T00:00:00Z' })).toBe('2020-10-16')
  })

  it('buildCreditM2Chart：2 条 series + phaseBg（filter 掉 neutral）', () => {
    const opt = buildCreditM2Chart(derivedMonthly, cycleCredit)
    const series = opt.series as Array<{ name: string; markArea?: { data: unknown[] } }>
    expect(series).toHaveLength(2)
    expect(series[0].name).toBe('M2同比 (%)')
    expect(series[1].name).toBe('M2趋势 (%)')
    // easing / tightening 至少 1 段 phase 背景
    expect((series[0].markArea!.data as unknown[]).length).toBeGreaterThan(0)
  })

  it('buildStackedArea：series 数 = cols 数，全部 stack:"total"', () => {
    const cols = ['household', 'non_fin_corp', 'gov_total']
    const opt = buildStackedArea(derivedMonthly, cols) as { series: LineSeriesOption[] }
    expect(opt.series).toHaveLength(cols.length)
    for (const s of opt.series) {
      expect(s.stack).toBe('total')
      expect(s.type).toBe('line')
    }
  })

  it('buildMultiLine 无 markLineAt：series 不带 markLine', () => {
    const opt = buildMultiLine(derivedMonthly, [
      { col: 'cpi_yoy', name: 'CPI' }, { col: 'ppi_yoy', name: 'PPI' },
    ]) as { series: LineSeriesOption[] }
    for (const s of opt.series) expect(s.markLine).toBeUndefined()
  })

  it('FE-L7：markLineAt 存在时，参考线挂到每个 series（不是仅 [0]）', () => {
    const opt = buildMultiLine(derivedMonthly, [
      { col: 'ip_yoy', name: 'IP' }, { col: 'cpi_yoy', name: 'CPI' },
    ], '%', 50, '荣枯线') as { series: LineSeriesOption[] }
    expect(opt.series.length).toBe(2)
    for (const s of opt.series) {
      expect(s.markLine).toBeDefined()
      const data = (s.markLine as { data: Array<{ yAxis: number }> }).data
      expect(data[0].yAxis).toBe(50)
    }
  })

  it('buildScatterQuadrant：按 phase 分组 + hline/vline 各一条 markLine', () => {
    const cycle = [
      { date: '2025-01-01', phase: 'recovery', gdp_yoy: 5, cpi_yoy: 1 },
      { date: '2025-02-01', phase: 'recovery', gdp_yoy: 6, cpi_yoy: 1.5 },
      { date: '2025-03-01', phase: 'overheating', gdp_yoy: 7, cpi_yoy: 3 },
      { date: '2025-04-01', phase: 'recovery', gdp_yoy: null, cpi_yoy: 2 }, // 应被丢
    ]
    const opt = buildScatterQuadrant(cycle, 'gdp_yoy', 'cpi_yoy', 'GDP', 'CPI', 2, 5) as { series: Array<{ type: string; markLine?: { data: unknown[] } }> }
    const scatters = opt.series.filter((s) => s.type === 'scatter' && !s.markLine)
    expect(scatters.length).toBe(2)   // 2 个 phase
    const helper = opt.series.find((s) => s.markLine)!
    expect(helper.markLine!.data).toHaveLength(2)   // hline + vline
  })

  it('buildRadar：assessment 缺字段回退 0，不抛', () => {
    const opt = buildRadar({ leverage_space_score: 60 }) as { series: Array<{ data: Array<{ value: number[] }> }> }
    expect(opt.series[0].data[0].value).toEqual([60, 0, 0])
  })

  it('季度切线超出样本范围时，象限底色仍覆盖到坐标轴边缘', () => {
    const opt = buildScatterQuadrant([{ date: '2026-01-01', x: 4, y: 5 }], 'x', 'y', 'X', 'Y', 2, 0, {
      tl: 'stagflation', tr: 'overheating', bl: 'recession', br: 'recovery',
    }) as {
      xAxis: { min: number; max: number }; yAxis: { min: number; max: number }
      series: Array<{ markArea?: { data: Array<Array<{ coord: number[] }>> } }>
    }
    const areas = opt.series.at(-1)!.markArea!.data
    expect(areas).toHaveLength(4)
    expect(areas[1][0].coord).toEqual([opt.xAxis.min, 2])
    expect(areas[2][1].coord).toEqual([opt.xAxis.max, 2])
    expect(areas[3][0].coord).toEqual([opt.xAxis.min, opt.yAxis.min])
    expect(areas[0][1].coord).toEqual([opt.xAxis.max, opt.yAxis.max])
  })

  it('CPI/PPI 共用百分比轴：保留单边历史与内部空值，去掉尾部空月', () => {
    const opt = buildMultiLine([
      { date: '2026-08-01', cpi: 2, ppi: null },
      { date: '2026-09-01', cpi: null, ppi: -1 },
      { date: '2026-10-01', cpi: 3, ppi: -0.5 },
      { date: '2026-11-01', cpi: null, ppi: null },
    ], [{ col: 'cpi', name: 'CPI 同比' }, { col: 'ppi', name: 'PPI 同比' }], '%', 0) as {
      xAxis: { data: string[] }; yAxis: { name: string }; series: LineSeriesOption[]
      tooltip: { formatter: (items: unknown[]) => string }
    }
    expect(opt.xAxis.data).toEqual(['2026-08-01', '2026-09-01', '2026-10-01'])
    expect(opt.yAxis.name).toBe('%')
    expect(opt.series[0].data).toEqual([2, null, 3])
    expect(opt.series[0].connectNulls).toBe(false)
    const html = opt.tooltip.formatter(opt.series.map(({ name, data }) => ({ seriesName: name, value: data?.[0] })))
    expect(html).toContain('CPI 同比: <b>2.00%</b>')
    expect(html).toContain('PPI 同比: <b>—</b>')
  })

  it('buildDualAxisLine / buildBarLineCombo：两个 yAxis 两个 series', () => {
    const dual = buildDualAxisLine(derivedMonthly, 'cpi_yoy', 'ppi_yoy') as { yAxis: unknown[]; series: unknown[] }
    expect(dual.yAxis).toHaveLength(2); expect(dual.series).toHaveLength(2)
    const bl = buildBarLineCombo(derivedMonthly, 'revenue_cum', 'revenue_cum_yoy', '收入', '同比') as { yAxis: unknown[]; series: Array<{ type: string }> }
    expect(bl.yAxis).toHaveLength(2)
    expect(bl.series.map((s) => s.type)).toEqual(['bar', 'line'])
  })

  it('buildCreditImpulseChart / buildSpreadChart：单 series + 类型正确', () => {
    const imp = buildCreditImpulseChart(cycleCredit) as { series: Array<{ type: string }> }
    expect(imp.series).toHaveLength(1); expect(imp.series[0].type).toBe('bar')
    const sp = buildSpreadChart(derivedMonthly, 'm2_m1_spread') as { series: Array<{ type: string; markLine: { data: Array<{ yAxis: number }> } }> }
    expect(sp.series[0].type).toBe('line')
    expect(sp.series[0].markLine.data[0].yAxis).toBe(0)   // 默认零线
  })

  it('柱线图左右轴共用零点但保持独立量纲；负基数同比空值不连线', () => {
    const opt = buildBarLineCombo([
      { date: '2026-08', amount: 900, yoy: -50 },
      { date: '2026-09', amount: 400, yoy: 25 },
      { date: '2026-10', amount: -5846, yoy: null },
    ], 'amount', 'yoy', '贷款', '同比', '亿元', '%') as {
      yAxis: Array<{ min: number; max: number }>
      series: LineSeriesOption[]
    }
    const [left, right] = opt.yAxis
    expect(-left.min / (left.max - left.min)).toBeCloseTo(-right.min / (right.max - right.min))
    expect(left.min).toBeLessThanOrEqual(-5846)
    expect(opt.series[1]).toMatchObject({ name: '同比 (%)', data: [-50, 25, null], connectNulls: false })
  })
})
