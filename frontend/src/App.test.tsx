import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import App from './App'
import { useWorkspace } from './state/workspace'

vi.mock('./components/MapView', () => ({
  MapView: ({ region, shelters }: { region: { town: string }; shelters: Array<{ name: string }> }) => (
    <div data-testid="map-view">
      真实底图与热力图 · {region.town} · {shelters.length} 处场所
    </div>
  ),
}))
vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="coverage-chart" /> }))

const regionMetadata = {
  region_id: 'shiqi',
  town: '石岐街道',
  role: 'supplementary',
  data_version: 'shelterplan_module_a_v2.0-demo__shiqi',
  release_status: 'PASS_DEMO',
  data_status: 'DEMO_ONLY',
  demand_nodes: 7336,
  shelters: 25,
  od_rows: 183400,
  reachable_pairs: 178000,
  population_estimate: 601716.8,
  coverage: {
    '15': {
      threshold_min: 15,
      demand_nodes: 7336,
      covered_nodes: 2043,
      uncovered_nodes: 5293,
      population_with_candidate: 231618,
      population_total: 601716,
      population_share: 0.385,
      interpretation: 'network candidate reachability only',
    },
  },
  quality: {
    real_pilot_accepted: false,
    verdict: 'DEMO_ONLY',
    unresolved_shelters: 0,
    blockers: [],
  },
  layers: [
    { layer_id: 'shelter_capacity', description: 'Shelter points', geometry_type: 'Point', format: 'GeoJSON', fields: ['shelter_id'], available: true },
    { layer_id: 'population_heatmap', description: 'Heatmap', geometry_type: 'Point', format: 'GeoJSON', fields: ['pop_est'], available: true },
  ],
}

const shelterGeoJson = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        shelter_id: 'zs_7c10257791',
        shelter_name_demo: '中山市实验小学',
        address_demo: '中山市石岐区永盛街32号',
        community_name: '市教体局',
        capacity_persons_effective: 8000,
        area_sqm: 37000,
      },
      geometry: { type: 'Point', coordinates: [113.3858, 22.547] },
    },
    {
      type: 'Feature',
      properties: {
        shelter_id: 'zs_ba9e4c3236',
        shelter_name_demo: '中山市华侨中学高中部',
        address_demo: '中山市石岐街道大沙南路13号',
        community_name: '市教体局',
        capacity_persons_effective: 7600,
        area_sqm: 19000,
      },
      geometry: { type: 'Point', coordinates: [113.3879, 22.5496] },
    },
    {
      type: 'Feature',
      properties: {
        shelter_id: 'zs_f1e6d13f73',
        shelter_name_demo: '中山市石岐中心小学室内应急避护场所',
        address_demo: '广东省中山市东区东文路2号',
        community_name: '市教体局',
        capacity_persons_effective: 1400,
        area_sqm: 2850,
      },
      geometry: { type: 'Point', coordinates: [113.4051, 22.5201] },
    },
  ],
}

