import { Activity, AlertTriangle, ArrowRight, Compass, GitCompare, ShieldCheck, TrendingUp, X } from 'lucide-react'
import type { RegionMetadata, ShelterRecord, TaskMode } from '../api/types'

export type ScenarioPreset = {
  id: string
  mode: TaskMode
  title: string
  badge: string
  description: string
  layers: string[]
  prompt: string
  focusShelterId?: string
  stressIds?: string[]
  optPreset?: Array<{ shelterId: string; capacity: string; cost: string }>
}

type Props = {
  region: RegionMetadata
  shelters: ShelterRecord[]
  sessionReady: boolean
  onDismiss: () => void
  onLaunchScenario: (preset: ScenarioPreset) => void
}

export function buildRegionScenarios(_region: RegionMetadata, shelters: ShelterRecord[]): ScenarioPreset[] {
  const byCapDesc = [...shelters].sort((a, b) => b.capacity - a.capacity)
  const top1 = byCapDesc[0]
  const top2 = byCapDesc[1]
  const top3 = byCapDesc[2]
  const mid = byCapDesc.find((s) => s.capacity >= 100 && s.capacity <= 1500) ?? byCapDesc[Math.min(3, byCapDesc.length - 1)]

  const stressTarget = top1
  const optA = top1
  const optB = top2 ?? top1
  const optC = mid ?? top3 ?? top1

  const optPrompt = optA && optB && optC && optA.shelter_id !== optB.shelter_id
    ? `预算 2，给 ${optA.shelter_id} 扩容 600 人成本 2，给 ${optB.shelter_id} 扩容 450 人成本 1，给 ${optC.shelter_id} 扩容 420 人成本 1，做预算优化并对比贪心方案。`
    : '请基于当前区域避难场所做预算优化评估。'

  return [
    {
      id: 'baseline-gap',
      mode: 'assess',
      title: '灾后服务盲区摸底',
      badge: '现状诊断',
      description: '叠加人口热力与 15 分钟步行可达圈，识别高密度人群与避难容量缺口分布。',
      layers: ['boundary', 'population_heatmap', 'shelter_capacity'],
      prompt: '请评估当前区域的避难服务能力，步行阈值为 15 分钟，人口需求比例为 10%。',
    },
    {
      id: 'outage-stress',
      mode: 'stress',
      title: '核心场所受损推演',
      badge: '抗毁压测',
      description: stressTarget
        ? `模拟主力点「${stressTarget.name}」(${stressTarget.capacity.toLocaleString('zh-CN')}人) 因内涝停用，测算周边超载与覆盖跌落。`
        : '模拟区域主力避难所因次生灾害停用后的服务能力跌落与缺口扩散。',
      layers: ['boundary', 'population_heatmap', 'shelter_capacity'],
      prompt: stressTarget
        ? `请执行压力测试：暂时停用 ${stressTarget.shelter_id}（${stressTarget.name}），分析对区域覆盖率、服务人数和周边场所负载的影响。`
        : '请对当前区域最大容量避难场所执行停用压力测试。',
      focusShelterId: stressTarget?.shelter_id,
      stressIds: stressTarget ? [stressTarget.shelter_id] : [],
    },
    {
      id: 'budget-knapsack',
      mode: 'optimize',
      title: '重建资金最优分配',
      badge: 'MIP vs 贪心',
      description: '固定 2 单位灾后重建预算，用整数规划寻找全局最优扩容组合，量化比经验贪心多覆盖的人数。',
      layers: ['boundary', 'population_heatmap', 'coverage_15min', 'shelter_capacity'],
      prompt: optPrompt,
      optPreset: optA && optB && optC && optA.shelter_id !== optB.shelter_id
        ? [
          { shelterId: optA.shelter_id, capacity: '600', cost: '2' },
          { shelterId: optB.shelter_id, capacity: '450', cost: '1' },
          { shelterId: optC.shelter_id, capacity: '420', cost: '1' },
        ]
        : undefined,
    },
    {
      id: 'walk-sensitivity',
      mode: 'compare',
      title: '灾后路网受阻对比',
      badge: '韧性扫描',
      description: '对比 10 / 15 / 20 分钟步行时间阈值下的避难覆盖拐点，评估道路积水减速的影响。',
      layers: ['boundary', 'coverage_15min', 'access_gaps_15min', 'shelter_capacity'],
      prompt: '请对比步行阈值 10 分钟、15 分钟与 20 分钟下的避难服务覆盖率与未服务人口变化。',
    },
  ]
}

export function IntroModal({ region, shelters, sessionReady, onDismiss, onLaunchScenario }: Props) {
  const presets = buildRegionScenarios(region, shelters)
  const icons = [Activity, AlertTriangle, TrendingUp, GitCompare]
  const totalCap = shelters.reduce((sum, s) => sum + s.capacity, 0)

  return (
    <div className="intro-backdrop" role="dialog" aria-modal="true" aria-label="灾后避难网络修复与韧性重建决策引导">
      <div className="intro-card">
        <button type="button" className="intro-close" onClick={onDismiss} aria-label="进入自由探索模式">
          <X size={16} />
        </button>

        <div className="intro-eyebrow">
          <ShieldCheck size={14} />
          <span>SHELTERPLAN · 城市韧性与灾后避难重建决策工作台</span>
        </div>

        <h1>从灾后应急盲区到最优重建分配</h1>
        <p className="intro-lead">
          以 <strong>中山市 · {region.town}</strong> 为例：当强台风或极端内涝冲击城区，如何快速摸清现有{' '}
          <strong>{region.shelters} 处避难场所（总容量 {totalCap.toLocaleString('zh-CN')} 人）</strong>{' '}
          的真实服务短板、推演核心设施受损连锁反应，并用有限重建预算算出经过数学验证的全局最优扩容方案？
        </p>

        <div className="intro-grid">
          {presets.map((preset, idx) => {
            const Icon = icons[idx] ?? Activity
            return (
              <button
                key={preset.id}
                type="button"
                className="intro-scenario-btn"
                onClick={() => onLaunchScenario(preset)}
              >
                <div className="isb-top">
                  <span className={`isb-icon tone-${idx}`}>
                    <Icon size={16} />
                  </span>
                  <span className="isb-badge">{preset.badge}</span>
                </div>
                <strong>{preset.title}</strong>
                <p>{preset.description}</p>
                <span className="isb-cta">
                  {sessionReady ? '立即在地图上运行' : '进入此任务工作台'} <ArrowRight size={13} />
                </span>
              </button>
            )
          })}
        </div>

        <div className="intro-footer">
          <span className="intro-meta">
            <Compass size={13} /> 底层求解：HiGHS 线性/混合整数规划 · 全链路确定性 Verifier 核验
          </span>
          <button type="button" className="intro-skip-btn" onClick={onDismiss}>
            直接浏览地图工作台
          </button>
        </div>
      </div>
    </div>
  )
}
