export type CStatus = 'NEEDS_CLARIFICATION' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface RegionInfo {
  region_id: string
  town: string
  role?: string
  data_version: string
  data_status: string
  release_status: string
  demand_nodes: number | null
  shelters: number | null
  od_available: boolean
  blockers?: string[]
  warnings?: string[]
}

export interface CoverageStat {
  threshold_min: number
  demand_nodes: number
  covered_nodes: number
  uncovered_nodes: number
  population_with_candidate: number
  population_total: number
  population_share: number
  interpretation: string
}

export interface LayerInfo {
  layer_id: string
  description: string
  geometry_type: string
  format: string
  fields: string[]
  available: boolean
  unavailable_reason?: string
}

export interface RegionMetadata {
  region_id: string
  town: string
  role: string
  data_version: string
  release_status: string
  data_status: string
  demand_nodes: number
  shelters: number
  od_rows: number
  reachable_pairs: number
  population_estimate: number
  coverage: Record<string, CoverageStat>
  quality: {
    real_pilot_accepted: boolean
    verdict: string
    unresolved_shelters: number
    boundary_status?: string
    blockers: string[]
  }
  layers: LayerInfo[]
}

export interface TurnEnvelope {
  turn_id: string
  session_id: string
  status: CStatus
  user_text?: string
  created_at?: string
  updated_at?: string
  region_id?: string
  task_type?: string | null
  result_id?: string | null
  data_version?: string
  data_status?: string
  metrics?: Record<string, unknown>
  map?: unknown
  warnings?: string[]
  message?: string | null
  clarification?: { fields?: string[]; question?: string; pending_task_type?: string; resume_hint?: string } | null
  error?: { code?: string; message?: string; retryable?: boolean } | string | null
  timings?: Record<string, number | null>
  llm?: { calls?: number; prompt_tokens?: number; completion_tokens?: number; mode?: string; fallback_used?: boolean }
  cancel_accepted?: boolean
  note?: string
}

export interface SessionEnvelope {
  session_id: string
  status: string
  region_id: string
  data_version: string
  data_status: string
  turns?: Array<{ turn_id: string; status: CStatus; created_at?: string }>
}

export interface AssignmentRow {
  shelter_id: string
  [key: string]: unknown
}

export interface ResultLayer {
  layer_id: string
  variant: string
  kind: string
  rows?: AssignmentRow[]
  geometry?: unknown
  note?: string
}

export interface ResultLayers {
  result_id: string
  variant: string
  layers: ResultLayer[]
  deficits: { count: number; sample: unknown[] }
  data_version: string
  data_status: string
}

export interface EvidencePage {
  section: string
  offset: number
  limit: number
  total: number
  has_more: boolean
  items: unknown[]
}

export interface CCapabilities {
  statuses: CStatus[]
  task_types?: string[]
  b_service_status?: string
  data_version?: string
  data_status?: string
  region_mode?: string
  limits?: { turn_timeout_s?: number; poll_interval_hint_ms?: number; max_text_chars?: number }
  disclosure?: string
}

export interface LayerCatalog {
  region_id: string
  data_version: string
  data_status: string
  release_status: string
  layers: Array<Record<string, unknown>>
}
