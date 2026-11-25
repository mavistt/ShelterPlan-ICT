import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check, ChevronDown, Compass, Info, LoaderCircle, MapPin,
  PanelLeftOpen, PanelRightClose, PanelRightOpen, ShieldCheck, X,
} from 'lucide-react'
import { api, ApiError, C_FINAL_STATUSES } from './api/client'
import type { RegionMetadata, ShelterRecord } from './api/types'
import { MapView } from './components/MapView'
import { TaskDrawer } from './components/TaskDrawer'
import { AgentCopilot } from './components/AgentCopilot'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { IntroModal, type ScenarioPreset } from './components/IntroModal'
import { useWorkspace, type WorkspaceMessage } from './state/workspace'

const intFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })

function useCurrentRegion() {
  const regionId = useWorkspace((s) => s.regionId)
  const localRegions = useQuery({ queryKey: ['local-regions'], queryFn: api.localRegions, staleTime: 5 * 60_000 })
  const cRegions = useQuery({ queryKey: ['c-regions'], queryFn: api.regions, refetchInterval: 30_000, retry: 1 })
  const region = localRegions.data?.regions.find((item) => item.region_id === regionId)
    ?? localRegions.data?.regions[0]
  return { region, localRegions, cRegions }
}

function useShelters(regionId?: string) {
  const query = useQuery({
    queryKey: ['shelter-features', regionId],
    queryFn: () => api.geoJson(regionId!, 'shelter_capacity'),
    enabled: Boolean(regionId),
    staleTime: 30 * 60_000,
  })
  const shelters = useMemo<ShelterRecord[]>(() => {
    const features = query.data?.features ?? []
    return features
      .map((f) => {
        const p = (f.properties ?? {}) as Record<string, unknown>
        const coords = f.geometry && 'coordinates' in f.geometry ? (f.geometry.coordinates as number[]) : [0, 0]
        const id = String(p.shelter_id ?? '')
        if (!id) return null
        const name = String(p.shelter_name ?? p.shelter_name_demo ?? p.shelter_name_candidate ?? id)
        const address = String(p.address ?? p.address_demo ?? p.address_candidate ?? '')
        const community = String(p.community_name ?? p['所属社区（村）'] ?? '')
        const capRaw = Number(p.capacity_persons_effective ?? p.capacity_persons ?? 0)
        const areaRaw = p.area_sqm != null ? Number(p.area_sqm) : null
        return {
          shelter_id: id,
          name,
          address,
          community,
          capacity: Number.isFinite(capRaw) ? Math.round(capRaw) : 0,
          area_sqm: areaRaw != null && Number.isFinite(areaRaw) ? areaRaw : null,
          phone: p.phone ? String(p.phone) : null,
          capacity_level: String(p.capacity_level ?? 'medium'),
          lon: Number(coords[0] ?? 0),
          lat: Number(coords[1] ?? 0),
        }
      })
      .filter((s): s is ShelterRecord => s !== null)
  }, [query.data])
  return { shelters, isLoading: query.isLoading }
}

function useSession(region?: RegionMetadata) {
  const regionId = useWorkspace((s) => s.regionId)
  const sessionId = useWorkspace((s) => s.sessionId)
  const setSession = useWorkspace((s) => s.setSession)
  const setBusy = useWorkspace((s) => s.setSessionBusy)
  const setMessages = useWorkspace((s) => s.setMessages)
  const clearMessages = useWorkspace((s) => s.clearSessionMessages)
  const health = useQuery({ queryKey: ['c-health'], queryFn: api.health, refetchInterval: 15_000, retry: 1 })

  useEffect(() => {
    if (!region || !health.data || sessionId) return
    let cancelled = false
    const restoreOrCreate = async () => {
      let failure: string | null = null
      setBusy(true)
      try {
        const key = `sp-session:${regionId}`
        const stored = sessionStorage.getItem(key)
        if (stored) {
          try {
            const existing = await api.getSession(stored)
            if (existing.region_id === regionId && existing.data_version === region.data_version) {
              if (!cancelled) {
                setSession(existing.session_id)
                // Hydrate persisted turns so page reload keeps conversation history
                if (existing.turns?.length) {
                  const fullTurns = await Promise.all(
                    existing.turns.map((t) => api.getTurn(t.turn_id).catch(() => null)),
                  )
                  const restored: WorkspaceMessage[] = []
                  for (const turn of fullTurns) {
                    if (!turn) continue
                    const ts = turn.created_at ?? new Date().toISOString()
                    if (turn.user_text) {
                      restored.push({ id: `${turn.turn_id}-user`, role: 'user', text: turn.user_text, createdAt: ts })
                    }
                    restored.push({ id: turn.turn_id, role: 'assistant', text: '', createdAt: ts, turn })
                  }
                  if (!cancelled && restored.length) setMessages(restored)
                }
              }
              return
            }
          } catch {
            sessionStorage.removeItem(key)
          }
        }
        const fresh = await api.createSession(regionId)
        if (!cancelled) {
          sessionStorage.setItem(key, fresh.session_id)
          setSession(fresh.session_id)
          clearMessages()
        }
      } catch (error) {
        failure = error instanceof Error ? error.message : '无法建立决策会话'
        if (!cancelled) setBusy(false, failure)
      } finally {
        if (!cancelled && !failure) setBusy(false)
      }
    }
    void restoreOrCreate()
    return () => { cancelled = true }
  }, [clearMessages, health.data, region, regionId, sessionId, setBusy, setMessages, setSession])

  return health
}

