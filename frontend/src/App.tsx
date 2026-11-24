import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import {
  Activity, AlertTriangle, ArrowUpRight, BookOpenCheck, Check, ChevronDown,
  CircleHelp, Clock3, Database, Gauge, History, LoaderCircle, Map, MapPin,
  Pause, Play, Send, ShieldCheck, Sparkles, TrendingUp, WandSparkles, X,
  type LucideIcon,
} from 'lucide-react'
import { api, ApiError, C_FINAL_STATUSES } from './api/client'
import type { CCapabilities, CStatus, RegionMetadata } from './api/types'
import { MapView } from './components/MapView'
import { useWorkspace, type WorkspaceMessage } from './state/workspace'

const NAV_ITEMS = [
  { to: '/', label: '规划总览', icon: Gauge, end: true },
  { to: '/spatial', label: '空间数据', icon: Map },
  { to: '/assessment', label: '服务评估', icon: Activity },
  { to: '/scenario', label: '情景模拟', icon: WandSparkles },
  { to: '/optimize', label: '预算优化', icon: TrendingUp },
  { to: '/runs', label: '运行与证据', icon: History },
]

const STATUS_LABELS: Record<CStatus, string> = {
  RUNNING: '执行中', SUCCEEDED: '已完成', NEEDS_CLARIFICATION: '待补充信息', FAILED: '未完成', CANCELLED: '已取消',
}

const numberFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })
const integerFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })

function formatNumber(value: unknown, digits = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return (digits ? numberFmt : integerFmt).format(value)
}

function useCurrentRegion() {
  const regionId = useWorkspace((state) => state.regionId)
  const localRegions = useQuery({ queryKey: ['local-regions'], queryFn: api.localRegions, staleTime: 5 * 60_000 })
  const cRegions = useQuery({ queryKey: ['c-regions'], queryFn: api.regions, refetchInterval: 30_000, retry: 1 })
  const region = localRegions.data?.regions.find((item) => item.region_id === regionId)
  const cRegion = cRegions.data?.regions.find((item) => item.region_id === regionId)
  return { region, localRegions, cRegions, cRegion }
}

function useSession(region?: RegionMetadata) {
  const regionId = useWorkspace((state) => state.regionId)
  const sessionId = useWorkspace((state) => state.sessionId)
  const setSession = useWorkspace((state) => state.setSession)
  const setBusy = useWorkspace((state) => state.setSessionBusy)
  const clearMessages = useWorkspace((state) => state.clearSessionMessages)
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
              if (!cancelled) setSession(existing.session_id)
              return
            }
          } catch { sessionStorage.removeItem(key) }
        }
        const fresh = await api.createSession(regionId)
        if (!cancelled) {
          sessionStorage.setItem(key, fresh.session_id)
          setSession(fresh.session_id)
          clearMessages()
        }
      } catch (error) {
        failure = error instanceof Error ? error.message : '无法创建 C 会话'
        if (!cancelled) setBusy(false, failure)
      } finally {
        if (!cancelled && !failure) setBusy(false)
      }
    }
    void restoreOrCreate()
    return () => { cancelled = true }
  }, [clearMessages, health.data, region, regionId, sessionId, setBusy, setSession])
  return health
}

function useWorkflow() {
  const queryClient = useQueryClient()
  const sessionId = useWorkspace((state) => state.sessionId)
  const addMessage = useWorkspace((state) => state.addMessage)
  const updateTurn = useWorkspace((state) => state.updateTurn)
  const setActive = useWorkspace((state) => state.setActive)
  const activeTurnId = useWorkspace((state) => state.activeTurnId)
  const activeStatus = useWorkspace((state) => state.activeStatus)
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const run = useCallback(async (prompt: string) => {
    const text = prompt.trim()
    if (!text || !sessionId || submitting) return
    if (text.length > 4000) {
      setLocalError('请求超过 C 接口的 4,000 字符限制。')
      return
    }
    setSubmitting(true)
    setLocalError(null)
    try {
      const ack = await api.submitTurn(sessionId, text, crypto.randomUUID())
      const now = new Date().toISOString()
      const userMessage: WorkspaceMessage = { id: `${ack.turn_id}-user`, role: 'user', text, createdAt: now }
      const assistantMessage: WorkspaceMessage = { id: ack.turn_id, role: 'assistant', text: '', createdAt: now, turn: ack }
      addMessage(userMessage)
      addMessage(assistantMessage)
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
        setLocalError('等待超过 190 秒。请查看 C 服务状态；已提交任务可能仍在执行。')
        return
      }
      await queryClient.invalidateQueries({ queryKey: ['c-session', sessionId] })
      if (current.result_id) {
        await queryClient.invalidateQueries({ queryKey: ['result-layers', sessionId, current.result_id] })
      }
    } catch (error) {
      const message = error instanceof ApiError ? `${error.message}（HTTP ${error.status}）` : error instanceof Error ? error.message : '请求失败'
      setLocalError(message)
    } finally {
      setSubmitting(false)
    }
  }, [addMessage, queryClient, sessionId, setActive, submitting, updateTurn])

  const cancel = useCallback(async () => {
    if (!activeTurnId || activeStatus !== 'RUNNING') return
    try {
      const response = await api.cancelTurn(activeTurnId)
      updateTurn(response)
      if (response.cancel_accepted === false) setLocalError('B 求解已经开始，C 未能取消任务；后台求解可能仍会继续。')
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '取消请求失败')
    }
  }, [activeStatus, activeTurnId, updateTurn])

  return { run, cancel, submitting, localError, clearError: () => setLocalError(null) }
}

