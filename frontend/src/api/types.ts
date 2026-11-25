export type CStatus = 'NEEDS_CLARIFICATION' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export type TaskMode = 'assess' | 'stress' | 'optimize' | 'compare'
export type BasemapStyle = 'amap_light' | 'arcgis_street' | 'tianditu' | 'local_vector'

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

export interface ShelterRecord {
  shelter_id: string
  name: string
  address: string
  community: string
  capacity: number
  area_sqm: number | null
  phone: string | null
  capacity_level: string
  lon: number
  lat: number
}

export interface GreedyComparison {
  status?: string
  greedy_served_demand?: number
  mip_served_demand?: number
  served_demand_gap?: number
  greedy_selected_option_ids?: string[]
  mip_selected_option_ids?: string[]
  [key: string]: unknown
}

export interface MonotonicityCheck {
  status?: string
  parameter?: string
  direction?: string
  checked_pairs?: number
  violations?: number
  [key: string]: unknown
}

export interface CompositeStage {
  stage_id: string
  status: string
  reason_code?: string | null
  scenario_id?: string | null
  parent_scenario_id?: string | null
  result_id?: string | null
  skill_ids?: string[]
  solver_status?: string | null
  verification_status?: string | null
  metrics?: Record<string, unknown>
  duration_ms?: number | null
}

export interface CompositeComparison {
  baseline?: { served_demand?: number; total_demand?: number; result_id?: string }
  damaged?: { served_demand?: number; total_demand?: number; result_id?: string }
  recovered?: { optimized_served_demand?: number; total_demand?: number; selected_option_ids?: string[]; total_cost_units?: number; budget_units?: number; result_id?: string }
  damage_service_loss?: number
  recovery_service_gain?: number
  recovery_ratio?: { status?: string; value?: number | null; reason_code?: string }
  budget_units?: number | null
  selected_option_ids?: string[]
  greedy_comparison?: GreedyComparison | null
}

export interface CompositeSummary {
  status?: string
  user_goal?: string
  region_id?: string
  data_version?: string
  stages?: CompositeStage[]
  comparison?: CompositeComparison | null
  branch_decisions?: Array<Record<string, unknown>>
  constraint_checks?: Array<{ check_id?: string; passed?: boolean; observed?: unknown; expected?: unknown }>
  verification_results?: Array<{ stage_id?: string; status?: string; passed?: boolean }>
  execution_trace_id?: string
}

export interface TurnEnvelope {
  turn_id: string
  session_id: string
  status: CStatus
  user_text?: string
  created_at?: string
  updated_at?: string
  region_id?: string
  intent?: string | null
  task_type?: string | null
  result_id?: string | null
  data_version?: string
  data_status?: string
  metrics?: Record<string, unknown>
  greedy_comparison?: GreedyComparison | null
  monotonicity_check?: MonotonicityCheck | null
  composite?: CompositeSummary | null
  map?: unknown
  warnings?: string[]
  message?: string | null
  clarification?: { fields?: string[]; question?: string; pending_task_type?: string; resume_hint?: string } | null
  error?: { code?: string; message?: string; retryable?: boolean } | string | null
  timings?: Record<string, number | null>
  llm?: {
    calls?: number
    prompt_tokens?: number
    completion_tokens?: number
    mode?: string
    extract_modes?: string[]
    fallback_used?: boolean
  }
  cancel_accepted?: boolean
  resumed_from_clarification?: boolean
  note?: string
}

export interface SessionEnvelope {
  session_id: string
  status?: string
  region_id: string
  data_version: string
  data_status: string
  params?: Record<string, unknown> | null
  scenario_id?: string | null
  result_ids?: string[]
  turns?: Array<{ turn_id: string; status: CStatus; created_at?: string }>
}

export interface AssignmentRow {
  shelter_id: string
  load_persons?: number
  utilization?: number
  capacity_persons?: number
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

export interface OptimizationCandidate {
  id: string
  shelterId: string
  capacity: string
  cost: string
}
