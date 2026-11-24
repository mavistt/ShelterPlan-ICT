import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  Activity, AlertTriangle, ChevronLeft, GitCompare, MapPin, Play, Plus,
  Search, Sparkles, Trash2, TrendingUp,
} from 'lucide-react'
import type { RegionMetadata, ShelterRecord, TaskMode } from '../api/types'
import { useWorkspace } from '../state/workspace'

type Props = {
  region: RegionMetadata
  shelters: ShelterRecord[]
  sessionReady: boolean
  submitting: boolean
  onRun: (prompt: string) => void
  onLayersPreset: (layers: string[]) => void
}

const MODE_TABS: Array<{ id: TaskMode; label: string; icon: typeof Activity }> = [
  { id: 'assess', label: '现状诊断', icon: Activity },
  { id: 'stress', label: '受损推演', icon: AlertTriangle },
  { id: 'optimize', label: '重建优化', icon: TrendingUp },
  { id: 'compare', label: '方案对比', icon: GitCompare },
]

const WALK_OPTIONS = [5, 10, 15, 20, 25, 30]
const SENS_CONFIG: Record<'max_walk_time_min' | 'population_demand_ratio' | 'budget_units', {
  label: string
  unit: string
  min: number
  max: number
  step: number
  defaultStart: number
  defaultEnd: number
}> = {
  max_walk_time_min: { label: '步行时间', unit: 'min', min: 5, max: 45, step: 5, defaultStart: 10, defaultEnd: 30 },
  population_demand_ratio: { label: '需求比例', unit: '%', min: 5, max: 40, step: 5, defaultStart: 5, defaultEnd: 25 },
  budget_units: { label: '重建预算', unit: '单位', min: 1, max: 8, step: 1, defaultStart: 1, defaultEnd: 4 },
}

function interpolateReachShare(coverage: RegionMetadata['coverage'], minutes: number): number {
  const anchors: Array<[number, number]> = [
    [0, 0],
    [10, (coverage['10']?.population_share ?? 0.21) * 100],
    [15, (coverage['15']?.population_share ?? 0.38) * 100],
    [20, (coverage['20']?.population_share ?? 0.49) * 100],
    [30, (coverage['30']?.population_share ?? 0.70) * 100],
    [45, Math.min(96, (coverage['30']?.population_share ?? 0.70) * 100 + 18)],
  ]
  if (minutes <= 0) return 0
  for (let i = 1; i < anchors.length; i += 1) {
    const [x0, y0] = anchors[i - 1]
    const [x1, y1] = anchors[i]
    if (minutes <= x1) {
      const t = (minutes - x0) / (x1 - x0)
      return Number((y0 + (y1 - y0) * t).toFixed(1))
    }
  }
  return Number(anchors[anchors.length - 1][1].toFixed(1))
}

function buildLinearGrid(start: number, end: number, count: number): number[] {
  const lo = Math.min(start, end)
  const hi = Math.max(start, end)
  if (count <= 2 || lo === hi) return [lo, hi]
  const pts: number[] = []
  for (let i = 0; i < count; i += 1) {
    const raw = lo + ((hi - lo) * i) / (count - 1)
    pts.push(Number(raw.toFixed(1)))
  }
  return [...new Set(pts)]
}