function mockFetch(url: string, init?: RequestInit) {
  if (url === '/__a/v2/regions') return { regions: [regionMetadata] }
  if (url === '/__a/v2/layers/shiqi/shelter_capacity') return shelterGeoJson
  if (url === '/api/c/healthz') return { status: 'ok', service: 'shelterplan-module-c', data_status: 'DEMO_ONLY', data_version: regionMetadata.data_version }
  if (url === '/api/c/v1/regions') {
    return {
      source_mode: 'module_a_v2',
      regions: [{ region_id: 'shiqi', town: '石岐街道', data_version: regionMetadata.data_version, data_status: 'DEMO_ONLY', release_status: 'PASS_DEMO', demand_nodes: 7336, shelters: 25, od_available: true, warnings: [] }],
    }
  }
  if (url === '/api/c/v1/capabilities') return { statuses: ['NEEDS_CLARIFICATION', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'], task_types: ['assess', 'optimize_budget'], b_service_status: 'ok', limits: { turn_timeout_s: 180, poll_interval_hint_ms: 500, max_text_chars: 4000 } }
  if (url === '/api/c/v1/sessions' && init?.method === 'POST') return { session_id: 'session-test', status: 'READY', region_id: 'shiqi', data_version: regionMetadata.data_version, data_status: 'DEMO_ONLY' }
  throw new Error(`Unhandled mocked request ${url}`)
}

describe('ShelterPlan Map-First Resilience Workbench', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    sessionStorage.clear()
    useWorkspace.setState({
      regionId: 'shiqi',
      sessionId: null,
      messages: [],
      activeMode: 'assess',
      drawerOpen: true,
      copilotOpen: true,
      evidenceOpen: false,
      introOpen: true,
      stressedShelterIds: [],
      optCandidates: [],
    })
  })

  it('renders Map-First workbench, first-screen post-disaster intro, and real Chinese shelter names without DEMO_ONLY banners', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const result = mockFetch(String(input), init)
      return new Response(JSON.stringify(result), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter><App /></MemoryRouter></QueryClientProvider>)

    expect(await screen.findByText('从灾后应急盲区到最优重建分配')).toBeInTheDocument()
    expect(screen.getByTestId('map-view')).toHaveTextContent('石岐街道')
    expect(screen.queryByText('演示数据 · 请勿用于真实应急决策')).not.toBeInTheDocument()
    expect(screen.queryByText('DEMO ONLY')).not.toBeInTheDocument()

    // Dismiss intro modal and verify real Chinese shelter names in TaskDrawer
    fireEvent.click(screen.getByText('直接浏览地图工作台'))
    expect(await screen.findByText('中山市实验小学')).toBeInTheDocument()
    expect(screen.getByText('中山市华侨中学高中部')).toBeInTheDocument()

    // Switch to Optimize mode and verify knapsack preset button works
    fireEvent.click(screen.getByRole('tab', { name: '重建优化' }))
    expect(await screen.findByText('灾后重建预算最优分配')).toBeInTheDocument()
    expect(screen.getByText('经典 3 方案预设')).toBeInTheDocument()

    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/c/v1/sessions', expect.objectContaining({ method: 'POST' })))
  })

  it('formats Agent KPI ratios, walk time units, MIP vs Greedy bars, and monotonicity badge properly', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const result = mockFetch(String(input), init)
      return new Response(JSON.stringify(result), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    useWorkspace.setState({
      sessionId: 'session-test',
      introOpen: false,
      messages: [
        {
          id: 't-opt-1',
          role: 'assistant',
          text: '',
          createdAt: '2026-11-25T00:00:00Z',
          turn: {
            turn_id: 't-opt-1',
            session_id: 'session-test',
            status: 'SUCCEEDED',
            task_type: 'optimize_budget',
            result_id: 'res-opt-1',
            message: '预算优化完成，优先扩容 zs_ba9e4c3236 与 zs_f1e6d13f73。',
            metrics: {
              optimized_coverage_ratio: 0.168,
              coverage_delta_persons: 870,
              optimized_served_demand: 12904,
              weighted_walk_time_min: 9.2,
            },
            greedy_comparison: {
              status: 'available',
              greedy_served_demand: 12634,
              mip_served_demand: 12904,
              served_demand_gap: 270,
            },
            monotonicity_check: {
              status: 'PASS',
            },
          },
        },
      ],
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter><App /></MemoryRouter></QueryClientProvider>)

    const ratios = await screen.findAllByText('16.8%')
    expect(ratios.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('+870 人')).toBeInTheDocument()
    expect(screen.getByText('9.2 min')).toBeInTheDocument()
    expect(screen.getByText('全局规划 (MIP) vs 经验贪心 (Greedy)')).toBeInTheDocument()
    expect(screen.getByText('+270 人')).toBeInTheDocument()
    expect(screen.getByText('单调性通过')).toBeInTheDocument()
  })
})
