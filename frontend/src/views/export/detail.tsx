import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/export'
const detailFields = [
  '模板编号',
  '模板名称',
  '数据源绑定',
  '导出格式',
  '字段选择',
  '筛选参数',
  '导出上限',
  '是否启用',
]

type Entry = Record<string, string | number | null>
type Snapshot = {
  snapshot_id: number
  filename: string
  file_format: string
  filters: { keyword: string; status: string; fields: string[]; limit: number }
  total: number
  row_count: number
  matched_codes: string[]
  omitted_codes: string[]
  created_at: string
  hit_count: number
}

export default function ExportDetailView() {
  const { entryId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const keyword = searchParams.get('keyword') ?? ''
  const status = searchParams.get('status') ?? ''

  const [entry, setEntry] = useState<Entry | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [errorMessage, setErrorMessage] = useState('')

  const listQuery = useMemo(
    () => new URLSearchParams(
      Object.entries({ keyword, status }).filter(([, value]) => value !== '')
    ).toString(),
    [keyword, status],
  )

  const backTo = useMemo(() => `/export${listQuery ? `?${listQuery}` : ''}`, [listQuery])

  const loadSnapshots = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/snapshots?${listQuery}`)
      if (!response.ok) {
        return
      }
      const payload = await response.json()
      // 最新生成的快照排在最前，与“最近快照”标题口径一致
      setSnapshots((payload.items ?? []).slice().reverse())
    } catch {
      // 快照清单拉取失败不阻断详情展示
    }
  }, [listQuery])

  const loadDetail = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${entryId}`)
      if (!response.ok) {
        let message = '导出模板详情读取失败'
        try {
          const payload = await response.json()
          message = payload.message ?? payload.detail ?? message
        } catch {
          // 保留默认错误提示
        }
        throw new Error(message)
      }
      setEntry(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '导出模板详情读取失败')
    }
  }, [entryId])

  useEffect(() => {
    void loadDetail()
  }, [loadDetail])

  useEffect(() => {
    void loadSnapshots()
  }, [loadSnapshots])

  async function downloadSnapshot(snapshot: Snapshot) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/snapshots/${snapshot.snapshot_id}/download`)
      if (!response.ok) {
        throw new Error('快照文件下载失败，请稍后重试')
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = snapshot.filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      // 下载完成后同步刷新最近快照，口径与列表筛选保持一致
      await loadSnapshots()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照文件下载失败')
    }
  }

  // 清单已按生成时间倒序排列，首条即最近快照
  const latestSnapshot = snapshots[0]

  return (
    <section className="page" data-module="export-detail">
      <header className="page-head">
        <div>
          <h2>导出模板详情</h2>
          <p className="page-desc">
            模板编号 {entryId} 的明细与导出快照；快照清单沿用列表筛选
            （{[keyword && `编号含「${keyword}」`, status && `状态=${status}`].filter(Boolean).join('，') || '无条件'}）。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to={backTo}>返回导出模板列表</Link>
        </div>
      </header>

      {errorMessage && <p className="error-text">{errorMessage}</p>}

      {entry && (
        <table className="data-table">
          <tbody>
            <tr>
              <th style={{ width: 160 }}>记录编号</th>
              <td>{entry.id}</td>
              <th style={{ width: 160 }}>状态</th>
              <td>{entry.status}</td>
            </tr>
            {detailFields.map((field) => (
              <tr key={field}>
                <th>{field}</th>
                <td colSpan={3}>{entry[field] ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>
          最近快照
          {latestSnapshot && (
            <span style={{ marginLeft: 8, color: 'var(--muted)', fontWeight: 400, fontSize: 12 }}>
              #{latestSnapshot.snapshot_id} · {latestSnapshot.created_at} ·
              页面总计 {latestSnapshot.total} 条 / 文件 {latestSnapshot.row_count} 行 ·
              缓存复用 {latestSnapshot.hit_count} 次
            </span>
          )}
        </h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>快照编号</th>
              <th>生成时间</th>
              <th>格式</th>
              <th>绑定筛选</th>
              <th>页面总计 / 文件行数</th>
              <th>命中模板编号</th>
              <th>被上限过滤的编号</th>
              <th>缓存复用次数</th>
              <th>下载</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((snapshot) => (
              <tr key={snapshot.snapshot_id}>
                <td>{snapshot.snapshot_id}</td>
                <td>{snapshot.created_at}</td>
                <td>{snapshot.file_format}</td>
                <td>
                  {[
                    snapshot.filters.keyword && `编号含「${snapshot.filters.keyword}」`,
                    snapshot.filters.status && `状态=${snapshot.filters.status}`,
                    `字段=${snapshot.filters.fields.join('、')}`,
                    `上限=${snapshot.filters.limit}`,
                  ].filter(Boolean).join('；')}
                </td>
                <td>{snapshot.total} / {snapshot.row_count}</td>
                <td>{snapshot.matched_codes.join('、') || '—'}</td>
                <td>{snapshot.omitted_codes.join('、') || '—'}</td>
                <td>{snapshot.hit_count}</td>
                <td>
                  <button className="link" type="button" onClick={() => void downloadSnapshot(snapshot)}>
                    下载快照
                  </button>
                </td>
              </tr>
            ))}
            {snapshots.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={9}>当前列表筛选条件下还没有导出快照</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </section>
  )
}