function useWorkflow() {
  const queryClient = useQueryClient()
  const sessionId = useWorkspace((s) => s.sessionId)
  const addMessage = useWorkspace((s) => s.addMessage)
  const updateTurn = useWorkspace((s) => s.updateTurn)
  const setActive = useWorkspace((s) => s.setActive)
  const clearMessages = useWorkspace((s) => s.clearSessionMessages)
  const activeTurnId = useWorkspace((s) => s.activeTurnId)
  const activeStatus = useWorkspace((s) => s.activeStatus)
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const run = useCallback(async (prompt: string) => {
    const text = prompt.trim()
    if (!text || !sessionId || submitting) return
    if (text.length > 4000) {
      setLocalError('请求超过 4,000 字符限制。')
      return
    }
    setSubmitting(true)
    setLocalError(null)
    try {
      const ack = await api.submitTurn(sessionId, text, crypto.randomUUID())
      const now = new Date().toISOString()
      addMessage({ id: `${ack.turn_id}-user`, role: 'user', text, createdAt: now })
      addMessage({ id: ack.turn_id, role: 'assistant', text: '', createdAt: now, turn: ack })
      updateTurn(ack)
      setActive(ack.turn_id, ack.status)
      let current = ack
      const start = Date.now()
      while (!C_FINAL_STATUSES.has(current.status) && Date.now() - start < 190_000) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        current = await api.getTurn(ack.turn_id)
        updateTurn(current)
      }
      if (!C_FINAL_STATUSES.has(current.status)) {
        setLocalError('等待超时（190s），请检查后端求解服务状态。')
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['c-session', sessionId] })
      if (current.result_id) {
        await queryClient.invalidateQueries({ queryKey: ['result-layers', sessionId, current.result_id] })
      }
    } catch (error) {
      const msg = error instanceof ApiError ? `${error.message} (HTTP ${error.status})` : error instanceof Error ? error.message : '请求失败'
      setLocalError(msg)
    } finally {
      setSubmitting(false)
    }
  }, [addMessage, queryClient, sessionId, setActive, submitting, updateTurn])

  const cancel = useCallback(async () => {
    if (!activeTurnId || activeStatus !== 'RUNNING') return
    try {
      const res = await api.cancelTurn(activeTurnId)
      updateTurn(res)
      if (res.cancel_accepted === false) setLocalError('求解器已启动，当前计算将在后台完成。')
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '取消失败')
    }
  }, [activeStatus, activeTurnId, updateTurn])

  const reset = useCallback(async () => {
    if (!sessionId || submitting) return
    try {
      await api.resetSession(sessionId)
      clearMessages()
      setLocalError(null)
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '重置会话失败')
    }
  }, [clearMessages, sessionId, submitting])

  return { run, cancel, reset, submitting, localError, clearError: () => setLocalError(null) }
}

