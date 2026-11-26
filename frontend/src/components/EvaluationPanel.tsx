import { useQuery } from '@tanstack/react-query'
import { Activity, AlertCircle, Clock3, X } from 'lucide-react'
import { api } from '../api/client'
import type { EvaluationArmSummary, EvaluationSummary } from '../api/types'

function statusLabel(status?: string) {
  if (status === 'PASS') return '通过'
  if (status === 'PARTIAL') return '部分完成'
  if (status === 'NOT_RUN') return '未运行'
  if (status === 'READY') return '已实测'
  return status || '待生成'
}

function ArmCard({ arm }: { arm: EvaluationArmSummary }) {
  const done = arm.status === 'PASS' || arm.status === 'READY'
  return (
    <article className={`eval-arm-card ${done ? 'complete' : 'pending'}`}>
      <div className="eval-arm-top">
        <span className="eval-arm-id">{arm.id}</span>
        <span className={`eval-badge ${done ? 'good' : 'muted'}`}>{statusLabel(arm.status)}</span>
      </div>
      <strong>{arm.name}</strong>
      {arm.result && <span className="eval-arm-result">{arm.result}</span>}
      {arm.note && <p>{arm.note}</p>}
    </article>
  )
}

function AdmissionRow({ label, demo_eligible, needs_review, excluded, enabled }: {
  label: string; demo_eligible?: number; needs_review?: number; excluded?: number; enabled?: boolean
}) {
  return (
    <div className="eval-admission-row">
      <span className="eval-region-state"><i className={enabled ? 'live' : 'disabled'} />{label}</span>
      <span>{demo_eligible ?? 0} DEMO</span>
      <span>{needs_review ? `${needs_review} 待审核` : excluded ? `${excluded} 排除` : '—'}</span>
    </div>
  )
}

function EvaluationContent({ data }: { data: EvaluationSummary }) {
  const b = data.b_structured
  return (
    <>
      <div className="eval-hero">
        <div className="eval-hero-icon"><Activity size={17} /></div>
        <div>
          <strong>{data.frozen_task_count} 项冻结任务</strong>
          <span>{data.report_version} · 数据级别 {data.data_status}</span>
        </div>
      </div>

      <section className="eval-section">
        <div className="eval-section-title"><span>B Solver 结构化实测</span><span className="eval-badge good">{statusLabel(b.status)}</span></div>
        <div className="eval-kpi-grid">
          <div><strong>{b.pass_count ?? '—'}<small>/{b.result_count ?? '—'}</small></strong><span>任务通过</span></div>
          <div><strong>{b.success_rate == null ? '—' : `${(b.success_rate * 100).toFixed(1)}%`}</strong><span>冻结集成功率</span></div>
          <div><strong>{b.trace_count ?? '—'}</strong><span>可追溯记录</span></div>
        </div>
        {b.note && <p className="eval-note">{b.note}</p>}
        {b.oracle_status && <p className="eval-note">数学证据：{b.oracle_status}</p>}
      </section>

      <section className="eval-section">
        <div className="eval-section-title"><span>公平对照实验组</span><span className="eval-badge muted">真实 Agent 未计分</span></div>
        <div className="eval-arm-list">{data.arms.map((arm) => <ArmCard key={arm.id} arm={arm} />)}</div>
        <div className="eval-callout"><Clock3 size={14} /><span>{data.agent_evaluation.note || '没有可用的真实 LLM 配置；各 Agent 组按 NOT_RUN 保留。'}</span></div>
      </section>

      <section className="eval-section">
        <div className="eval-section-title"><span>A v2 数据准入</span><span className="eval-badge muted">VERIFIED 0</span></div>
        <div className="eval-admission-head"><span>区域</span><span>可演示规划</span><span>限制</span></div>
        <div className="eval-admission-table">
          <AdmissionRow label="石岐街道" {...data.admission.shiqi} />
          <AdmissionRow label="东区街道" {...data.admission.east_district} />
          <AdmissionRow label="小榄镇" {...data.admission.xiaolan} />
        </div>
        <p className="eval-note">小榄镇已禁用规划：30 条待审核、1 条排除。DEMO 数据只用于演示与算法评估。</p>
      </section>
    </>
  )
}

export function EvaluationPanel({ onClose }: { onClose: () => void }) {
  const query = useQuery({ queryKey: ['evaluation-summary'], queryFn: api.evaluationSummary, retry: false, staleTime: 60_000 })
  return (
    <aside className="evaluation-panel" role="dialog" aria-label="ShelterPlan 集成评测概览">
      <header className="eval-panel-header">
        <div><span className="eval-overline">SHELTERPLAN EVAL</span><h2>评测与集成状态</h2></div>
        <button type="button" className="glass-icon-btn" onClick={onClose} aria-label="关闭评测面板"><X size={15} /></button>
      </header>
      <div className="eval-panel-body">
        {query.isLoading && <div className="eval-loading"><span className="spin" />正在读取冻结评测摘要…</div>}
        {query.isError && (
          <div className="eval-empty"><AlertCircle size={18} /><strong>评测摘要暂不可用</strong><span>请确认 C 服务已启动，且集成报告已生成。</span></div>
        )}
        {query.data && <EvaluationContent data={query.data} />}
        {query.data?.generated_at && <footer className="eval-updated">报告生成于 {new Date(query.data.generated_at).toLocaleString('zh-CN')}</footer>}
      </div>
    </aside>
  )
}