function AppShell() {
  const { region, localRegions, cRegions, cRegion } = useCurrentRegion()
  const health = useSession(region)
  const regionId = useWorkspace((state) => state.regionId)
  const setRegion = useWorkspace((state) => state.setRegion)
  const sessionBusy = useWorkspace((state) => state.sessionBusy)
  const sessionError = useWorkspace((state) => state.sessionError)
  const capabilities = useQuery({ queryKey: ['c-capabilities'], queryFn: api.capabilities, refetchInterval: 30_000, retry: 1 })
  const flow = useWorkflow()
  const [activeLayers, setActiveLayers] = useState(['boundary', 'shelter_capacity'])
  const [regionMenuOpen, setRegionMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => setActiveLayers(['boundary', 'shelter_capacity']), [regionId])

  const regionOptions = useMemo(() => {
    const available = new Set((cRegions.data?.regions ?? []).map((item) => item.region_id))
    return (localRegions.data?.regions ?? []).filter((item) => available.has(item.region_id))
  }, [cRegions.data?.regions, localRegions.data?.regions])
  const page = NAV_ITEMS.find((item) => item.to === location.pathname)
  const result = useActiveResult(regionId)

  const setPagePrompt = (prompt: string) => { void flow.run(prompt) }
  if (localRegions.isLoading || !region) {
    return <div className="app-loading"><LoaderCircle className="spin" /> 正在读取 A v2 区域目录…</div>
  }

  return <div className="app-frame">
    <aside className="sidebar">
      <Link to="/" className="brand-lockup" aria-label="ShelterPlan 首页">
        <span className="brand-icon"><span /><span /><span /></span>
        <span><strong>ShelterPlan</strong><small>URBAN RESILIENCE STUDIO</small></span>
      </Link>
      <div className="workspace-label">工作台</div>
      <nav className="side-nav" aria-label="主导航">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          <Icon size={18} strokeWidth={1.8} /><span>{label}</span>{to === '/runs' && result.resultId && <i className="nav-dot" />}
        </NavLink>)}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-source">
        <div className="source-icon"><Database size={16} /></div>
        <div><strong>A v2 数据目录</strong><span>WorldPop · OSM · 2025</span></div>
        <Check size={14} className="source-check" />
      </div>
      <div className="sidebar-user">
        <div className="user-avatar">D</div><div><strong>规划工作台</strong><span>Module D · Local Demo</span></div><ChevronDown size={15} />
      </div>
    </aside>

    <div className="app-content">
      <header className="topbar">
        <div className="breadcrumb"><span>工作区</span><span className="crumb-slash">/</span><strong>{page?.label ?? '规划总览'}</strong></div>
        <div className="topbar-actions">
          <div className={`connection-pill ${health.isError ? 'offline' : 'online'}`}><span className="connection-dot" />{health.data?.status === 'ok' ? 'C 服务在线' : health.isLoading ? '连接中' : 'C 服务离线'}</div>
          <span className="demo-pill"><span /> DEMO ONLY</span>
          <div className="topbar-divider" />
          <div className="region-picker-wrap">
            <button className="region-picker" onClick={() => setRegionMenuOpen((open) => !open)} aria-expanded={regionMenuOpen}>
              <span className="region-picker-icon"><MapPin size={15} /></span><span><small>当前区域</small><strong>{region.town}</strong></span><ChevronDown size={15} />
            </button>
            {regionMenuOpen && <div className="region-menu">
              {regionOptions.map((option) => <button key={option.region_id} onClick={() => { setRegion(option.region_id); setRegionMenuOpen(false) }}>
                <span>{option.town}</span><small>{option.region_id}</small>{regionId === option.region_id && <Check size={15} />}
              </button>)}
              {!regionOptions.length && <div className="region-empty">C 尚未发布可用区域</div>}
            </div>}
          </div>
        </div>
      </header>

      <div className="page-grid">
        <main className="page-main">
          <Routes>
            <Route path="/" element={<OverviewPage region={region} regionCatalog={regionOptions} onRegionSelect={setRegion} activeLayers={activeLayers} onActiveLayersChange={setActiveLayers} result={result} onRun={setPagePrompt} cRegion={cRegion} />} />
            <Route path="/spatial" element={<SpatialPage region={region} activeLayers={activeLayers} onActiveLayersChange={setActiveLayers} />} />
            <Route path="/assessment" element={<AssessmentPage region={region} onRun={setPagePrompt} />} />
            <Route path="/scenario" element={<ScenarioPage region={region} onRun={setPagePrompt} />} />
            <Route path="/optimize" element={<OptimizePage region={region} onRun={setPagePrompt} />} />
            <Route path="/runs" element={<RunsPage region={region} sessionId={useWorkspace.getState().sessionId} result={result} />} />
            <Route path="*" element={<OverviewPage region={region} regionCatalog={regionOptions} onRegionSelect={setRegion} activeLayers={activeLayers} onActiveLayersChange={setActiveLayers} result={result} onRun={setPagePrompt} cRegion={cRegion} />} />
          </Routes>
        </main>
        <aside className="assistant-column">
          <AssistantPanel
            region={region}
            online={Boolean(health.data?.status === 'ok')}
            sessionBusy={sessionBusy}
            sessionError={sessionError}
            capabilities={capabilities.data}
            onRun={setPagePrompt}
            flow={flow}
          />
        </aside>
      </div>
      <footer className="app-footer"><span>ShelterPlan · B/C integrated workspace</span><span>界面数据来自 A v2 manifest 与 C API · {region.data_version}</span></footer>
    </div>
  </div>
}