export default function App() {
  const { region, localRegions, cRegions } = useCurrentRegion()
  const health = useSession(region)
  const { shelters } = useShelters(region?.region_id)
  const capabilities = useQuery({ queryKey: ['c-capabilities'], queryFn: api.capabilities, refetchInterval: 30_000, retry: 1 })
  const flow = useWorkflow()

  const regionId = useWorkspace((s) => s.regionId)
  const setRegion = useWorkspace((s) => s.setRegion)
  const sessionId = useWorkspace((s) => s.sessionId)
  const sessionBusy = useWorkspace((s) => s.sessionBusy)
  const sessionError = useWorkspace((s) => s.sessionError)
  const activeResultId = useWorkspace((s) => s.activeResultId)
  const messages = useWorkspace((s) => s.messages)
  const drawerOpen = useWorkspace((s) => s.drawerOpen)
  const setDrawerOpen = useWorkspace((s) => s.setDrawerOpen)
  const copilotOpen = useWorkspace((s) => s.copilotOpen)
  const setCopilotOpen = useWorkspace((s) => s.setCopilotOpen)
  const evidenceOpen = useWorkspace((s) => s.evidenceOpen)
  const setEvidenceOpen = useWorkspace((s) => s.setEvidenceOpen)
  const introOpen = useWorkspace((s) => s.introOpen)
  const setIntroOpen = useWorkspace((s) => s.setIntroOpen)
  const setActiveMode = useWorkspace((s) => s.setActiveMode)
  const setStressedShelterIds = useWorkspace((s) => s.setStressedShelterIds)
  const setOptCandidates = useWorkspace((s) => s.setOptCandidates)
  const flyTo = useWorkspace((s) => s.flyTo)

  // Default layers: boundary + Kepler heatmap + capacity-scaled shelters
  const [activeLayers, setActiveLayers] = useState<string[]>(['boundary', 'population_heatmap', 'shelter_capacity'])
  const [regionMenuOpen, setRegionMenuOpen] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)
  const [evidenceResultId, setEvidenceResultId] = useState<string | null>(null)
  const [snapshotView, setSnapshotView] = useState<'baseline' | 'damaged' | 'recovered'>('recovered')

  useEffect(() => {
    setActiveLayers(['boundary', 'population_heatmap', 'shelter_capacity'])
  }, [regionId])

  const effectiveResultId = evidenceResultId ?? activeResultId
  const resultLayersQuery = useQuery({
    queryKey: ['result-layers', sessionId, effectiveResultId],
    queryFn: () => api.resultLayers(sessionId!, effectiveResultId!),
    enabled: Boolean(sessionId && effectiveResultId),
    retry: false,
  })
  const layerMap = useMemo(() => {
    const m: Record<string, typeof resultLayersQuery.data extends undefined ? never : NonNullable<typeof resultLayersQuery.data>['layers'][number]> = {}
    for (const l of resultLayersQuery.data?.layers ?? []) m[l.layer_id] = l
    return m
  }, [resultLayersQuery.data])

  const hasThreeState = Boolean(
    layerMap.shelter_utilization_baseline?.rows?.length
    && layerMap.shelter_utilization_damaged?.rows?.length
    && layerMap.shelter_utilization_recovered?.rows?.length,
  )

  useEffect(() => {
    if (hasThreeState) setSnapshotView('recovered')
  }, [effectiveResultId, hasThreeState])

  const resultRows = useMemo(() => {
    if (hasThreeState) {
      if (snapshotView === 'baseline') return layerMap.shelter_utilization_baseline?.rows ?? []
      if (snapshotView === 'damaged') return layerMap.shelter_utilization_damaged?.rows ?? []
      return layerMap.shelter_utilization_recovered?.rows ?? []
    }
    return layerMap.shelter_utilization?.rows ?? []
  }, [hasThreeState, layerMap, snapshotView])

  // Latest succeeded turn for HUD KPIs
  const latestTurn = useMemo(() => {
    return [...messages].reverse().find((m) => m.turn?.status === 'SUCCEEDED')?.turn ?? null
  }, [messages])

  const regionOptions = useMemo(() => {
    const cSet = new Set((cRegions.data?.regions ?? []).map((r) => r.region_id))
    const all = localRegions.data?.regions ?? []
    return cSet.size ? all.filter((r) => cSet.has(r.region_id)) : all
  }, [cRegions.data?.regions, localRegions.data?.regions])

  const totalCapacity = useMemo(() => shelters.reduce((sum, s) => sum + s.capacity, 0), [shelters])

  const handleLaunchScenario = (preset: ScenarioPreset) => {
    setIntroOpen(false)
    setActiveMode(preset.mode)
    setDrawerOpen(true)
    setCopilotOpen(true)
    setActiveLayers(preset.layers)
    if (preset.stressIds) setStressedShelterIds(preset.stressIds)
    if (preset.optPreset) {
      setOptCandidates(preset.optPreset.map((item, i) => ({
        id: `intro-opt-${i}-${crypto.randomUUID()}`,
        shelterId: item.shelterId,
        capacity: item.capacity,
        cost: item.cost,
      })))
    }
    if (preset.focusShelterId) {
      const target = shelters.find((s) => s.shelter_id === preset.focusShelterId)
      if (target) flyTo(target.lon, target.lat, target.shelter_id)
    }
    if (sessionId && health.data?.status === 'ok') {
      void flow.run(preset.prompt)
    }
  }

  if (localRegions.isLoading || !region) {
    return (
      <div className="boot-screen">
        <LoaderCircle size={22} className="spin" />
        <span>正在载入空间底图与区域数据…</span>
      </div>
    )
  }

  const online = health.data?.status === 'ok'
  const hudCoverage = typeof latestTurn?.metrics?.optimized_coverage_ratio === 'number'
    ? latestTurn.metrics.optimized_coverage_ratio
    : typeof latestTurn?.metrics?.coverage_ratio === 'number'
      ? latestTurn.metrics.coverage_ratio
      : null
  const hudServed = typeof latestTurn?.metrics?.optimized_served_demand === 'number'
    ? latestTurn.metrics.optimized_served_demand
    : typeof latestTurn?.metrics?.served_demand === 'number'
      ? latestTurn.metrics.served_demand
      : null
  const hudDelta = typeof latestTurn?.metrics?.coverage_delta_persons === 'number'
    ? latestTurn.metrics.coverage_delta_persons
    : null

  return (
    <div className="workbench-root">
      {/* Full-viewport interactive Map Stage */}
      <MapView
        region={region}
        shelters={shelters}
        activeLayers={activeLayers}
        onActiveLayersChange={setActiveLayers}
        resultRows={resultRows}
      />

      {/* Floating Apple-Maps Frosted Top Bar */}
      <header className="floating-topbar">
        <div className="ft-left">
          <div className="brand-chip">
            <span className="brand-mark"><ShieldCheck size={15} /></span>
            <div>
              <strong>ShelterPlan</strong>
              <small>灾后韧性重建决策</small>
            </div>
          </div>

          <div className="region-drop-wrap">
            <button
              type="button"
              className="region-drop-btn"
              onClick={() => setRegionMenuOpen((v) => !v)}
              aria-expanded={regionMenuOpen}
            >
              <MapPin size={14} className="accent-teal" />
              <span>中山 · <strong>{region.town}</strong></span>
              <ChevronDown size={14} />
            </button>
            {regionMenuOpen && (
              <div className="glass-popover region-popover">
                <div className="popover-title">切换分析镇街</div>
                {regionOptions.map((opt) => (
                  <button
                    key={opt.region_id}
                    type="button"
                    className={`region-opt-row ${opt.region_id === region.region_id ? 'selected' : ''}`}
                    onClick={() => {
                      setRegion(opt.region_id)
                      setRegionMenuOpen(false)
                    }}
                  >
                    <div>
                      <strong>{opt.town}</strong>
                      <small>{opt.shelters} 处场所 · {(opt.population_estimate / 10000).toFixed(1)} 万模型人口</small>
                    </div>
                    {opt.region_id === region.region_id && <Check size={14} className="accent-teal" />}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Dynamic Live HUD KPIs */}
        <div className="ft-hud" aria-label="核心态势指标">
          <div className="hud-item">
            <span>避难场所 / 容量</span>
            <b>{region.shelters} 处 · {intFmt.format(totalCapacity)} 人</b>
          </div>
          <span className="hud-sep" />
          <div className="hud-item">
            <span>{hudCoverage !== null ? '当前求解覆盖率' : '15分钟路网可达'}</span>
            <b className="accent-teal">
              {hudCoverage !== null
                ? `${(hudCoverage <= 1 ? hudCoverage * 100 : hudCoverage).toFixed(1)}%`
                : `${((region.coverage['15']?.population_share ?? 0) * 100).toFixed(1)}%`}
            </b>
          </div>
          {hudServed !== null && (
            <>
              <span className="hud-sep" />
              <div className="hud-item">
                <span>已服务人数</span>
                <b>
                  {intFmt.format(Math.round(hudServed))} 人
                  {hudDelta !== null && hudDelta > 0 && (
                    <em className="hud-delta">+{intFmt.format(Math.round(hudDelta))}</em>
                  )}
                </b>
              </div>
            </>
          )}
        </div>

        <div className="ft-right">
          <button
            type="button"
            className="glass-pill-btn"
            onClick={() => setIntroOpen(true)}
            title="打开灾后场景任务引导"
          >
            <Compass size={14} /> <span>场景引导</span>
          </button>

          <div className="info-popover-wrap">
            <button
              type="button"
              className={`glass-icon-btn ${infoOpen ? 'is-active' : ''}`}
              onClick={() => setInfoOpen((v) => !v)}
              title="数据来源与口径说明"
              aria-label="数据来源与口径说明"
            >
              <Info size={15} />
            </button>
            {infoOpen && (
              <div className="glass-popover info-popover">
                <div className="popover-title">
                  <span>数据来源与模型口径</span>
                  <button type="button" className="icon-close-btn" onClick={() => setInfoOpen(false)}><X size={13} /></button>
                </div>
                <ul className="info-list">
                  <li><b>人口栅格</b>：WorldPop 2025 100m 人口模型估算（当前区域 {(region.population_estimate / 10000).toFixed(1)} 万）。</li>
                  <li><b>步行路网</b>：OpenStreetMap 静态路网快照（{intFmt.format(region.od_rows)} 条 OD 矩阵）。</li>
                  <li><b>避难场所</b>：2024 年度应急避难场所清单（EPSG:4326 WGS84，支持实时交互与参数推演）。</li>
                  <li><b>版本标识</b>：<code>{region.data_version}</code></li>
                </ul>
              </div>
            )}
          </div>

          <button
            type="button"
            className="glass-icon-btn"
            onClick={() => setCopilotOpen(!copilotOpen)}
            title={copilotOpen ? '收起规划助手' : '展开规划助手'}
            aria-label={copilotOpen ? '收起规划助手' : '展开规划助手'}
          >
            {copilotOpen ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
          </button>
        </div>
      </header>

      {/* Floating 3-State Snapshot Switcher when a composite disaster_recovery result is active */}
      {hasThreeState && (
        <div className="tri-state-switcher" role="group" aria-label="灾前灾损恢复三态切换">
          <span className="tss-label">地图快照</span>
          {([
            { id: 'baseline', label: '灾前基线' },
            { id: 'damaged', label: '灾后受损' },
            { id: 'recovered', label: '优化恢复' },
          ] as const).map((item) => (
            <button
              key={item.id}
              type="button"
              className={`tss-btn ${snapshotView === item.id ? `active tone-${item.id}` : ''}`}
              onClick={() => setSnapshotView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      {/* Left Collapsible Disaster Recovery Task Drawer */}
      {drawerOpen ? (
        <TaskDrawer
          region={region}
          shelters={shelters}
          sessionReady={Boolean(online && sessionId)}
          submitting={flow.submitting}
          onRun={(prompt) => {
            setCopilotOpen(true)
            void flow.run(prompt)
          }}
          onLayersPreset={setActiveLayers}
        />
      ) : (
        <button
          type="button"
          className="drawer-reopen-btn"
          onClick={() => setDrawerOpen(true)}
          title="展开规划任务台"
        >
          <PanelLeftOpen size={15} /> <span>规划任务台</span>
        </button>
      )}

      {/* Right Collapsible Structured Agent Copilot */}
      {copilotOpen && (
        <AgentCopilot
          region={region}
          shelters={shelters}
          online={online}
          sessionBusy={sessionBusy}
          sessionError={sessionError}
          capabilities={capabilities.data}
          submitting={flow.submitting}
          localError={flow.localError}
          onClearError={flow.clearError}
          onRun={(prompt) => void flow.run(prompt)}
          onCancel={() => void flow.cancel()}
          onResetSession={() => void flow.reset()}
          onOpenEvidence={(rid) => {
            if (rid) setEvidenceResultId(rid)
            setEvidenceOpen(true)
          }}
        />
      )}

      {/* First-Screen Dismissible Scenario Context Card */}
      {introOpen && (
        <IntroModal
          region={region}
          shelters={shelters}
          sessionReady={Boolean(online && sessionId)}
          onDismiss={() => setIntroOpen(false)}
          onLaunchScenario={handleLaunchScenario}
        />
      )}

      {/* Slide-over Verifier & Audit Evidence Drawer */}
      {evidenceOpen && (
        <EvidenceDrawer
          region={region}
          shelters={shelters}
          sessionId={sessionId}
          resultId={effectiveResultId}
          resultRows={resultRows}
          onClose={() => setEvidenceOpen(false)}
        />
      )}
    </div>
  )
}
