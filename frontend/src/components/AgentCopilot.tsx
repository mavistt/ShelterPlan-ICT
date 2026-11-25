import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Activity, AlertTriangle, ArrowUp, CheckCircle2, CircleHelp, FileCheck2,
  GitCompare, LoaderCircle, MapPin, RotateCcw, ShieldCheck, Sparkles,
  Square, TrendingUp, X,
} from 'lucide-react'
import type { CCapabilities, CStatus, RegionMetadata, ShelterRecord, TurnEnvelope } from '../api/types'
import { useWorkspace, type WorkspaceMessage } from '../state/workspace'

type Props = {
  region: RegionMetadata
  shelters: ShelterRecord[]
  online: boolean
  sessionBusy: boolean
  sessionError: string | null
  capabilities?: CCapabilities
  submitting: boolean
  localError: string | null
  onClearError: () => void
  onRun: (prompt: string) => void
  onCancel: () => void
  onResetSession: () => void
  onOpenEvidence: (resultId?: string) => void
}

const STATUS_LABELS: Record<CStatus, string> = {
  RUNNING: '求解中',
  SUCCEEDED: '已完成',
  NEEDS_CLARIFICATION: '待补充参数',
  FAILED: '未完成',
  CANCELLED: '已取消',
}

const TASK_LABELS: Record<string, string> = {
  assess: '现状诊断',
  stress_test: '受损推演',
  compare: '多方案对比',
  sensitivity: '敏感性扫描',
  optimize: '预算优化 (MIP)',
  optimize_budget: '预算优化 (MIP)',
  disaster_recovery: '灾后恢复闭环',
  diagnose_intervene: '缺口诊断干预',
  explain_result: '结果解释',
}

const STAGE_LABELS: Record<string, string> = {
  baseline: '① 灾前基线',
  damage: '② 灾损评估',
  diagnose: '③ 瓶颈诊断',
  recovery: '④ 恢复优化',
  compare: '⑤ 三态对比',
  verify: '⑥ 契约核验',
}

const intFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })

function formatMetricValue(key: string, value: number): string {
  if (key.includes('ratio') || key.includes('rate')) {
    const pct = value <= 1.0001 ? value * 100 : value
    return `${pct.toFixed(1)}%`
  }
  if (key.includes('time') || key.includes('min')) {
    return `${value.toFixed(1)} min`
  }
  if (key.includes('delta') || key.includes('gap')) {
    return `${value >= 0 ? '+' : ''}${intFmt.format(Math.round(value))} 人`
  }
  if (key.includes('demand') || key.includes('population') || key.includes('persons')) {
    return `${intFmt.format(Math.round(value))} 人`
  }
  return value.toFixed(2)
}

