import type {
  CCapabilities, EvidencePage, LayerCatalog, RegionInfo, RegionMetadata,
  ResultLayers, SessionEnvelope, TurnEnvelope,
} from './types'
import type { FeatureCollection } from 'geojson'

const C_BASE = '/api/c'
const ASSET_BASE = '/__a/v2'

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { ...(init?.body ? { 'content-type': 'application/json' } : {}), ...init?.headers },
  })
  const body = await response.json().catch(() => ({})) as Record<string, unknown>
  if (!response.ok) {
    const detail = body.detail
    const message = typeof detail === 'string' ? detail : typeof body.error === 'string' ? body.error : `请求失败 (${response.status})`
    throw new ApiError(message, response.status, typeof body.code === 'string' ? body.code : undefined)
  }
  return body as T
}

export const api = {
  health: () => request<{ status: string; service: string; arm?: string; dialogue_enabled?: boolean; data_status?: string; data_version?: string }>(`${C_BASE}/healthz`),
  capabilities: () => request<CCapabilities>(`${C_BASE}/v1/capabilities`),
  regions: () => request<{ source_mode: string; regions: RegionInfo[] }>(`${C_BASE}/v1/regions`),
  localRegions: () => request<{ regions: RegionMetadata[] }>(`${ASSET_BASE}/regions`),
  layers: (regionId: string) => request<LayerCatalog>(`${C_BASE}/v1/regions/${encodeURIComponent(regionId)}/layers`),
  geoJson: async (regionId: string, layerId: string): Promise<FeatureCollection> => {
    const response = await fetch(`${ASSET_BASE}/layers/${encodeURIComponent(regionId)}/${encodeURIComponent(layerId)}`)
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { error?: string }
      throw new ApiError(body.error ?? `图层加载失败 (${response.status})`, response.status)
    }
    return response.json() as Promise<FeatureCollection>
  },
  createSession: (regionId: string) => request<SessionEnvelope>(`${C_BASE}/v1/sessions`, {
    method: 'POST', body: JSON.stringify({ region_id: regionId }),
  }),
  getSession: (sessionId: string) => request<SessionEnvelope>(`${C_BASE}/v1/sessions/${encodeURIComponent(sessionId)}`),
  resetSession: (sessionId: string) => request<SessionEnvelope>(`${C_BASE}/v1/sessions/${encodeURIComponent(sessionId)}/reset`, { method: 'POST' }),
  submitTurn: (sessionId: string, text: string, clientTurnId: string) => request<TurnEnvelope>(
    `${C_BASE}/v1/sessions/${encodeURIComponent(sessionId)}/turns`,
    { method: 'POST', body: JSON.stringify({ text, client_turn_id: clientTurnId }) },
  ),
  getTurn: (turnId: string) => request<TurnEnvelope>(`${C_BASE}/v1/turns/${encodeURIComponent(turnId)}`),
  cancelTurn: (turnId: string) => request<TurnEnvelope>(`${C_BASE}/v1/turns/${encodeURIComponent(turnId)}/cancel`, { method: 'POST' }),
  resultLayers: (sessionId: string, resultId: string, variant = 'auto') => request<ResultLayers>(
    `${C_BASE}/v1/results/${encodeURIComponent(resultId)}/layers?session_id=${encodeURIComponent(sessionId)}&variant=${encodeURIComponent(variant)}`,
  ),
  evidence: (sessionId: string, resultId: string, section: string, offset = 0, limit = 20) => request<EvidencePage>(
    `${C_BASE}/v1/results/${encodeURIComponent(resultId)}/evidence/${encodeURIComponent(section)}?session_id=${encodeURIComponent(sessionId)}&offset=${offset}&limit=${limit}`,
  ),
}

export const C_FINAL_STATUSES = new Set(['NEEDS_CLARIFICATION', 'SUCCEEDED', 'FAILED', 'CANCELLED'])
