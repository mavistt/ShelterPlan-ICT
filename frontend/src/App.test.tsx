import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

vi.mock('./components/MapView', () => ({
  MapView: ({ region }: { region: { town: string } }) => <div data-testid="map-view">真实图层地图 · {region.town}</div>,
}))
vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="coverage-chart" /> }))

const regionMetadata = {
  region_id: 'shiqi', town: '石岐街道', role: 'supplementary',
  data_version: 'shelterplan_module_a_v2.0-demo__shiqi', release_status: 'PASS_DEMO', data_status: 'DEMO_ONLY',
  demand_nodes: 7336, shelters: 25, od_rows: 183400, reachable_pairs: 178000, population_estimate: 601716.8,
  coverage: { '15': { threshold_min: 15, demand_nodes: 7336, covered_nodes: 2043, uncovered_nodes: 5293, population_with_candidate: 231618, population_total: 601716, population_share: .38, interpretation: 'network candidate reachability only; no capacity allocation' } },
  quality: { real_pilot_accepted: false, verdict: 'DEMO_ONLY', unresolved_shelters: 25, blockers: ['Demo coordinates are approximate or unresolved.'] },
  layers: [{ layer_id: 'shelter_capacity', description: 'Shelter points', geometry_type: 'Point', format: 'GeoJSON', fields: ['shelter_id'], available: true }],
}

function mockFetch(url: string, init?: RequestInit) {
  if (url === '/__a/v2/regions') return { regions: [regionMetadata] }
  if (url === '/api/c/healthz') return { status: 'ok', service: 'shelterplan-module-c', data_status: 'DEMO_ONLY', data_version: regionMetadata.data_version }
  if (url === '/api/c/v1/regions') return { source_mode: 'module_a_v2', regions: [{ region_id: 'shiqi', town: '石岐街道', data_version: regionMetadata.data_version, data_status: 'DEMO_ONLY', release_status: 'PASS_DEMO', demand_nodes: 7336, shelters: 25, od_available: true, warnings: [] }] }
  if (url === '/api/c/v1/capabilities') return { statuses: ['NEEDS_CLARIFICATION', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'], task_types: ['assess', 'optimize'], b_service_status: 'ok', limits: { turn_timeout_s: 180, poll_interval_hint_ms: 500, max_text_chars: 4000 } }
  if (url === '/api/c/v1/sessions' && init?.method === 'POST') return { session_id: 'session-test', status: 'READY', region_id: 'shiqi', data_version: regionMetadata.data_version, data_status: 'DEMO_ONLY' }
  throw new Error(`Unhandled mocked request ${url}`)
}

describe('Module D workspace', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); sessionStorage.clear() })

  it('shows manifest-backed A v2 figures and the DEMO_ONLY disclosure', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const result = mockFetch(String(input), init)
      return new Response(JSON.stringify(result), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter><App /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByRole('heading', { name: '规划总览' })).toBeInTheDocument()
    expect(screen.getByText('演示数据 · 请勿用于真实应急决策')).toBeInTheDocument()
    expect(screen.getByText('60.2 万')).toBeInTheDocument()
    expect(screen.getByTestId('map-view')).toHaveTextContent('石岐街道')
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/c/v1/sessions', expect.objectContaining({ method: 'POST' })))
  })
})