function useActiveResult(regionId: string) {
  const sessionId = useWorkspace((state) => state.sessionId)
  const resultId = useWorkspace((state) => state.activeResultId)
  const query = useQuery({
    queryKey: ['result-layers', sessionId, resultId],
    queryFn: () => api.resultLayers(sessionId!, resultId!),
    enabled: Boolean(sessionId && resultId),
    retry: false,
  })
  return { resultId, layers: query.data, loading: query.isLoading, error: query.error, regionId }
}

function PageHeading({ eyebrow, title, subtitle, actions }: { eyebrow: string; title: string; subtitle: string; actions?: ReactNode }) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{subtitle}</p></div>{actions && <div className="page-heading-actions">{actions}</div>}</div>
}

function OverviewPage({ region, regionCatalog, onRegionSelect, activeLayers, onActiveLayersChange, result, onRun, cRegion }: {
  region: RegionMetadata; regionCatalog: RegionMetadata[]; onRegionSelect: (regionId: string) => void
  activeLayers: string[]; onActiveLayersChange: (layers: string[]) => void
  result: ReturnType<typeof useActiveResult>; onRun: (prompt: string) => void; cRegion?: { warnings?: string[]; blockers?: string[] }
}) {
  const sessionId = useWorkspace((state) => state.sessionId)
  const coverageOption = {
    animation: false,
    grid: { left: 12, right: 10, top: 24, bottom: 20, containLabel: true },
    tooltip: { trigger: 'axis', formatter: (items: Array<{ axisValue: string; value: number }>) => `${items[0]?.axisValue} 分钟<br/>可达候选人口 ${integerFmt.format(items[0]?.value ?? 0)}` },
    xAxis: { type: 'category', data: ['10', '15', '20', '30'], axisLine: { lineStyle: { color: '#e3ebea' } }, axisTick: { show: false }, axisLabel: { color: '#82959b', fontSize: 11 } },
    yAxis: { type: 'value', splitNumber: 3, splitLine: { lineStyle: { color: '#edf1f0' } }, axisLabel: { color: '#91a0a4', fontSize: 10, formatter: (value: number) => value >= 10000 ? `${Math.round(value / 10000)}万` : value } },
    series: [{ type: 'bar', barWidth: 24, data: ['10', '15', '20', '30'].map((threshold) => region.coverage[threshold]?.population_with_candidate ?? 0), itemStyle: { color: '#4eaaa0', borderRadius: [5, 5, 0, 0] } }],
  }
  const resultRows = result.layers?.layers.find((layer) => layer.layer_id === 'shelter_utilization')?.rows ?? []
  return <>
    <PageHeading eyebrow="REGIONAL DECISION SPACE" title="规划总览" subtitle={`查看 ${region.town} 的空间基线，并通过 Agent 发起可追溯的评估与规划任务。`} actions={<span className="version-tag"><span className="version-dot" />{region.data_version}</span>} />
    <div className="data-banner"><div className="banner-shield"><ShieldCheck size={19} /></div><div><strong>演示数据 · 请勿用于真实应急决策</strong><span>人口为 WorldPop 模型估算，场所信息、坐标和容量未完成现场核验。</span></div><span className="banner-code">{region.release_status}</span></div>
    <div className="kpi-grid">
      <KpiCard icon={Map} label="需求节点" value={integerFmt.format(region.demand_nodes)} detail="人口栅格正值单元" color="mint" />
      <KpiCard icon={MapPin} label="避难场所" value={integerFmt.format(region.shelters)} detail={`${region.quality.unresolved_shelters} 处坐标待核验`} color="blue" />
      <KpiCard icon={Activity} label="模型人口估算" value={`${(region.population_estimate / 10000).toFixed(1)} 万`} detail="非实际疏散人数" color="amber" />
      <KpiCard icon={Database} label="OD 可达关系" value={integerFmt.format(region.od_rows)} detail={`${integerFmt.format(region.reachable_pairs)} 对有路网路径`} color="violet" />
    </div>
    <section className="region-dataset-shelf" aria-label="A v2 区域数据样例">
      <div className="region-shelf-heading"><div><span className="section-eyebrow">REGIONAL DATASETS</span><strong>区域数据样例</strong></div><span>{regionCatalog.length} 个 A v2 地区 · 点击切换地图</span></div>
      <div className="region-dataset-grid">{regionCatalog.map((item) => <button type="button" key={item.region_id} className={`region-dataset-card ${item.region_id === region.region_id ? 'selected' : ''}`} onClick={() => onRegionSelect(item.region_id)} aria-pressed={item.region_id === region.region_id}>
        <div className="region-dataset-title"><span className="region-dataset-pin"><MapPin size={14} /></span><span><strong>{item.town}</strong><small>{item.region_id}</small></span><span className="region-dataset-state">GIS 就绪</span></div>
        <div className="region-dataset-stats"><span><b>{integerFmt.format(item.demand_nodes)}</b><small>需求节点</small></span><span><b>{integerFmt.format(item.shelters)}</b><small>场所</small></span><span><b>{integerFmt.format(item.od_rows)}</b><small>OD 关系</small></span></div>
      </button>)}</div>
    </section>
    {cRegion?.warnings?.length ? <div className="notice-line"><AlertTriangle size={15} /> C 区域目录提示：{cRegion.warnings[0]}</div> : null}
    <div className="overview-map-card card">
      <div className="section-card-head"><div><span className="section-eyebrow">SPATIAL BASELINE</span><h2>区域空间基线</h2></div><div className="map-legend"><span><i className="legend-heat" />人口热度（可选）</span><span><i className="legend-shelter" />避难场所</span><span><i className="legend-outline" />分析边界</span></div></div>
      <MapView region={region} activeLayers={activeLayers} onActiveLayersChange={onActiveLayersChange} resultRows={resultRows} />
    </div>
    <div className="overview-bottom-grid">
      <section className="card coverage-card">
        <div className="section-card-head compact-head"><div><span className="section-eyebrow">NETWORK REACHABILITY</span><h2>步行可达候选人口</h2></div><span className="soft-tag">网络覆盖 · 不含容量分配</span></div>
        <ReactECharts option={coverageOption} style={{ height: 180, width: '100%' }} notMerge lazyUpdate />
        <div className="chart-note"><CircleHelp size={14} />图中统计表示至少存在网络候选路径的模型人口。</div>
      </section>
      <section className="card quick-task-card">
        <div className="section-card-head compact-head"><div><span className="section-eyebrow">START A RUN</span><h2>快速开始</h2></div><Sparkles size={17} className="accent-icon" /></div>
        <button className="quick-task" disabled={!sessionId} onClick={() => onRun('请评估当前区域的避难服务能力，步行阈值为 15 分钟，人口需求比例为 20%。')}><span className="quick-icon mint"><Activity size={16} /></span><span><strong>运行服务能力评估</strong><small>15 分钟步行 · 需求比例 20%</small></span><ArrowUpRight size={16} /></button>
        <button className="quick-task" disabled={!sessionId} onClick={() => onRun('请先说明当前区域做预算优化所需的预算和扩容候选项。')}><span className="quick-icon amber"><TrendingUp size={16} /></span><span><strong>准备预算优化</strong><small>候选项和预算由你明确提供</small></span><ArrowUpRight size={16} /></button>
        <div className="quick-task-foot"><BookOpenCheck size={14} />每次运行由 C Harness 记录状态并绑定当前会话。</div>
      </section>
    </div>
  </>
}