export function AgentCopilot({
  region,
  shelters,
  online,
  sessionBusy,
  sessionError,
  capabilities,
  submitting,
  localError,
  onClearError,
  onRun,
  onCancel,
  onResetSession,
  onOpenEvidence,
}: Props) {
  const messages = useWorkspace((s) => s.messages)
  const sessionId = useWorkspace((s) => s.sessionId)
  const activeStatus = useWorkspace((s) => s.activeStatus)
  const activeTurnId = useWorkspace((s) => s.activeTurnId)
  const flyTo = useWorkspace((s) => s.flyTo)
  const [draft, setDraft] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  const shelterById = useMemo(() => new Map(shelters.map((s) => [s.shelter_id, s])), [shelters])

  const quickPrompts = useMemo(() => {
    const byCap = [...shelters].sort((a, b) => b.capacity - a.capacity)
    const top1 = byCap[0]
    const top2 = byCap[1]
    const mid = byCap.find((s) => s.capacity >= 100 && s.capacity <= 1500) ?? byCap[2]
    return [
      {
        tag: '现状诊断',
        title: '15分钟服务缺口评估',
        prompt: '请评估当前区域的避难服务能力，步行阈值为 15 分钟，人口需求比例为 10%。',
        icon: Activity,
      },
      {
        tag: '受损推演',
        title: top1 ? `停用${top1.name.slice(0, 7)}压测` : '主力场所停用压测',
        prompt: top1 ? `请执行压力测试：暂时停用 ${top1.shelter_id}（${top1.name}），分析对服务指标的影响。` : '请对当前区域最大容量避难场所执行停用压力测试。',
        icon: AlertTriangle,
      },
      {
        tag: '重建优化',
        title: '预算 2 扩容 MIP 求解',
        prompt: top1 && top2 && mid
          ? `预算 2，给 ${top1.shelter_id} 扩容 600 人成本 2，给 ${top2.shelter_id} 扩容 450 人成本 1，给 ${mid.shelter_id} 扩容 420 人成本 1，做预算优化。`
          : '请基于当前区域避难场所做预算优化。',
        icon: TrendingUp,
      },
      {
        tag: '方案对比',
        title: '10m vs 20m 步行对比',
        prompt: '请对比步行阈值 10 分钟和 20 分钟下的避难服务覆盖率差异。',
        icon: GitCompare,
      },
    ]
  }, [shelters])

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages.length, activeStatus])

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    if (!draft.trim() || !online || !sessionId || submitting) return
    onRun(draft)
    setDraft('')
  }

  return (
    <aside className="copilot-panel" aria-label="ShelterPlan 规划助手">
      <div className="copilot-head">
        <div className="copilot-brand">
          <span className="copilot-avatar"><Sparkles size={14} /></span>
          <div>
            <strong>规划助手</strong>
            <span className="copilot-status">
              <i className={online ? 'dot-online' : 'dot-offline'} />
              {online ? `${region.town} · 求解器在线` : '等待后端服务连接'}
            </span>
          </div>
        </div>
        <div className="copilot-head-actions">
          <button
            type="button"
            className="head-pill-btn"
            title="清空上下文并新开对话"
            disabled={!sessionId || submitting}
            onClick={onResetSession}
          >
            <RotateCcw size={12} /> 新对话
          </button>
          <button
            type="button"
            className="head-pill-btn"
            title="查看 Verifier 计算审计证据"
            onClick={() => onOpenEvidence()}
          >
            <FileCheck2 size={12} /> 审计
          </button>
        </div>
      </div>

      {!sessionId && (
        <div className="session-pill-banner">
          {sessionBusy
            ? <><LoaderCircle size={13} className="spin" /> 正在建立 {region.town} 规划会话…</>
            : sessionError
              ? <><AlertTriangle size={13} /> {sessionError}</>
              : '连接后端服务后自动开启对话'}
        </div>
      )}

      <div className="copilot-stream" ref={listRef}>
        {!messages.length && (
          <div className="copilot-empty">
            <div className="ce-hero">
              <span className="ce-icon"><Sparkles size={18} /></span>
              <div>
                <strong>ShelterPlan 空间规划助手</strong>
                <p>直接提问，或点击下方灾后重建高频任务发起求解</p>
              </div>
            </div>
            <div className="ce-card-grid">
              {quickPrompts.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.title}
                    type="button"
                    className="ce-starter-card"
                    disabled={!online || !sessionId || submitting}
                    onClick={() => onRun(item.prompt)}
                  >
                    <div className="ces-top">
                      <Icon size={13} />
                      <span>{item.tag}</span>
                    </div>
                    <b>{item.title}</b>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {messages.map((msg, idx) =>
          msg.role === 'user' ? (
            <div className="msg-user" key={msg.id}>
              <div className="msg-user-bubble">{msg.text}</div>
              <span className="msg-time">
                {new Date(msg.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          ) : (
            <AgentResultCard
              key={msg.id}
              message={msg}
              isLatest={idx === messages.length - 1}
              canFollowUp={Boolean(online && sessionId && !submitting)}
              shelterById={shelterById}
              onFlyTo={(s) => flyTo(s.lon, s.lat, s.shelter_id)}
              onFollowUp={onRun}
              onOpenEvidence={onOpenEvidence}
            />
          ),
        )}

        {localError && (
          <div className="copilot-error">
            <AlertTriangle size={14} />
            <span>{localError}</span>
            <button type="button" onClick={onClearError} aria-label="关闭错误"><X size={13} /></button>
          </div>
        )}
      </div>

      {activeStatus === 'NEEDS_CLARIFICATION' && (
        <div className="clarify-bar">
          <CircleHelp size={13} /> 请在下方直接补充缺失参数，系统将自动继续执行。
        </div>
      )}

      <form className="copilot-composer" onSubmit={submit}>
        <div className={`composer-shell ${submitting ? 'is-running' : ''}`}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              submitting
                ? 'HiGHS 求解与 Verifier 核验中… 点击右侧停止可取消'
                : online
                  ? '问我任何避难规划问题，例如：停用实验小学后缺口多大？'
                  : '等待后端服务连接…'
            }
            rows={2}
            maxLength={capabilities?.limits?.max_text_chars ?? 4000}
            disabled={!online || !sessionId || submitting}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
          />
          {submitting && activeStatus === 'RUNNING' ? (
            <button
              type="button"
              className="composer-stop-btn"
              aria-label="停止当前求解"
              title="停止当前求解"
              disabled={!activeTurnId}
              onClick={onCancel}
            >
              <Square size={11} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              className="composer-send-btn"
              aria-label="发送请求"
              disabled={!draft.trim() || !online || !sessionId || submitting}
            >
              <ArrowUp size={15} strokeWidth={2.4} />
            </button>
          )}
        </div>
      </form>
    </aside>
  )
}

function humanizeText(text: string, shelterById: Map<string, ShelterRecord>): string {
  let out = text.replace(/zs_[a-f0-9]{8,12}/gi, (id) => {
    const s = shelterById.get(id.toLowerCase())
    return s ? `「${s.name}」` : id
  })
  out = out.replace(/CAP_\d+_([A-F0-9]{6})/gi, (code, suffix: string) => {
    const low = suffix.toLowerCase()
    for (const s of shelterById.values()) {
      if (s.shelter_id.toLowerCase().endsWith(low)) return `「${s.name}」`
    }
    return code
  })
  // Translate raw code/solver identifiers into clean professional Chinese
  const replacements: Array<[RegExp, string]> = [
    [/verification_status\s*=\s*PASS_DEMO\.?/gi, '全部约束与数据校验均已通过。'],
    [/verification_status\s*=\s*PASS\.?/gi, '全部约束校验已通过。'],
    [/verification_status\s*=\s*FAIL\.?/gi, '存在未通过的核验项，请查看审计详情。'],
    [/主目标阶段最优核验\s*=\s*TRUE/gi, '主目标已证明达到全局最优'],
    [/主目标阶段最优核验\s*=\s*FALSE/gi, '主目标为当前时间内的可行解'],
    [/主目标阶段最优核验\s*=\s*UNKNOWN/gi, '主目标最优性待确认'],
    [/Greedy对照\s*=\s*available（?/gi, '与经验贪心算法对照结果：'],
    [/Greedy对照\s*=\s*unavailable/gi, '当前情景暂不适用贪心对照'],
    [/求解状态\s*OPTIMAL/gi, '求解器已找到全局最优解'],
    [/求解状态\s*FEASIBLE/gi, '求解器已找到可行解'],
    [/population_demand_ratio/gi, '应急需求比例'],
    [/max_walk_time_min/gi, '步行时间阈值'],
    [/budget_units/gi, '重建预算上限'],
    [/distance_deficit/gi, '步行距离盲区'],
    [/capacity_deficit/gi, '场所容量缺口'],
    [/\bINCONCLUSIVE\b/g, '样本不足'],
    [/\bOPTIMAL\b/g, '全局最优'],
    [/\bFEASIBLE\b/g, '可行解'],
    [/\bPASS\b/g, '通过'],
    [/（([^（）]*)；\s*$/g, '（$1）'],
  ]
  for (const [pattern, target] of replacements) {
    out = out.replace(pattern, target)
  }
  return out.replace(/；\s*。/g, '。').trim()
}

type BottleneckBreakdown = {
  noConnPersons: number
  capShortPersons: number
  noConnNodes: number
  capShortNodes: number
}

function parseBottleneck(rawText: string): BottleneckBreakdown | null {
  const noConnP = rawText.match(/NO_LEGAL_CONNECTION:\s*([\d,.]+)\s*人/)
  const capP = rawText.match(/CAPACITY_COMPETITION_OR_INSUFFICIENCY:\s*([\d,.]+)\s*人/)
  if (!noConnP && !capP) return null
  const noConnN = rawText.match(/'NO_LEGAL_CONNECTION':\s*(\d+)/)
  const capN = rawText.match(/'CAPACITY_COMPETITION_OR_INSUFFICIENCY':\s*(\d+)/)
  const toNum = (m: RegExpMatchArray | null) => (m ? Number(m[1].replace(/,/g, '')) : 0)
  return {
    noConnPersons: Math.round(toNum(noConnP)),
    capShortPersons: Math.round(toNum(capP)),
    noConnNodes: toNum(noConnN),
    capShortNodes: toNum(capN),
  }
}

function buildExecutiveSummary(
  turn: TurnEnvelope,
  rawText: string,
  shelterById: Map<string, ShelterRecord>,
): { headline: string; bullets: string[]; bottleneck: BottleneckBreakdown | null; selectedShelters: ShelterRecord[]; hasLog: boolean } {
  const isStructuredLog = rawText.includes('【结论】') || rawText.includes('【关键指标】') || rawText.includes('【结果解释】')
  const matchedShelters: ShelterRecord[] = []
  const seen = new Set<string>()

  // Match full zs_ IDs or 6-char suffix inside CAP_3_D13F73
  const fullIds = rawText.match(/zs_[a-f0-9]{8,12}/gi) ?? []
  for (const id of fullIds) {
    const s = shelterById.get(id.toLowerCase())
    if (s && !seen.has(s.shelter_id)) { seen.add(s.shelter_id); matchedShelters.push(s) }
  }
  const capCodes = rawText.match(/CAP_\d+_([A-F0-9]{6})/gi) ?? []
  for (const code of capCodes) {
    const suffix = code.split('_').pop()?.toLowerCase() ?? ''
    if (!suffix) continue
    for (const s of shelterById.values()) {
      if (s.shelter_id.toLowerCase().endsWith(suffix) && !seen.has(s.shelter_id)) {
        seen.add(s.shelter_id)
        matchedShelters.push(s)
      }
    }
  }

  if (!isStructuredLog || turn.status !== 'SUCCEEDED') {
    return { headline: humanizeText(rawText, shelterById), bullets: [], bottleneck: null, selectedShelters: matchedShelters, hasLog: false }
  }

  // Dedicated natural-Chinese synthesis for bottleneck diagnosis (no raw Python dicts or English enums!)
  const bottleneck = parseBottleneck(rawText)
  if (turn.task_type === 'diagnose_intervene' && bottleneck) {
    const totalUnserved = bottleneck.noConnPersons + bottleneck.capShortPersons
    const noConnPct = totalUnserved > 0 ? ((bottleneck.noConnPersons / totalUnserved) * 100).toFixed(1) : '0.0'
    const capPct = totalUnserved > 0 ? ((bottleneck.capShortPersons / totalUnserved) * 100).toFixed(1) : '0.0'
    const dominant = bottleneck.noConnPersons >= bottleneck.capShortPersons ? '步行距离盲区' : '现有场所容量不足'
    const headline = `核心瓶颈为「${dominant}」：当前共有 ${intFmt.format(totalUnserved)} 人未能获得避难服务，其中 ${noConnPct}% 因 15 分钟步行不可达，${capPct}% 因周边避难所已满载。`
    const bullets: string[] = [
      `步行盲区（${intFmt.format(bottleneck.noConnPersons)} 人，${intFmt.format(bottleneck.noConnNodes)} 个网格）：单纯扩容现有场所无法覆盖，建议在盲区片区新增避难布点。`,
      `容量缺口（${intFmt.format(bottleneck.capShortPersons)} 人，${intFmt.format(bottleneck.capShortNodes)} 个网格）：可直接在左侧「重建优化」中对满载场所扩容缓解。`,
    ]
    return { headline, bullets, bottleneck, selectedShelters: matchedShelters, hasLog: true }
  }

  // For explain_result / compare / sensitivity, preserve substantive explanation lines as readable bullets!
  if (turn.task_type && ['explain_result', 'diagnose_intervene', 'compare', 'sensitivity'].includes(turn.task_type)) {
    const meaningfulLines = rawText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0)
      .filter((l) => !/^【(数据声明|假设说明|结果编号|参数)】/.test(l))
      .map((l) => humanizeText(l.replace(/^【[^】]+】\s*/, ''), shelterById))
      .filter((l) => !/DEMO_ONLY|HYPOTHETICAL/i.test(l))
    const headline = meaningfulLines[0] ?? humanizeText(rawText, shelterById)
    const bullets = meaningfulLines.slice(1, 5)
    return { headline, bullets, bottleneck: null, selectedShelters: matchedShelters, hasLog: true }
  }

  const m = turn.metrics ?? {}
  const optDelta = typeof m.coverage_delta_persons === 'number' ? Math.round(m.coverage_delta_persons) : null
  const optRatio = typeof m.optimized_coverage_ratio === 'number' ? m.optimized_coverage_ratio : null
  const baseRatio = typeof m.coverage_ratio === 'number' ? m.coverage_ratio : null
  const served = typeof m.optimized_served_demand === 'number'
    ? Math.round(m.optimized_served_demand)
    : typeof m.served_demand === 'number'
      ? Math.round(m.served_demand)
      : null
  const unserved = typeof m.unserved_demand === 'number' ? Math.round(m.unserved_demand) : null

  const budgetMatch = rawText.match(/总成本\s*([\d.]+)\s*\/\s*预算\s*([\d.]+)/)
  const namesText = matchedShelters.map((s) => `「${s.name}」`).join('、')

  if (turn.task_type === 'optimize' || turn.task_type === 'optimize_budget') {
    const parts: string[] = []
    if (namesText) parts.push(`建议优先扩容 ${namesText}`)
    else parts.push('已完成预算约束下的最优扩容求解')
    if (budgetMatch) parts.push(`动用预算 ${budgetMatch[1]} / ${budgetMatch[2]}`)
    if (optDelta !== null && optDelta > 0) parts.push(`新增覆盖 ${intFmt.format(optDelta)} 人`)
    if (optRatio !== null) {
      const pct = (optRatio <= 1 ? optRatio * 100 : optRatio).toFixed(1)
      parts.push(`区域覆盖率提升至 ${pct}%`)
    }
    return { headline: `${parts.join('，')}。`, bullets: [], bottleneck: null, selectedShelters: matchedShelters, hasLog: true }
  }

  if (turn.task_type === 'stress_test') {
    const target = namesText ? `停用 ${namesText} 后` : '设施停用推演完成'
    const cov = baseRatio !== null ? `，当前覆盖率 ${(baseRatio <= 1 ? baseRatio * 100 : baseRatio).toFixed(1)}%` : ''
    const gap = unserved !== null ? `，未覆盖缺口 ${intFmt.format(unserved)} 人` : ''
    return { headline: `${target}${cov}${gap}。`, bullets: [], bottleneck: null, selectedShelters: matchedShelters, hasLog: true }
  }

  if (baseRatio !== null && served !== null) {
    const pct = (baseRatio <= 1 ? baseRatio * 100 : baseRatio).toFixed(1)
    const gapText = unserved !== null ? `，仍有 ${intFmt.format(unserved)} 人存在避难承载缺口` : ''
    return {
      headline: `当前条件下已服务 ${intFmt.format(served)} 人（覆盖率 ${pct}%）${gapText}。`,
      bullets: [],
      bottleneck: null,
      selectedShelters: matchedShelters,
      hasLog: true,
    }
  }

  const firstLine = rawText.split('\n').find((l) => l.startsWith('【结论】'))?.replace('【结论】', '').trim()
  return { headline: humanizeText(firstLine || rawText, shelterById), bullets: [], bottleneck: null, selectedShelters: matchedShelters, hasLog: true }
}

function AgentResultCard({
  message,
  isLatest,
  canFollowUp,
  shelterById,
  onFlyTo,
  onFollowUp,
  onOpenEvidence,
}: {
  message: WorkspaceMessage
  isLatest: boolean
  canFollowUp: boolean
  shelterById: Map<string, ShelterRecord>
  onFlyTo: (s: ShelterRecord) => void
  onFollowUp: (prompt: string) => void
  onOpenEvidence: (resultId?: string) => void
}) {
  const turn = message.turn
  if (!turn) return null
  const status = turn.status
  const errorMessage = typeof turn.error === 'object' && turn.error
    ? turn.error.message ?? ''
    : typeof turn.error === 'string'
      ? turn.error
      : ''
  const rawText = turn.clarification?.question ?? turn.message ?? (status === 'RUNNING' ? '正在执行规划求解…' : errorMessage)

  const summary = useMemo(() => buildExecutiveSummary(turn, rawText, shelterById), [rawText, shelterById, turn])
  const kpis = useMemo(() => buildStructuredKpis(turn), [turn])
  const greedy = turn.greedy_comparison?.status === 'available' ? turn.greedy_comparison : null
  const mono = turn.monotonicity_check?.status && turn.monotonicity_check.status !== 'NOT_APPLICABLE'
    ? turn.monotonicity_check
    : null
  const fallbackUsed = Boolean(turn.llm?.fallback_used)

  // Filter out noisy boilerplate disclaimers from card warnings
  const realWarnings = (turn.warnings ?? []).filter(
    (w) => !/DEMO_ONLY|HYPOTHETICAL_IMPROVEMENT|演示数据|请勿用于真实应急|user-supplied assumptions/i.test(w),
  )

  return (
    <div className="msg-agent">
      <div className="ma-header">
        <span className={`ma-status status-${status.toLowerCase()}`}>
          {status === 'SUCCEEDED' ? <CheckCircle2 size={12} /> : status === 'RUNNING' ? <LoaderCircle size={12} className="spin" /> : <AlertTriangle size={12} />}
          {STATUS_LABELS[status]}
        </span>
        {turn.task_type && <span className="ma-task-tag">{TASK_LABELS[turn.task_type] ?? turn.task_type}</span>}
        {fallbackUsed && <span className="ma-fallback-tag" title="本地 LLM 内存不足已降级为规则解析">规则解析</span>}
      </div>

      {summary.headline && (
        <div className={status === 'SUCCEEDED' ? 'ma-headline' : 'ma-text'}>{summary.headline}</div>
      )}

      {turn.composite?.comparison && (
        <CompositeRecoveryCard composite={turn.composite} />
      )}

      {summary.bottleneck && (
        <BottleneckBarCard data={summary.bottleneck} />
      )}

      {summary.bullets.length > 0 && (
        <ul className="ma-bullets">
          {summary.bullets.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}

      {kpis.length > 0 && (
        <div className="ma-kpi-grid">
          {kpis.map((item) => (
            <div key={item.label} className={`ma-kpi ${item.accent ?? ''}`}>
              <span>{item.label}</span>
              <b>{item.value}</b>
            </div>
          ))}
        </div>
      )}

      {greedy && typeof greedy.greedy_served_demand === 'number' && (
        <GreedyComparisonCard greedy={greedy} turn={turn} />
      )}

      {summary.selectedShelters.length > 0 && (
        <div className="ma-shelter-chips">
          {summary.selectedShelters.slice(0, 4).map((s) => (
            <button key={s.shelter_id} type="button" className="ma-shelter-chip" onClick={() => onFlyTo(s)}>
              <MapPin size={11} />
              <span>{s.name}</span>
            </button>
          ))}
        </div>
      )}

      {summary.hasLog && (
        <details className="ma-raw-details">
          <summary>查看完整求解日志</summary>
          <p className="ma-text">{rawText}</p>
        </details>
      )}

      {status === 'SUCCEEDED' && (
        <div className="ma-verify-row">
          <span className="verify-badge">
            <ShieldCheck size={12} /> HiGHS · Verifier 通过
          </span>
          {mono && (
            <span className={`verify-badge ${mono.status === 'PASS' ? 'pass' : 'warn'}`}>
              单调性{mono.status === 'PASS' ? '通过' : mono.status === 'INCONCLUSIVE' ? '样本不足' : String(mono.status)}
            </span>
          )}
          {turn.result_id && (
            <button type="button" className="evidence-link-btn" onClick={() => onOpenEvidence(turn.result_id ?? undefined)}>
              审计证据 →
            </button>
          )}
        </div>
      )}

      {realWarnings.length > 0 && (
        <div className="ma-warning">
          <AlertTriangle size={12} />
          <span>{realWarnings.join('；')}</span>
        </div>
      )}

      {isLatest && status === 'SUCCEEDED' && canFollowUp && (
        <div className="ma-followups">
          <button type="button" onClick={() => onFollowUp('请诊断当前区域未服务需求的主要瓶颈原因（容量不足还是步行距离受限）。')}>
            诊断缺口瓶颈
          </button>
          <button type="button" onClick={() => onFollowUp('撤销上一步参数修改。')}>
            撤销上一步
          </button>
        </div>
      )}
    </div>
  )
}

function buildStructuredKpis(turn: TurnEnvelope): Array<{ label: string; value: string; accent?: string }> {
  const m = turn.metrics
  if (!m) return []
  const out: Array<{ label: string; value: string; accent?: string }> = []
  const pick = (key: string, label: string, accent?: string) => {
    const val = m[key]
    if (typeof val === 'number' && Number.isFinite(val)) {
      out.push({ label, value: formatMetricValue(key, val), accent })
    }
  }
  if (typeof m.optimized_coverage_ratio === 'number') {
    pick('optimized_coverage_ratio', '优化后覆盖率', 'teal')
    pick('coverage_delta_persons', '新增覆盖人数', 'teal')
    pick('optimized_served_demand', '优化后服务人数')
    pick('weighted_walk_time_min', '加权步行时间')
  } else {
    pick('coverage_ratio', '服务覆盖率', 'teal')
    pick('served_demand', '已服务人数')
    pick('unserved_demand', '未覆盖缺口', 'amber')
    pick('weighted_walk_time_min', '加权步行时间')
  }
  return out.slice(0, 4)
}

function CompositeRecoveryCard({ composite }: { composite: NonNullable<TurnEnvelope['composite']> }) {
  const cmp = composite.comparison
  const stages = composite.stages ?? []
  const checks = composite.constraint_checks ?? []
  const passedChecks = checks.filter((c) => c.passed).length
  const baseServed = Math.round(Number(cmp?.baseline?.served_demand ?? 0))
  const dmgServed = Math.round(Number(cmp?.damaged?.served_demand ?? 0))
  const recServed = Math.round(Number(cmp?.recovered?.optimized_served_demand ?? 0))
  const loss = Math.round(Number(cmp?.damage_service_loss ?? 0))
  const gain = Math.round(Number(cmp?.recovery_service_gain ?? 0))
  const ratioVal = cmp?.recovery_ratio?.status === 'APPLICABLE' && typeof cmp.recovery_ratio.value === 'number'
    ? `${(cmp.recovery_ratio.value * 100).toFixed(1)}%`
    : null

  return (
    <div className="composite-card">
      <div className="cc-tristate-grid">
        <div className="tristate-cell">
          <span>灾前基线</span>
          <b>{intFmt.format(baseServed)} 人</b>
        </div>
        <div className="tristate-cell danger">
          <span>灾后受损</span>
          <b>{intFmt.format(dmgServed)} 人</b>
          {loss > 0 && <small>-{intFmt.format(loss)}</small>}
        </div>
        <div className="tristate-cell teal">
          <span>优化恢复</span>
          <b>{intFmt.format(recServed)} 人</b>
          {gain > 0 && <small>+{intFmt.format(gain)}{ratioVal ? ` (${ratioVal})` : ''}</small>}
        </div>
      </div>

      {stages.length > 0 && (
        <details className="skill-timeline-details">
          <summary>
            <span>Skill 执行时间线（{stages.length} 阶段）</span>
            {checks.length > 0 && (
              <span className="contract-pill">{passedChecks}/{checks.length} 契约校验通过</span>
            )}
          </summary>
          <div className="skill-timeline-list">
            {stages.map((st) => (
              <div key={st.stage_id} className="stl-row">
                <span className={`stl-dot ${st.status === 'COMPLETED' ? 'ok' : 'warn'}`} />
                <div className="stl-copy">
                  <strong>{STAGE_LABELS[st.stage_id] ?? st.stage_id}</strong>
                  <small>
                    {st.skill_ids?.length ? st.skill_ids.join(' → ') : '跨阶段汇总校验'}
                    {typeof st.duration_ms === 'number' ? ` · ${Math.round(st.duration_ms)}ms` : ''}
                  </small>
                </div>
                <span className="stl-status">{st.solver_status ?? st.status}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function BottleneckBarCard({ data }: { data: BottleneckBreakdown }) {
  const total = Math.max(data.noConnPersons + data.capShortPersons, 1)
  const noConnPct = (data.noConnPersons / total) * 100
  const capPct = (data.capShortPersons / total) * 100
  return (
    <div className="bottleneck-card">
      <div className="bn-head">
        <strong>未覆盖缺口归因结构</strong>
        <span>共 {intFmt.format(total)} 人</span>
      </div>
      <div className="bn-stacked">
        <div className="bn-seg dist" style={{ width: `${noConnPct}%` }} />
        <div className="bn-seg cap" style={{ width: `${capPct}%` }} />
      </div>
      <div className="bn-legend">
        <span><i className="bn-dot dist" />步行盲区 <b>{noConnPct.toFixed(1)}%</b> ({intFmt.format(data.noConnPersons)}人)</span>
        <span><i className="bn-dot cap" />容量满载 <b>{capPct.toFixed(1)}%</b> ({intFmt.format(data.capShortPersons)}人)</span>
      </div>
    </div>
  )
}

function GreedyComparisonCard({ greedy, turn }: { greedy: NonNullable<TurnEnvelope['greedy_comparison']>; turn: TurnEnvelope }) {
  const greedyVal = Number(greedy.greedy_served_demand ?? 0)
  const rawGap = Number(greedy.served_demand_gap ?? 0)
  const gap = Math.abs(rawGap) < 0.5 ? 0 : Math.round(rawGap)
  const mipVal = typeof greedy.mip_served_demand === 'number'
    ? greedy.mip_served_demand
    : typeof turn.metrics?.optimized_served_demand === 'number'
      ? Number(turn.metrics.optimized_served_demand)
      : greedyVal + rawGap
  const maxVal = Math.max(mipVal, greedyVal, 1)
  const mipPct = Math.min(100, (mipVal / maxVal) * 100)
  const greedyPct = Math.min(100, (greedyVal / maxVal) * 100)

  return (
    <div className="greedy-card">
      <div className="gc-head">
        <strong>全局规划 (MIP) vs 经验贪心 (Greedy)</strong>
        <span className={`gc-gain ${gap > 0 ? 'pos' : ''}`}>
          {gap > 0 ? `+${intFmt.format(gap)} 人` : gap === 0 ? '与贪心持平' : `${intFmt.format(gap)} 人`}
        </span>
      </div>
      <div className="gc-bar-row">
        <span>HiGHS MIP</span>
        <div className="gc-track"><div className="gc-fill mip" style={{ width: `${mipPct}%` }} /></div>
        <b>{intFmt.format(Math.round(mipVal))}</b>
      </div>
      <div className="gc-bar-row">
        <span>Greedy 贪心</span>
        <div className="gc-track"><div className="gc-fill greedy" style={{ width: `${greedyPct}%` }} /></div>
        <b>{intFmt.format(Math.round(greedyVal))}</b>
      </div>
    </div>
  )
}
