import { getJSON } from './client'

export interface ComputePoint {
  entity: string; metric: string; period_start: string | null; period_end: string
  value: number; kind: string; unit: string; currency: string | null; basis: string; scope: string
  frequency: string; source_url: string; source_urls?: string[]; source_locator: string
  source_excerpt: string; published_at: string; observed_at: string; method: string
  source_id: string; definition_version: string
}
export interface ComputeSeries {
  id: string; entity: string; metric: string; kind: string; unit: string; currency: string | null
  basis: string; scope: string; frequency: string; points: ComputePoint[]; stale?: boolean
}
export interface ComputeEntity { id: string; name: string; group: string; currency: string; note: string; tier: string }
export interface ComputeMetric { id: string; label: string; unit: string; description: string; derived: boolean }
export interface ComputePanel {
  id: string; title: string; question: string; entities: string[]; charts: [string, string[]][]
  caveat: string; as_of: string | null; series: ComputeSeries[]
}
export interface ComputeSource { id: string; name: string; method: string; url: string; cadence: string; history: string; status: string }
export interface ComputeInterpretation { headline: string; evidence: string; watch: string }
export interface ComputeOverview {
  as_of: string; observation_count: number; series_count: number; data_hash: string
  panels: ComputePanel[]; entities: Record<string, ComputeEntity>; metrics: Record<string, ComputeMetric>
  sources: ComputeSource[]
  expectation_vintages: ComputePoint[]
  analysis: { ts: string; model: string; stale: boolean; payload: { overall: string; panels: Record<string, ComputeInterpretation>; as_of: string; agent: string; summary: { id: string; headline: string; text: string }[]; sources: { title: string; url: string }[] } } | null
}
export const computeApi = {
  overview: (signal?: AbortSignal) => getJSON<ComputeOverview>('/compute/overview', { ttlMs: 0, signal }),
}