function KpiCard({ icon: Icon, label, value, detail, color }: { icon: LucideIcon; label: string; value: string; detail: string; color: string }) {
  return <div className="kpi-card card"><span className={`kpi-icon ${color}`}><Icon size={17} /></span><span className="kpi-label">{label}</span><strong className="kpi-value">{value}</strong><small>{detail}</small></div>
}

function SpatialPage({ region, activeLayers, onActiveLayersChange }: { region: RegionMetadata; activeLayers: string[]; onActiveLayersChange: (layers: string[]) => void }) {
  return <>
    <PageHeading eyebrow="A V2 DATA CATALOG" title="空间数据" subtitle="从已校验的区域 manifest 读取真实 GIS 图层；图层仅按需加载。" actions={<span className="data-status-tag"><Check size={13} />Manifest 匹配</span>} />
    <div className="spatial-stat-row"><div className="mini-stat"><span>坐标系</span><strong>EPSG:4326</strong></div><div className="mini-stat"><span>图层目录</span><strong>{region.layers.length} 项</strong></div><div className="mini-stat"><span>可显示 GeoJSON</span><strong>{region.layers.filter((layer) => layer.available).length} 项</strong></div><div className="mini-stat"><span>OD 矩阵</span><strong>{integerFmt.format(region.od_rows)} 条（不在浏览器加载）</strong></div></div>
    <div className="card spatial-map-card"><MapView region={region} activeLayers={activeLayers} onActiveLayersChange={onActiveLayersChange} /></div>
    <div className="card layer-catalog-card"><div className="section-card-head"><div><span className="section-eyebrow">AVAILABLE LAYERS</span><h2>图层目录</h2></div><span className="soft-tag">校验时按 A v2 SHA-256 manifest</span></div><div className="catalog-grid">
      {region.layers.map((layer) => <div className={`catalog-row ${!layer.available ? 'catalog-row-disabled' : ''}`} key={layer.layer_id}><span className={`catalog-color catalog-${layer.layer_id}`} /><div className="catalog-copy"><strong>{mapLabel(layer.layer_id)}</strong><small>{layer.description}</small></div><span className="catalog-type">{layer.geometry_type}</span><span className={`catalog-state ${layer.available ? 'ready' : ''}`}>{layer.available ? '可读取' : '未展示'}</span></div>)}
    </div><div className="data-source-note"><Database size={15} /><span>来源：WorldPop 2025 模型估算；步行网络来自静态 OpenStreetMap / Geofabrik 快照。容量、坐标、开放状态均有 demo 假设，细节见 A v2 质量摘要。</span></div></div>
  </>
}

function AssessmentPage({ region, onRun }: { region: RegionMetadata; onRun: (prompt: string) => void }) {
  const [walk, setWalk] = useState(15)
  const [ratio, setRatio] = useState(20)
  const sessionId = useWorkspace((state) => state.sessionId)
  return <>
    <PageHeading eyebrow="SERVICE ASSESSMENT" title="服务能力评估" subtitle="明确需求比例与步行阈值后，由 C Harness 提交当前区域评估。" />
    <div className="task-intro-card"><span className="task-intro-icon mint"><Activity size={18} /></span><div><strong>基于当前区域的基线评估</strong><p>{region.town} · {integerFmt.format(region.demand_nodes)} 个需求节点 · {integerFmt.format(region.shelters)} 处场所 · {region.data_status}</p></div><span className="api-badge">C session / turns</span></div>
    <div className="card form-card"><div className="section-card-head"><div><span className="section-eyebrow">TASK PARAMETERS</span><h2>评估参数</h2></div></div><div className="form-grid two-col">
      <label className="field-label">步行时间阈值 <span>分钟</span><div className="number-input"><input type="number" min={1} max={120} value={walk} onChange={(event) => setWalk(Number(event.target.value))} /><span>min</span></div><small>该数字将绑定到 walk time 参数。</small></label>
      <label className="field-label">人口需求比例 <span>%</span><div className="range-field"><input type="range" min={1} max={100} value={ratio} onChange={(event) => setRatio(Number(event.target.value))} /><b>{ratio}%</b></div><small>用于规划模型的需求参数，不代表实际疏散比例。</small></label>
    </div><div className="form-preview"><span>将提交给 C 的请求</span><code>评估当前区域，步行阈值 {walk} 分钟，人口需求比例 {ratio}%</code></div><button className="primary-button" disabled={!sessionId} onClick={() => onRun(`请评估当前区域的避难服务能力，步行阈值为 ${walk} 分钟，人口需求比例为 ${ratio}%。`)}><Play size={16} fill="currentColor" />提交评估</button></div>
    <section className="card method-card"><div className="method-mark"><ShieldCheck size={18} /></div><div><strong>口径说明</strong><p>评估结果来自 B 规划求解和 C 的验证流程。Solver 返回 `OPTIMAL` 或 `FEASIBLE` 时会保留原始状态；空间可达缺口不等于容量未满足人口。</p></div></section>
  </>
}