export function TaskDrawer({ region, shelters, sessionReady, submitting, onRun, onLayersPreset }: Props) {
  const activeMode = useWorkspace((s) => s.activeMode)
  const setActiveMode = useWorkspace((s) => s.setActiveMode)
  const setDrawerOpen = useWorkspace((s) => s.setDrawerOpen)
  const selectedShelterId = useWorkspace((s) => s.selectedShelterId)
  const flyTo = useWorkspace((s) => s.flyTo)
  const stressedShelterIds = useWorkspace((s) => s.stressedShelterIds)
  const toggleStressedShelter = useWorkspace((s) => s.toggleStressedShelter)
  const setStressedShelterIds = useWorkspace((s) => s.setStressedShelterIds)
  const optCandidates = useWorkspace((s) => s.optCandidates)
  const setOptCandidates = useWorkspace((s) => s.setOptCandidates)
  const addOptCandidate = useWorkspace((s) => s.addOptCandidate)

  // Assess state
  const [walkMin, setWalkMin] = useState(15)
  const [demandRatio, setDemandRatio] = useState(10)
  const [searchQuery, setSearchQuery] = useState('')

  // Optimize state
  const [budget, setBudget] = useState(2)
  const [policy, setPolicy] = useState<'stackable' | 'exclusive_group'>('stackable')

  // Compare / Sensitivity state
  const [compareSubMode, setCompareSubMode] = useState<'compare' | 'sensitivity'>('compare')
  const [selectedWalkThresholds, setSelectedWalkThresholds] = useState<number[]>([10, 15, 20])
  const [sensParam, setSensParam] = useState<'max_walk_time_min' | 'population_demand_ratio' | 'budget_units'>('max_walk_time_min')
  const [sensStart, setSensStart] = useState(10)
  const [sensEnd, setSensEnd] = useState(30)
  const [sensCount, setSensCount] = useState(4)
  const sensPoints = useMemo(() => buildLinearGrid(sensStart, sensEnd, sensCount), [sensStart, sensEnd, sensCount])

  const sortedShelters = useMemo(() => [...shelters].sort((a, b) => b.capacity - a.capacity), [shelters])
  const shelterMap = useMemo(() => new Map(shelters.map((s) => [s.shelter_id, s])), [shelters])

  // Seed default optCandidates once shelters load if empty
  useEffect(() => {
    if (sortedShelters.length >= 3 && optCandidates.length === 0) {
      const top1 = sortedShelters[0]
      const top2 = sortedShelters[1]
      const mid = sortedShelters.find((s) => s.capacity >= 100 && s.capacity <= 1500) ?? sortedShelters[2]
      setOptCandidates([
        { id: 'preset-1', shelterId: top1.shelter_id, capacity: '600', cost: '2' },
        { id: 'preset-2', shelterId: top2.shelter_id, capacity: '450', cost: '1' },
        { id: 'preset-3', shelterId: mid.shelter_id, capacity: '420', cost: '1' },
      ])
    }
  }, [optCandidates.length, setOptCandidates, sortedShelters])

  const filteredShelters = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return sortedShelters
    return sortedShelters.filter((s) =>
      s.name.toLowerCase().includes(q)
      || s.address.toLowerCase().includes(q)
      || s.shelter_id.toLowerCase().includes(q),
    )
  }, [searchQuery, sortedShelters])

  const coverageChartOption = useMemo(() => ({
    animation: false,
    grid: { left: 8, right: 8, top: 18, bottom: 20, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (items: Array<{ axisValue: string; value: number }>) => {
        const v = items[0]?.value ?? 0
        return `${items[0]?.axisValue} 分钟步行圈<br/>可达人口 ${(v / 10000).toFixed(1)} 万`
      },
    },
    xAxis: {
      type: 'category',
      data: ['10m', '15m', '20m', '30m'],
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
      axisLabel: { color: '#64748b', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      splitNumber: 3,
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => `${Math.round(v / 10000)}万` },
    },
    series: [{
      type: 'bar',
      barWidth: 20,
      data: ['10', '15', '20', '30'].map((t) => Math.round(region.coverage[t]?.population_with_candidate ?? 0)),
      itemStyle: { color: '#0d9488', borderRadius: [4, 4, 0, 0] },
    }],
  }), [region.coverage])

  const comparePreviewOption = useMemo(() => {
    const pts = [...selectedWalkThresholds].sort((a, b) => a - b)
    const vals = pts.map((m) => interpolateReachShare(region.coverage, m))
    return {
      animation: true,
      animationDuration: 260,
      grid: { left: 8, right: 12, top: 18, bottom: 20, containLabel: true },
      tooltip: {
        trigger: 'axis',
        formatter: (items: Array<{ axisValue: string; value: number }>) => {
          const share = items[0]?.value ?? 0
          const pop = Math.round((region.population_estimate * share) / 100)
          return `${items[0]?.axisValue} 步行圈<br/>路网可达率：<b>${share}%</b>（约 ${(pop / 10000).toFixed(1)} 万人）`
        },
      },
      xAxis: {
        type: 'category',
        data: pts.map((m) => `${m}m`),
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: { color: '#475569', fontSize: 11, fontWeight: 600 },
      },
      yAxis: {
        type: 'value',
        max: 100,
        splitNumber: 3,
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: '{value}%' },
      },
      series: [
        {
          type: 'bar',
          barWidth: 22,
          data: vals,
          itemStyle: { color: 'rgba(13, 148, 136, 0.22)', borderRadius: [5, 5, 0, 0] },
        },
        {
          type: 'line',
          smooth: 0.3,
          symbolSize: 8,
          data: vals,
          lineStyle: { color: '#0d9488', width: 2.4 },
          itemStyle: { color: '#0d9488', borderColor: '#ffffff', borderWidth: 2 },
        },
      ],
    }
  }, [region.coverage, region.population_estimate, selectedWalkThresholds])

  const sensPreviewOption = useMemo(() => {
    const unit = SENS_CONFIG[sensParam].unit
    const baseCap = shelters.reduce((sum, s) => sum + s.capacity, 0) || 24000
    const yValues = sensPoints.map((x) => {
      if (sensParam === 'max_walk_time_min') return interpolateReachShare(region.coverage, x)
      if (sensParam === 'population_demand_ratio') {
        const demand = (region.population_estimate * x) / 100
        return Number(Math.min(100, (baseCap / Math.max(demand, 1)) * 100).toFixed(1))
      }
      // budget_units: diminishing marginal gain preview
      const baseCov = interpolateReachShare(region.coverage, 15) * 0.28
      return Number(Math.min(100, baseCov + Math.sqrt(x) * 4.2).toFixed(1))
    })
    return {
      animation: true,
      animationDuration: 240,
      grid: { left: 8, right: 12, top: 16, bottom: 20, containLabel: true },
      tooltip: {
        trigger: 'axis',
        formatter: (items: Array<{ axisValue: string; value: number }>) =>
          `采样点 ${items[0]?.axisValue}<br/>预期覆盖趋势：<b>${items[0]?.value}%</b>`,
      },
      xAxis: {
        type: 'category',
        data: sensPoints.map((p) => `${p}${unit}`),
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: { color: '#475569', fontSize: 10.5 },
      },
      yAxis: {
        type: 'value',
        splitNumber: 3,
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: '{value}%' },
      },
      series: [{
        type: 'line',
        smooth: 0.35,
        symbolSize: 8,
        data: yValues,
        lineStyle: { color: '#0284c7', width: 2.5 },
        areaStyle: { color: 'rgba(2, 132, 199, 0.10)' },
        itemStyle: { color: '#0284c7', borderColor: '#ffffff', borderWidth: 2 },
      }],
    }
  }, [region.coverage, region.population_estimate, sensParam, sensPoints, shelters])

  const loadKnapsackPreset = () => {
    if (sortedShelters.length < 3) return
    const top1 = sortedShelters[0]
    const top2 = sortedShelters[1]
    const mid = sortedShelters.find((s) => s.capacity >= 100 && s.capacity <= 1500) ?? sortedShelters[2]
    setBudget(2)
    setPolicy('stackable')
    setOptCandidates([
      { id: `knap-${crypto.randomUUID()}`, shelterId: top1.shelter_id, capacity: '600', cost: '2' },
      { id: `knap-${crypto.randomUUID()}`, shelterId: top2.shelter_id, capacity: '450', cost: '1' },
      { id: `knap-${crypto.randomUUID()}`, shelterId: mid.shelter_id, capacity: '420', cost: '1' },
    ])
  }

  const totalCandidateCost = optCandidates.reduce((sum, c) => sum + (Number(c.cost) || 0), 0)
  const totalCandidateCap = optCandidates.reduce((sum, c) => sum + (Number(c.capacity) || 0), 0)
  const optValid = sessionReady
    && !submitting
    && Number.isFinite(budget)
    && budget >= 0
    && optCandidates.length > 0
    && optCandidates.length <= 12
    && optCandidates.every((c) => shelterMap.has(c.shelterId) && Number(c.capacity) > 0 && Number(c.cost) > 0)

  const handleRunOptimize = () => {
    const policyText = policy === 'exclusive_group' ? '（候选方案互斥，最多选一项）' : ''
    const items = optCandidates
      .map((c) => `给 ${c.shelterId} 扩容 ${c.capacity} 人成本 ${c.cost}`)
      .join('，')
    onLayersPreset(['boundary', 'population_heatmap', 'coverage_15min', 'shelter_capacity'])
    onRun(`预算 ${budget}，${items}${policyText}，做预算优化。`)
  }

  const handleRunStress = () => {
    if (!stressedShelterIds.length) return
    const desc = stressedShelterIds
      .map((id) => {
        const s = shelterMap.get(id)
        return s ? `${id}（${s.name}）` : id
      })
      .join('、')
    onLayersPreset(['boundary', 'population_heatmap', 'shelter_capacity'])
    onRun(`请执行压力测试：暂时停用 ${desc}，分析对服务覆盖率、未服务人数和周边场所负载的影响。`)
  }

  const toggleWalkThreshold = (v: number) => {
    setSelectedWalkThresholds((prev) => {
      const next = prev.includes(v)
        ? (prev.length > 2 ? prev.filter((x) => x !== v) : prev)
        : [...prev, v].sort((a, b) => a - b)
      return next
    })
    if ([10, 15, 20, 30].includes(v)) {
      onLayersPreset(['boundary', 'population_heatmap', `coverage_${v}min`, 'shelter_capacity'])
    }
  }

  const handleRunCompare = () => {
    if (compareSubMode === 'compare') {
      if (selectedWalkThresholds.length < 2) return
      onLayersPreset(['boundary', 'coverage_15min', 'access_gaps_15min', 'shelter_capacity'])
      onRun(`请对比步行阈值 ${selectedWalkThresholds.join(' 分钟、')} 分钟下的避难服务覆盖率与未服务人口差异。`)
    } else {
      if (sensPoints.length < 2) return
      const label = sensParam === 'max_walk_time_min'
        ? `步行阈值 ${sensPoints.join('、')} 分钟`
        : sensParam === 'population_demand_ratio'
          ? `人口需求比例 ${sensPoints.map((n) => `${n}%`).join('、')}`
          : `预算 ${sensPoints.join('、')}`
      onRun(`请对 ${label} 执行敏感性分析，并检查单调性与拐点。`)
    }
  }

  const totalStressedCap = stressedShelterIds.reduce((sum, id) => sum + (shelterMap.get(id)?.capacity ?? 0), 0)

  return (
    <aside className="task-drawer" aria-label="灾后规划任务工作台">
      <div className="drawer-tabs" role="tablist">
        {MODE_TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeMode === id}
            className={`drawer-tab ${activeMode === id ? 'active' : ''}`}
            onClick={() => setActiveMode(id)}
          >
            <Icon size={14} />
            <span>{label}</span>
          </button>
        ))}
        <button type="button" className="drawer-collapse-btn" onClick={() => setDrawerOpen(false)} title="收起任务面板" aria-label="收起任务面板">
          <ChevronLeft size={15} />
        </button>
      </div>

      <div className="drawer-body">
        {activeMode === 'assess' && (
          <>
            <div className="drawer-section">
              <div className="ds-head">
                <strong>避难服务能力诊断</strong>
                <span className="ds-pill">基线评估</span>
              </div>
              <p className="ds-desc">设定灾后紧急疏散步行阈值与转移比例，实时核算可覆盖人群与承载缺口。</p>

              <div className="field-block">
                <div className="field-row">
                  <label htmlFor="walk-min-range">步行时间阈值</label>
                  <div className="inline-num-wrap">
                    <input
                      type="number"
                      min={1}
                      max={60}
                      step={0.5}
                      value={walkMin}
                      onChange={(e) => {
                        const v = Number(e.target.value)
                        if (Number.isFinite(v)) setWalkMin(Math.max(1, Math.min(60, v)))
                      }}
                      aria-label="步行时间数值"
                    />
                    <span>分钟</span>
                  </div>
                </div>
                <input id="walk-min-range" type="range" min={1} max={60} step={0.5} value={walkMin} onChange={(e) => setWalkMin(Number(e.target.value))} />
                <div className="chip-row">
                  {[10, 15, 20, 30].map((v) => (
                    <button key={v} type="button" className={`mini-chip ${walkMin === v ? 'active' : ''}`} onClick={() => setWalkMin(v)}>
                      {v} min
                    </button>
                  ))}
                </div>
              </div>

              <div className="field-block">
                <div className="field-row">
                  <label htmlFor="demand-ratio-range">应急转移需求比例</label>
                  <div className="inline-num-wrap">
                    <input
                      type="number"
                      min={1}
                      max={50}
                      step={1}
                      value={demandRatio}
                      onChange={(e) => {
                        const v = Number(e.target.value)
                        if (Number.isFinite(v)) setDemandRatio(Math.max(1, Math.min(50, v)))
                      }}
                      aria-label="需求比例数值"
                    />
                    <span>%</span>
                  </div>
                </div>
                <input id="demand-ratio-range" type="range" min={1} max={50} step={0.5} value={demandRatio} onChange={(e) => setDemandRatio(Number(e.target.value))} />
                <div className="chip-row">
                  {[
                    { v: 5, t: '5% 局部转移' },
                    { v: 10, t: '10% 强台风' },
                    { v: 20, t: '20% 极限压测' },
                  ].map((item) => (
                    <button key={item.v} type="button" className={`mini-chip ${demandRatio === item.v ? 'active' : ''}`} onClick={() => setDemandRatio(item.v)}>
                      {item.t}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                className="primary-action-btn"
                disabled={!sessionReady || submitting}
                onClick={() => {
                  onLayersPreset(['boundary', 'population_heatmap', 'shelter_capacity'])
                  onRun(`请评估当前区域的避难服务能力，步行阈值为 ${walkMin} 分钟，人口需求比例为 ${demandRatio}%。`)
                }}
              >
                <Play size={14} fill="currentColor" /> 运行服务能力评估
              </button>
            </div>

            <div className="drawer-section">
              <div className="ds-head">
                <strong>步行圈可达人口分布</strong>
                <span className="ds-sub">路网可达基线</span>
              </div>
              <ReactECharts option={coverageChartOption} style={{ height: 140, width: '100%' }} notMerge lazyUpdate />
            </div>

            <div className="drawer-section">
              <div className="ds-head">
                <strong>避难场所名录 ({shelters.length})</strong>
                <span className="ds-sub">点击定位地图</span>
              </div>
              <div className="search-box">
                <Search size={13} />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="搜索场所名称、街道或 ID…"
                />
              </div>
              <div className="shelter-list">
                {filteredShelters.slice(0, 18).map((s) => (
                  <button
                    key={s.shelter_id}
                    type="button"
                    className={`shelter-list-item ${selectedShelterId === s.shelter_id ? 'is-selected' : ''}`}
                    onClick={() => flyTo(s.lon, s.lat, s.shelter_id)}
                  >
                    <MapPin size={13} className="sli-pin" />
                    <div className="sli-copy">
                      <strong>{s.name}</strong>
                      <small>{s.address || s.shelter_id}</small>
                    </div>
                    <span className="sli-cap">{s.capacity.toLocaleString('zh-CN')}人</span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {activeMode === 'stress' && (
          <div className="drawer-section">
            <div className="ds-head">
              <strong>设施受损 / 停用压力推演</strong>
              <span className="ds-pill danger">支持多点联选</span>
            </div>
            <p className="ds-desc">
              直接点击地图上的任意避难场所选择「模拟此点受损」，或从下方勾选主力场所，评估灾后设施失效的冲击。
            </p>

            <div className="quick-preset-bar">
              <button
                type="button"
                className="subtle-pill-action"
                onClick={() => {
                  const top1 = sortedShelters[0]
                  if (top1) {
                    setStressedShelterIds([top1.shelter_id])
                    flyTo(top1.lon, top1.lat, top1.shelter_id)
                  }
                }}
              >
                <Sparkles size={13} /> 选中最大主力点 ({sortedShelters[0]?.name.slice(0, 8) ?? '—'})
              </button>
              {stressedShelterIds.length > 0 && (
                <button type="button" className="ghost-clear-btn" onClick={() => setStressedShelterIds([])}>
                  清空
                </button>
              )}
            </div>

            <div className="selected-chips-box">
              {!stressedShelterIds.length ? (
                <div className="empty-mini">尚未选择受损场所。请点击地图气泡或下方列表添加。</div>
              ) : (
                <>
                  <div className="stress-summary">
                    已标记 <b>{stressedShelterIds.length}</b> 处停用 · 损失容量 <b>{totalStressedCap.toLocaleString('zh-CN')} 人</b>
                  </div>
                  {stressedShelterIds.map((id) => {
                    const s = shelterMap.get(id)
                    return (
                      <div key={id} className="stress-chip">
                        <button type="button" className="stress-chip-loc" onClick={() => s && flyTo(s.lon, s.lat, s.shelter_id)}>
                          <strong>{s?.name ?? id}</strong>
                          <small>{s ? `${s.capacity.toLocaleString('zh-CN')}人 · ${id}` : id}</small>
                        </button>
                        <button type="button" className="icon-del-btn" onClick={() => toggleStressedShelter(id)} aria-label="移除受损点">
                          <Trash2 size={13} />
                        </button>
                      </div>
                    )
                  })}
                </>
              )}
            </div>

            <button
              type="button"
              className="primary-action-btn danger-btn"
              disabled={!sessionReady || submitting || !stressedShelterIds.length}
              onClick={handleRunStress}
            >
              <AlertTriangle size={14} /> 运行受损压力推演
            </button>

            <div className="sub-list-title">快速勾选区域场所</div>
            <div className="shelter-list compact">
              {sortedShelters.slice(0, 12).map((s) => {
                const checked = stressedShelterIds.includes(s.shelter_id)
                return (
                  <button
                    key={s.shelter_id}
                    type="button"
                    className={`shelter-list-item ${checked ? 'is-danger-selected' : ''}`}
                    onClick={() => {
                      toggleStressedShelter(s.shelter_id)
                      flyTo(s.lon, s.lat, s.shelter_id)
                    }}
                  >
                    <span className={`check-dot ${checked ? 'checked' : ''}`} />
                    <div className="sli-copy">
                      <strong>{s.name}</strong>
                      <small>{s.shelter_id}</small>
                    </div>
                    <span className="sli-cap">{s.capacity.toLocaleString('zh-CN')}人</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {activeMode === 'optimize' && (
          <div className="drawer-section">
            <div className="ds-head">
              <strong>灾后重建预算最优分配</strong>
              <button type="button" className="subtle-link-btn" onClick={loadKnapsackPreset} title="载入 3 方案经典背包对比">
                <Sparkles size={12} /> 经典 3 方案预设
              </button>
            </div>

            {/* Apple-style Budget Allocation Overview Card */}
            <div className="budget-meter-card">
              <div className="bm-top">
                <div>
                  <span className="bm-kicker">重建财政预算上限</span>
                  <div className="bm-val-row">
                    <b>{budget.toFixed(1)}</b>
                    <span>预算单位</span>
                  </div>
                </div>
                <div className="bm-stepper">
                  <button type="button" onClick={() => setBudget((b) => Math.max(0.5, Number((b - 0.5).toFixed(1))))} aria-label="减少预算">−</button>
                  <button type="button" onClick={() => setBudget((b) => Math.min(20, Number((b + 0.5).toFixed(1))))} aria-label="增加预算">+</button>
                </div>
              </div>
              <input
                type="range"
                min={0.5}
                max={10}
                step={0.5}
                value={Math.min(10, budget)}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
              <div className="bm-stats-row">
                <span>候选池总成本 <b>{totalCandidateCost.toFixed(1)}</b></span>
                <span>总潜在增容 <b>+{totalCandidateCap.toLocaleString('zh-CN')} 人</b></span>
              </div>
              <div className="segmented-row subtle-seg">
                <button type="button" className={policy === 'stackable' ? 'active' : ''} onClick={() => setPolicy('stackable')}>
                  可叠加组合
                </button>
                <button type="button" className={policy === 'exclusive_group' ? 'active' : ''} onClick={() => setPolicy('exclusive_group')}>
                  互斥单选组
                </button>
              </div>
            </div>

            <div className="cand-list-head">
              <span>候选扩容项目 ({optCandidates.length}/12)</span>
              <button
                type="button"
                className="subtle-link-btn"
                disabled={optCandidates.length >= 12 || !sortedShelters.length}
                onClick={() => {
                  const next = sortedShelters.find((s) => !optCandidates.some((c) => c.shelterId === s.shelter_id)) ?? sortedShelters[0]
                  if (next) addOptCandidate(next.shelter_id, '400', '1')
                }}
              >
                <Plus size={12} /> 添加候选点
              </button>
            </div>

            <div className="cand-list">
              {optCandidates.map((cand, idx) => {
                const s = shelterMap.get(cand.shelterId)
                const eff = Number(cand.cost) > 0 ? Math.round((Number(cand.capacity) || 0) / Number(cand.cost)) : 0
                return (
                  <div key={cand.id} className="alloc-card">
                    <div className="alloc-card-head">
                      <span className="alloc-rank">{String(idx + 1).padStart(2, '0')}</span>
                      <select
                        className="alloc-select"
                        value={cand.shelterId}
                        onChange={(e) => {
                          const sid = e.target.value
                          setOptCandidates(optCandidates.map((c) => c.id === cand.id ? { ...c, shelterId: sid } : c))
                          const target = shelterMap.get(sid)
                          if (target) flyTo(target.lon, target.lat, target.shelter_id)
                        }}
                      >
                        {sortedShelters.map((item) => (
                          <option key={item.shelter_id} value={item.shelter_id}>
                            {item.name}（现容 {item.capacity}）
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="alloc-icon-btn"
                        onClick={() => s && flyTo(s.lon, s.lat, s.shelter_id)}
                        title="定位到地图"
                        aria-label="定位到地图"
                      >
                        <MapPin size={13} />
                      </button>
                      {optCandidates.length > 1 && (
                        <button
                          type="button"
                          className="alloc-icon-btn danger"
                          onClick={() => setOptCandidates(optCandidates.filter((c) => c.id !== cand.id))}
                          title="移除"
                          aria-label="移除候选措施"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                    <div className="alloc-card-metrics">
                      <label className="unit-field">
                        <span>扩容增量</span>
                        <div className="unit-input">
                          <input
                            type="number"
                            min="10"
                            step="50"
                            value={cand.capacity}
                            onChange={(e) => setOptCandidates(optCandidates.map((c) => c.id === cand.id ? { ...c, capacity: e.target.value } : c))}
                          />
                          <em>人</em>
                        </div>
                      </label>
                      <label className="unit-field">
                        <span>所需预算</span>
                        <div className="unit-input">
                          <input
                            type="number"
                            min="0.5"
                            step="0.5"
                            value={cand.cost}
                            onChange={(e) => setOptCandidates(optCandidates.map((c) => c.id === cand.id ? { ...c, cost: e.target.value } : c))}
                          />
                          <em>单位</em>
                        </div>
                      </label>
                      <div className="alloc-eff">
                        <span>单位性价比</span>
                        <b>{eff} 人/单位</b>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            <button
              type="button"
              className="primary-action-btn"
              disabled={!optValid}
              onClick={handleRunOptimize}
            >
              <TrendingUp size={14} /> 求解全局最优组合 (MIP vs 贪心)
            </button>
          </div>
        )}

        {activeMode === 'compare' && (
          <div className="drawer-section">
            <div className="ds-head">
              <strong>多情景对比与韧性拐点</strong>
              <span className="ds-pill teal">实时预览</span>
            </div>

            <div className="segmented-row">
              <button type="button" className={compareSubMode === 'compare' ? 'active' : ''} onClick={() => setCompareSubMode('compare')}>
                步行阈值对比
              </button>
              <button type="button" className={compareSubMode === 'sensitivity' ? 'active' : ''} onClick={() => setCompareSubMode('sensitivity')}>
                参数敏感性扫描
              </button>
            </div>

            {compareSubMode === 'compare' ? (
              <>
                <div className="compare-card">
                  <div className="cc-title">
                    <span>点击阈值胶囊（同步切换地图等时圈）</span>
                    <b>已选 {selectedWalkThresholds.length} 项</b>
                  </div>
                  <div className="pill-matrix">
                    {WALK_OPTIONS.map((v) => {
                      const active = selectedWalkThresholds.includes(v)
                      return (
                        <button
                          key={v}
                          type="button"
                          className={`matrix-pill ${active ? 'active' : ''}`}
                          onClick={() => toggleWalkThreshold(v)}
                        >
                          <strong>{v}</strong>
                          <span>min</span>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="compare-card">
                  <div className="cc-title">
                    <span>所选阈值可达率动态对比</span>
                    {selectedWalkThresholds.length >= 2 && (() => {
                      const sorted = [...selectedWalkThresholds].sort((a, b) => a - b)
                      const diff = interpolateReachShare(region.coverage, sorted[sorted.length - 1])
                        - interpolateReachShare(region.coverage, sorted[0])
                      return <b>+{diff.toFixed(1)}% 增益</b>
                    })()}
                  </div>
                  <ReactECharts option={comparePreviewOption} style={{ height: 152, width: '100%' }} notMerge />
                </div>
              </>
            ) : (
              <>
                <div className="segmented-row three-col">
                  {(Object.keys(SENS_CONFIG) as Array<keyof typeof SENS_CONFIG>).map((key) => (
                    <button
                      key={key}
                      type="button"
                      className={sensParam === key ? 'active' : ''}
                      onClick={() => {
                        setSensParam(key)
                        setSensStart(SENS_CONFIG[key].defaultStart)
                        setSensEnd(SENS_CONFIG[key].defaultEnd)
                      }}
                    >
                      {SENS_CONFIG[key].label}
                    </button>
                  ))}
                </div>

                <div className="compare-card">
                  <div className="cc-title">
                    <span>扫描区间与步数</span>
                    <b>
                      {Math.min(sensStart, sensEnd)} – {Math.max(sensStart, sensEnd)} {SENS_CONFIG[sensParam].unit} · {sensPoints.length} 点
                    </b>
                  </div>
                  <div className="sens-sliders">
                    <label className="sens-slider-row">
                      <span>起点</span>
                      <input
                        type="range"
                        min={SENS_CONFIG[sensParam].min}
                        max={SENS_CONFIG[sensParam].max - SENS_CONFIG[sensParam].step}
                        step={SENS_CONFIG[sensParam].step}
                        value={sensStart}
                        onChange={(e) => {
                          const v = Number(e.target.value)
                          setSensStart(v)
                          if (v >= sensEnd) setSensEnd(Math.min(SENS_CONFIG[sensParam].max, v + SENS_CONFIG[sensParam].step))
                        }}
                      />
                      <b>{sensStart}{SENS_CONFIG[sensParam].unit}</b>
                    </label>
                    <label className="sens-slider-row">
                      <span>终点</span>
                      <input
                        type="range"
                        min={SENS_CONFIG[sensParam].min + SENS_CONFIG[sensParam].step}
                        max={SENS_CONFIG[sensParam].max}
                        step={SENS_CONFIG[sensParam].step}
                        value={sensEnd}
                        onChange={(e) => {
                          const v = Number(e.target.value)
                          setSensEnd(v)
                          if (v <= sensStart) setSensStart(Math.max(SENS_CONFIG[sensParam].min, v - SENS_CONFIG[sensParam].step))
                        }}
                      />
                      <b>{sensEnd}{SENS_CONFIG[sensParam].unit}</b>
                    </label>
                    <label className="sens-slider-row">
                      <span>采样数</span>
                      <input
                        type="range"
                        min={2}
                        max={6}
                        step={1}
                        value={sensCount}
                        onChange={(e) => setSensCount(Number(e.target.value))}
                      />
                      <b>{sensCount} 档</b>
                    </label>
                  </div>
                </div>

                <div className="compare-card">
                  <div className="cc-title">
                    <span>实时趋势预览（拖动滑块联动）</span>
                    <span className="ds-sub">{SENS_CONFIG[sensParam].label}</span>
                  </div>
                  <ReactECharts option={sensPreviewOption} style={{ height: 146, width: '100%' }} notMerge />
                </div>
              </>
            )}

            <button
              type="button"
              className="primary-action-btn"
              disabled={!sessionReady || submitting}
              onClick={handleRunCompare}
            >
              <GitCompare size={14} /> {compareSubMode === 'compare' ? '运行多阈值方案对比' : '执行单调性与敏感性扫描'}
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}
