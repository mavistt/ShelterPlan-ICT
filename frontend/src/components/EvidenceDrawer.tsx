import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Code2, FileCheck2, LoaderCircle, MapPin, ShieldCheck, X, XCircle } from 'lucide-react'
import { api } from '../api/client'
import type { AssignmentRow, RegionMetadata, ShelterRecord } from '../api/types'
import { useWorkspace } from '../state/workspace'

type Props = {
  region: RegionMetadata
  shelters: ShelterRecord[]
  sessionId: string | null
  resultId: string | null
  resultRows: AssignmentRow[]
  onClose: () => void
}

const SECTIONS = [
  { id: 'checks', label: 'Verifier 核验项' },
  { id: 'optimization_checks', label: '优化核验' },
  { id: 'trace', label: '12 步技能轨迹' },
  { id: 'shelter_loads', label: '场所负荷分配' },
  { id: 'compare_rows', label: '多方案对比明细' },
  { id: 'sensitivity_points', label: '敏感性扫描点' },
  { id: 'deficits', label: '未覆盖缺口样本' },
]

export function EvidenceDrawer({ region, shelters, sessionId, resultId, resultRows, onClose }: Props) {
  const [section, setSection] = useState('checks')
  const [offset, setOffset] = useState(0)
  const [rawJson, setRawJson] = useState(false)
  const flyTo = useWorkspace((s) => s.flyTo)

  const shelterMap = useMemo(() => new Map(shelters.map((s) => [s.shelter_id, s])), [shelters])

  useEffect(() => setOffset(0), [section, resultId])

  const evidenceQuery = useQuery({
    queryKey: ['evidence', sessionId, resultId, section, offset],
    queryFn: () => api.evidence(sessionId!, resultId!, section, offset, 20),
    enabled: Boolean(sessionId && resultId),
    retry: false,
  })

  const items = evidenceQuery.data?.items ?? []

  return (
    <div className="evidence-backdrop" role="dialog" aria-modal="true" aria-label="计算证据与 Verifier 审计">
      <div className="evidence-sheet">
        <div className="es-head">
          <div className="es-title">
            <ShieldCheck size={18} className="es-icon" />
            <div>
              <strong>确定性计算审计与 Verifier 证据</strong>
              <small>{region.town} · {resultId ? `结果 ${resultId}` : '等待运行产生结果'}</small>
            </div>
          </div>
          <div className="es-head-actions">
            <button type="button" className={`mini-toggle-btn ${rawJson ? 'active' : ''}`} onClick={() => setRawJson((v) => !v)}>
              <Code2 size={13} /> {rawJson ? '结构化视图' : '原始 JSON'}
            </button>
            <button type="button" className="icon-close-btn" onClick={onClose} aria-label="关闭审计抽屉">
              <X size={16} />
            </button>
          </div>
        </div>

        {!resultId ? (
          <div className="es-empty">
            <FileCheck2 size={28} />
            <strong>当前会话尚未产生求解结果</strong>
            <p>运行任意一次诊断、压测或预算优化后，这里会展示 HiGHS 求解状态、Verifier 核验清单与每处避难场所的分配负荷。</p>
          </div>
        ) : (
          <div className="es-body">
            {/* Shelter load quick summary */}
            {resultRows.length > 0 && (
              <div className="es-block">
                <div className="es-block-head">
                  <strong>避难场所负载排行（点击定位地图）</strong>
                  <span>共 {resultRows.length} 处</span>
                </div>
                <div className="es-load-list">
                  {[...resultRows]
                    .sort((a, b) => (Number(b.utilization ?? 0) - Number(a.utilization ?? 0)))
                    .slice(0, 8)
                    .map((row) => {
                      const s = shelterMap.get(row.shelter_id)
                      const util = typeof row.utilization === 'number' ? row.utilization * 100 : 0
                      return (
                        <button
                          key={row.shelter_id}
                          type="button"
                          className="es-load-row"
                          onClick={() => {
                            if (s) {
                              flyTo(s.lon, s.lat, s.shelter_id)
                              onClose()
                            }
                          }}
                        >
                          <MapPin size={12} />
                          <span className="es-load-name">{s?.name ?? row.shelter_id}</span>
                          <div className="es-load-bar">
                            <div
                              className={`es-load-fill ${util >= 90 ? 'high' : util >= 65 ? 'mid' : 'ok'}`}
                              style={{ width: `${Math.min(100, util)}%` }}
                            />
                          </div>
                          <b>{util.toFixed(0)}%</b>
                        </button>
                      )
                    })}
                </div>
              </div>
            )}

            {/* Section tabs */}
            <div className="es-tabs">
              {SECTIONS.map((sec) => (
                <button
                  key={sec.id}
                  type="button"
                  className={section === sec.id ? 'active' : ''}
                  onClick={() => setSection(sec.id)}
                >
                  {sec.label}
                </button>
              ))}
            </div>

            {evidenceQuery.isLoading ? (
              <div className="es-loading"><LoaderCircle size={16} className="spin" /> 正在读取 Verifier 证据…</div>
            ) : evidenceQuery.isError ? (
              <div className="es-error">当前结果无此分项证据或读取失败。</div>
            ) : rawJson ? (
              <pre className="es-json">{JSON.stringify(items, null, 2)}</pre>
            ) : (
              <div className="es-structured">
                {items.length === 0 ? (
                  <div className="es-empty-inline">该分项无记录</div>
                ) : (
                  items.map((item, idx) => {
                    const obj = (typeof item === 'object' && item !== null ? item : { value: item }) as Record<string, unknown>
                    const passed = obj.passed === true || obj.status === 'PASS' || obj.status === 'OK' || obj.status === 'SUCCEEDED'
                    const failed = obj.passed === false || obj.status === 'FAIL' || obj.status === 'FAILED'
                    const title = String(obj.name ?? obj.check_name ?? obj.skill ?? obj.step ?? obj.shelter_id ?? `条目 #${offset + idx + 1}`)
                    const detail = String(obj.message ?? obj.detail ?? obj.summary ?? obj.status ?? '')
                    return (
                      <div key={idx} className="es-item-row">
                        <span className={`es-item-dot ${passed ? 'pass' : failed ? 'fail' : 'neutral'}`}>
                          {passed ? <CheckCircle2 size={14} /> : failed ? <XCircle size={14} /> : <FileCheck2 size={13} />}
                        </span>
                        <div className="es-item-copy">
                          <strong>{title}</strong>
                          {detail && <small>{detail}</small>}
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
            )}

            {evidenceQuery.data && evidenceQuery.data.total > 20 && (
              <div className="es-pager">
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button>
                <span>{offset + 1} – {Math.min(offset + 20, evidenceQuery.data.total)} / {evidenceQuery.data.total}</span>
                <button type="button" disabled={!evidenceQuery.data.has_more} onClick={() => setOffset(offset + 20)}>下一页</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