function ScenarioPage({ region, onRun }: { region: RegionMetadata; onRun: (prompt: string) => void }) {
  const [shelterId, setShelterId] = useState('')
  const sessionId = useWorkspace((state) => state.sessionId)
  const shelters = useQuery({ queryKey: ['shelter-features', region.region_id], queryFn: () => api.geoJson(region.region_id, 'shelter_capacity'), staleTime: 30 * 60_000 })
  const options = useMemo(() => (shelters.data?.features ?? []).map((feature) => String(feature.properties?.shelter_id ?? '')).filter(Boolean).sort(), [shelters.data])
  useEffect(() => { if (options.length && !options.includes(shelterId)) setShelterId(options[0]) }, [options, shelterId])
  return <>
    <PageHeading eyebrow="SCENARIO LAB" title="情景模拟" subtitle="选用 A v2 的真实场所 ID，描述停用影响并由 C 工作流执行比较。" />
    <div className="card form-card scenario-form"><div className="scenario-step"><span>01</span><div><strong>选择要模拟的场所</strong><small>来源于区域 shelter_capacity GeoJSON</small></div></div><label className="field-label">场所 ID<select value={shelterId} onChange={(event) => setShelterId(event.target.value)} disabled={!options.length}><option value="">{shelters.isLoading ? '读取场所清单…' : '没有可用场所'}</option>{options.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
      <div className="scenario-step second-step"><span>02</span><div><strong>生成停用对照请求</strong><small>不会修改 A 数据或正式场所状态</small></div></div><div className="scenario-preview"><AlertTriangle size={16} /><span>情景假设：暂时停用 <strong>{shelterId || '请选择场所'}</strong>，比较服务结果与当前基线。</span></div><button className="primary-button" disabled={!shelterId || !sessionId} onClick={() => onRun(`请执行压力测试：暂时停用 ${shelterId}，分析并说明对服务指标的影响。`)}><Play size={16} fill="currentColor" />运行情景对照</button>
      {shelters.isError && <div className="inline-error">场所目录读取失败：{shelters.error instanceof Error ? shelters.error.message : '未知错误'}</div>}
    </div>
    <div className="card scenario-explainer"><div className="explainer-icon"><Database size={17} /></div><div><strong>场所数据说明</strong><p>地图场所和容量来自 A v2 demo 清单。场所坐标可能为近似值，当前开放状态为演示假设。系统按 shelter_id 关联结果，不以名称或数组顺序匹配。</p></div></div>
  </>
}

function OptimizePage({ region, onRun }: { region: RegionMetadata; onRun: (prompt: string) => void }) {
  const [budget, setBudget] = useState('2')
  const sessionId = useWorkspace((state) => state.sessionId)
  const [candidates, setCandidates] = useState<Array<{ id: string; shelterId: string; capacity: string; cost: string }>>([{ id: 'candidate-1', shelterId: '', capacity: '300', cost: '1' }])
  const shelters = useQuery({ queryKey: ['shelter-features', region.region_id], queryFn: () => api.geoJson(region.region_id, 'shelter_capacity'), staleTime: 30 * 60_000 })
  const ids = useMemo(() => (shelters.data?.features ?? []).map((feature) => String(feature.properties?.shelter_id ?? '')).filter(Boolean).sort(), [shelters.data])
  useEffect(() => {
    if (ids.length && candidates.some((candidate) => !ids.includes(candidate.shelterId))) {
      setCandidates((current) => current.map((candidate, index) => ids.includes(candidate.shelterId) ? candidate : { ...candidate, shelterId: ids[index] ?? ids[0] }))
    }
  }, [ids, candidates])
  const isValid = Boolean(sessionId) && Number.isFinite(Number(budget)) && Number(budget) >= 0 && candidates.length <= 12 && candidates.length > 0 && candidates.every((candidate) => ids.includes(candidate.shelterId) && Number(candidate.capacity) > 0 && Number(candidate.cost) > 0)
  const prompt = `预算 ${budget}，${candidates.map((candidate) => `给 ${candidate.shelterId} 扩容 ${candidate.capacity} 人成本 ${candidate.cost}`).join('，')}，做预算优化。`
  const updateCandidate = (id: string, key: 'shelterId' | 'capacity' | 'cost', value: string) => setCandidates((current) => current.map((candidate) => candidate.id === id ? { ...candidate, [key]: value } : candidate))
  const addCandidate = () => setCandidates((current) => current.length >= 12 ? current : [...current, { id: `candidate-${crypto.randomUUID()}`, shelterId: ids.find((id) => !current.some((candidate) => candidate.shelterId === id)) ?? ids[0] ?? '', capacity: '300', cost: '1' }])
  return <>
    <PageHeading eyebrow="BUDGET OPTIMIZATION" title="预算优化" subtitle="只提交明确的预算与候选措施；新增容量和成本均作为 HYPOTHETICAL 情景输入。" actions={<span className="hypothetical-tag">HYPOTHETICAL 参数</span>} />
    <div className="optimization-warning"><AlertTriangle size={17} /><div><strong>情景参数，不是已核实的工程报价</strong><span>成本单位和容量增量由用户输入；最多 12 个候选项，遵守 B TaskRequest 公开限制。</span></div></div>
    <div className="card form-card optimization-card"><div className="section-card-head"><div><span className="section-eyebrow">IMPROVEMENT OPTIONS</span><h2>设置预算与候选措施</h2></div><span className="candidate-count">{candidates.length} / 12 候选</span></div>
      <div className="form-grid two-col"><label className="field-label">预算上限 <span>预算单位</span><input className="text-field" type="number" min="0" step="0.1" value={budget} onChange={(event) => setBudget(event.target.value)} /><small>例如 2。单位采用 B 情景定义，不自动换算成人民币。</small></label><div className="field-label">候选项数量 <span>最多 12 项</span><button className="secondary-button" type="button" onClick={addCandidate} disabled={candidates.length >= 12 || !ids.length}>＋ 添加一项措施 <small>{candidates.length} / 12</small></button><small>每项场所、增容量和成本由你分别确认。</small></div></div>
      {candidates.map((candidate, index) => <div className="candidate-editor" key={candidate.id}><div className="candidate-editor-title"><span>措施 {String(index + 1).padStart(2, '0')}</span><small>场所 ID 来自 A v2；增容量和成本为假设</small>{candidates.length > 1 && <button type="button" className="remove-candidate" onClick={() => setCandidates((current) => current.filter((item) => item.id !== candidate.id))}>移除</button>}</div><div className="form-grid candidate-grid"><label className="field-label">现有场所<select value={candidate.shelterId} onChange={(event) => updateCandidate(candidate.id, 'shelterId', event.target.value)} disabled={!ids.length}><option value="">{shelters.isLoading ? '读取场所…' : '无场所'}</option>{ids.map((id) => <option key={id} value={id}>{id}</option>)}</select></label><label className="field-label">假设增容 <span>人</span><input className="text-field" type="number" min="1" value={candidate.capacity} onChange={(event) => updateCandidate(candidate.id, 'capacity', event.target.value)} /></label><label className="field-label">假设成本 <span>预算单位</span><input className="text-field" type="number" min="0.01" step="0.1" value={candidate.cost} onChange={(event) => updateCandidate(candidate.id, 'cost', event.target.value)} /></label></div></div>)}
      <div className="form-preview"><span>预览将提交的自然语言请求</span><code>{prompt}</code></div>
      <button className="primary-button" disabled={!isValid} onClick={() => onRun(prompt)}><TrendingUp size={16} />提交预算优化</button>
    </div>
    <div className="method-card card"><div className="method-mark amber"><CircleHelp size={18} /></div><div><strong>求解状态会如实呈现</strong><p>OPTIMAL 表示已证明最优；FEASIBLE 表示找到可行解但未证明最优。未返回状态时界面不会推断。</p></div></div>
  </>
}

function RunsPage({ region, sessionId, result }: { region: RegionMetadata; sessionId: string | null; result: ReturnType<typeof useActiveResult> }) {
  const messages = useWorkspace((state) => state.messages)
  const [section, setSection] = useState('checks')
  const resultRows = result.layers?.layers.find((layer) => layer.layer_id === 'shelter_utilization')?.rows ?? []
  const [offset, setOffset] = useState(0)
  useEffect(() => setOffset(0), [section, result.resultId])
  const paginated = useQuery({
    queryKey: ['evidence', sessionId, result.resultId, section, offset],
    queryFn: () => api.evidence(sessionId!, result.resultId!, section, offset, 20),
    enabled: Boolean(sessionId && result.resultId), retry: false,
  })
  return <>
    <PageHeading eyebrow="AUDIT TRAIL" title="运行与证据" subtitle="查看当前浏览器会话的任务状态、结果分配与 C 提供的有界证据分页。" />
    <div className="card run-session-card"><div className="run-session-icon"><History size={18} /></div><div><span>当前 C 会话</span><strong>{sessionId ?? '等待创建会话…'}</strong></div><div className="run-region"><span>区域 / 版本</span><strong>{region.region_id} · {region.data_version}</strong></div></div>
    <div className="card runs-card"><div className="section-card-head"><div><span className="section-eyebrow">TURN HISTORY</span><h2>当前会话轮次</h2></div><span className="soft-tag">{messages.filter((message) => message.role === 'user').length} 条用户请求</span></div>
      {!messages.length ? <div className="empty-state"><Clock3 size={22} /><strong>当前浏览器会话暂无轮次</strong><span>在右侧 Agent 面板发起任务后，C 返回的五种状态会显示在这里。</span></div> : <div className="turn-timeline">{messages.filter((message) => message.role === 'assistant').map((message) => <TurnHistoryRow key={message.id} message={message} userMessage={messages.find((candidate) => candidate.id === `${message.id}-user`)} />)}</div>}
    </div>
    {result.resultId && <>
      <div className="card runs-card"><div className="section-card-head"><div><span className="section-eyebrow">SHELTER ASSIGNMENT</span><h2>场所负荷结果</h2></div><span className="result-id-tag">{result.resultId}</span></div>
        {resultRows.length ? <div className="table-wrap"><table><thead><tr><th>场所 ID</th><th>分配负荷</th><th>利用率</th></tr></thead><tbody>{resultRows.map((row) => <tr key={row.shelter_id}><td><code>{row.shelter_id}</code></td><td>{formatNumber(row.load_persons)} 人</td><td>{typeof row.utilization === 'number' ? `${(row.utilization * 100).toFixed(1)}%` : '—'}</td></tr>)}</tbody></table></div> : <div className="inline-hint">结果层不可用或当前结果没有场所分配明细。</div>}
      </div>
      <div className="card evidence-card"><div className="section-card-head"><div><span className="section-eyebrow">VERIFIER EVIDENCE</span><h2>验证证据分页</h2></div><span className="api-badge">session-scoped</span></div><div className="evidence-toolbar"><label>证据类型<select value={section} onChange={(event) => setSection(event.target.value)}>{['checks', 'optimization_checks', 'assignments', 'deficits', 'shelter_loads', 'diagnosis', 'trace', 'compare_rows', 'sensitivity_points'].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><span>{paginated.data ? `总计 ${paginated.data.total} 条` : '通过 C API 读取'}</span></div>
        {paginated.isLoading ? <div className="evidence-wait"><LoaderCircle className="spin" size={16} /> 正在读取证据…</div> : paginated.isError ? <div className="inline-error">读取失败：{paginated.error instanceof Error ? paginated.error.message : '证据不可用'}</div> : <><pre className="evidence-json">{JSON.stringify(paginated.data?.items ?? [], null, 2)}</pre><div className="pagination"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>{offset + 1}–{Math.min(offset + 20, paginated.data?.total ?? 0)}</span><button disabled={!paginated.data?.has_more} onClick={() => setOffset(offset + 20)}>下一页</button></div></>}
      </div>
    </>}
  </>
}

function TurnHistoryRow({ message, userMessage }: { message: WorkspaceMessage; userMessage?: WorkspaceMessage }) {
  const turn = message.turn
  const status = turn?.status ?? 'RUNNING'
  const answer = turn?.clarification?.question ?? turn?.message ?? (status === 'RUNNING' ? 'C Harness 正在执行…' : '')
  return <div className="timeline-row"><span className={`timeline-status status-${status.toLowerCase()}`}>{status === 'SUCCEEDED' ? <Check size={13} /> : status === 'RUNNING' ? <LoaderCircle size={13} /> : <AlertTriangle size={13} />}</span><div className="timeline-content"><div className="timeline-meta"><strong>{statusLabel(status)}</strong><span>{turn?.created_at ? new Date(turn.created_at).toLocaleString('zh-CN') : '本次会话'}</span>{turn?.task_type && <code>{turn.task_type}</code>}</div><p className="timeline-user">{userMessage?.text ?? turn?.user_text ?? '未返回请求文本'}</p>{answer && <p className="timeline-answer">{answer}</p>}{turn?.result_id && <span className="result-id-inline">result_id · {turn.result_id}</span>}</div></div>
}

function AssistantPanel({ region, online, sessionBusy, sessionError, capabilities, onRun, flow }: {
  region: RegionMetadata; online: boolean; sessionBusy: boolean; sessionError: string | null; capabilities?: CCapabilities
  onRun: (prompt: string) => void; flow: ReturnType<typeof useWorkflow>
}) {
  const messages = useWorkspace((state) => state.messages)
  const sessionId = useWorkspace((state) => state.sessionId)
  const activeStatus = useWorkspace((state) => state.activeStatus)
  const activeTurnId = useWorkspace((state) => state.activeTurnId)
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const loc = useLocation()
  const suggestions = loc.pathname === '/optimize'
    ? ['优化预算并说明 OPTIMAL / FEASIBLE 状态', '撤销上一步参数修改']
    : loc.pathname === '/scenario'
      ? ['对比步行阈值 10 分钟与 15 分钟', '撤销上一步情景调整']
      : ['评估 15 分钟步行服务能力', '说明当前 DEMO_ONLY 数据限制']
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [messages.length])
  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    if (!draft.trim()) return
    onRun(draft)
    setDraft('')
  }
  return <div className="assistant-panel card">
    <div className="assistant-head"><div className="agent-avatar"><Sparkles size={17} /></div><div><strong>规划 Agent</strong><span><i className={online ? 'online-dot' : 'offline-dot'} />{online ? 'C Harness 已连接' : '等待 C 服务'}</span></div><button className="assistant-more" aria-label="Agent 状态"><span /><span /><span /></button></div>
    <div className="assistant-context"><span className="context-pin"><MapPin size={13} /></span><span>分析区域</span><strong>{region.town}</strong><span className="context-separator">·</span><span>{region.data_status}</span></div>
    {!sessionId && <div className="session-banner">{sessionBusy ? <><LoaderCircle size={14} className="spin" />正在建立 C Session…</> : sessionError ? <><AlertTriangle size={14} />{sessionError}</> : '连接 C 服务后自动建立会话'}</div>}
    <div className="chat-messages" ref={(element) => { listRef.current = element }}>
      {!messages.length && <div className="agent-welcome"><div className="welcome-spark"><Sparkles size={19} /></div><strong>你好，我是 ShelterPlan 规划助手</strong><p>我可以帮你评估区域服务能力、比较情景，或在明确的预算与候选措施下运行优化。</p><div className="suggestion-list">{suggestions.map((suggestion) => <button key={suggestion} onClick={() => onRun(suggestion)} disabled={!online || !sessionId || flow.submitting}>{suggestion}<ArrowUpRight size={13} /></button>)}</div></div>}
      {messages.map((message) => message.role === 'user'
        ? <div className="chat-user" key={message.id}><div className="chat-user-bubble">{message.text}</div><span>你 · {new Date(message.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span></div>
        : <AgentMessage key={message.id} message={message} />)}
      {flow.localError && <div className="chat-error"><AlertTriangle size={14} /><span>{flow.localError}</span><button onClick={flow.clearError} aria-label="关闭错误"><X size={14} /></button></div>}
    </div>
    {flow.submitting && activeStatus === 'RUNNING' && <div className="turn-running"><LoaderCircle size={14} className="spin" /><span>C Harness 正在规划与验证…</span><button onClick={() => void flow.cancel()} disabled={!activeTurnId}><Pause size={13} />取消</button></div>}
    {activeStatus === 'NEEDS_CLARIFICATION' && <div className="clarify-tip"><CircleHelp size={14} />请补充 Agent 提出的字段；下一条回复会在当前 Session 中继续。</div>}
    <form className="chat-composer" onSubmit={submit}><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={online ? '描述你的评估或规划需求…' : 'C 服务连接后可提交任务'} rows={2} maxLength={4000} disabled={!online || !sessionId || flow.submitting} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} /><div className="composer-bottom"><span>{capabilities?.limits?.max_text_chars ?? 4000} 字上限 · Shift + Enter 换行</span><button type="submit" aria-label="发送请求" disabled={!draft.trim() || !online || !sessionId || flow.submitting}><Send size={16} /></button></div></form>
    <div className="assistant-disclosure"><ShieldCheck size={13} />结果受 DEMO_ONLY 数据质量限制；Solver 与 Verifier 状态会原样展示。</div>
  </div>
}

function AgentMessage({ message }: { message: WorkspaceMessage }) {
  const turn = message.turn
  if (!turn) return null
  const status = turn.status
  const errorCode = typeof turn.error === 'object' && turn.error ? turn.error.code : undefined
  const errorMessage = typeof turn.error === 'object' && turn.error ? turn.error.message ?? '' : typeof turn.error === 'string' ? turn.error : ''
  const sourceMismatch = status === 'FAILED' && errorCode === 'B_SCENARIO_INVALID' && /data_version|Module A source/i.test(errorMessage)
  const text = sourceMismatch
    ? `分析尚未进入求解器：C Session 已绑定 ${turn.region_id ?? '当前地区'}（${turn.data_version ?? 'A v2 数据版本'}），但 B 未找到匹配的数据源。请检查 configs/module_b.yaml 的 region_source_configs 是否登记该地区，并确认对应配置的 project.data_version 与 Session 一致；修改 B 配置后需重启服务。`
    : turn.clarification?.question ?? turn.message ?? (status === 'RUNNING' ? '正在处理你的请求…' : errorMessage)
  return <div className="chat-agent-message"><span className="agent-message-icon"><Sparkles size={14} /></span><div className="agent-message-body"><div className="agent-status-row"><span className={`status-chip status-${status.toLowerCase()}`}>{statusLabel(status)}</span>{turn.task_type && <code>{turn.task_type}</code>}</div>{text && <p>{text}</p>}
    {sourceMismatch && <details className="error-details"><summary>接口错误 · {errorCode}</summary><code>{errorMessage}</code></details>}
    {status === 'NEEDS_CLARIFICATION' && turn.clarification?.fields?.length ? <div className="clarification-fields">待补充：{turn.clarification.fields.join('、')}</div> : null}
    {status === 'SUCCEEDED' && <div className="agent-result-meta"><span>{turn.result_id ? `结果 ${turn.result_id}` : '任务完成'}</span>{turn.metrics && <MetricsStrip metrics={turn.metrics} />}</div>}
    {turn.data_status && <div className="message-data-foot">{turn.data_status} · {turn.data_version}</div>}
    {!!turn.warnings?.length && <div className="message-warning"><AlertTriangle size={12} />{turn.warnings.join('；')}</div>}
  </div></div>
}

function MetricsStrip({ metrics }: { metrics: Record<string, unknown> }) {
  const labels: Record<string, string> = { served_demand: '服务需求', unserved_demand: '未服务需求', coverage_ratio: '模型覆盖率', served_population: '服务人口', weighted_walk_time_min: '加权步行', solver_status: '求解状态', optimality: '最优性状态', objective_value: '目标值' }
  const entries = Object.entries(metrics).filter(([key, value]) => labels[key] && (typeof value === 'number' || typeof value === 'string')).slice(0, 4)
  if (!entries.length) return null
  return <div className="metrics-strip">{entries.map(([key, value]) => <span key={key}><small>{labels[key]}</small><b>{typeof value === 'number' ? formatNumber(value, key.includes('ratio')) : String(value)}</b></span>)}</div>
}

function statusLabel(status: CStatus) { return STATUS_LABELS[status] }

function mapLabel(layerId: string) {
  const labels: Record<string, string> = { boundary: '分析边界', analysis_area: '分析范围', shelters: '避难场所', shelter_capacity: '场所与容量', population_heatmap: '人口估算热度', population_density_raster: '人口密度栅格', coverage_10min: '10 分钟可达', coverage_15min: '15 分钟可达', coverage_20min: '20 分钟可达', coverage_30min: '30 分钟可达', access_gaps_10min: '10 分钟可达缺口', access_gaps_15min: '15 分钟可达缺口', access_gaps_20min: '20 分钟可达缺口', access_gaps_30min: '30 分钟可达缺口', network_buffer: '路网缓冲区', road_edges: '步行路网' }
  return labels[layerId] ?? layerId
}

export default function App() {
  return <AppShell />
}
