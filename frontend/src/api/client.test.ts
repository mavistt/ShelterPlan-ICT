import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

describe('C API client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('always scopes result layer reads to the owning session', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({ result_id: 'r-1', variant: 'baseline', layers: [], deficits: { count: 0, sample: [] }, data_version: 'v', data_status: 'DEMO_ONLY' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.resultLayers('session-owned', 'r-1')
    expect(String(fetchMock.mock.calls[0][0])).toContain('session_id=session-owned')
    expect(String(fetchMock.mock.calls[0][0])).toContain('/v1/results/r-1/layers')
  })

  it('requests bounded evidence pages through C', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({ section: 'checks', offset: 40, limit: 20, total: 61, has_more: true, items: [] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const page = await api.evidence('s-2', 'r-2', 'checks', 40, 20)
    expect(page.has_more).toBe(true)
    expect(String(fetchMock.mock.calls[0][0])).toContain('session_id=s-2&offset=40&limit=20')
  })
})
