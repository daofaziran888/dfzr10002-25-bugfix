import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/export'
const columns = [
  "模板编号",
  "模板名称",
  "数据源绑定",
  "导出格式",
  "字段选择",
  "筛选参数",
  "导出上限",
  "是否启用"
]
const actions = [
  "提交校验",
  "启用配置",
  "停用配置"
]
const statuses = [
  "草稿",
  "待校验",
  "已启用",
  "已停用"
]
const exportFormats = ["XLSX", "CSV"]

type Row = Record<string, string | number | null>
type Filters = {
  keyword: string
  status: string
  format: string
  fields: string[]
  limit: string
}
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

const EMPTY_FILTERS: Filters = {
  keyword: '',
  status: '',
  format: 'XLSX',
  fields: ['模板编号', '模板名称'],
  limit: '10000',
}

export default function ExportView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [downloading, setDownloading] = useState(false)

  const listQuery = useMemo(
    () => new URLSearchParams(
      Object.entries({ keyword: filters.keyword, status: filters.status })
        .filter(([, value]) => value !== '')
    ).toString(),
    [filters.keyword, filters.status],
  )

  const detailSearch = useMemo(() => {
    const params = new URLSearchParams()
    if (filters.keyword) params.set('keyword', filters.keyword)
    if (filters.status) params.set('status', filters.status)
    const value = params.toString()
    return value ? `?${value}` : ''
  }, [filters.keyword, filters.status])

  const stats = useMemo(() => [
    { label: '导出模板总数', value: total },
    { label: '待校验导出模板', value: rows.filter((row) => row.status === '待校验').length },
    { label: '已启用导出模板', value: rows.filter((row) => row.status === '已启用').length },
  ], [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}?${listQuery}`)
      if (!response.ok) {
        throw new Error('导出模板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      // 下载动作或列表未命中时，不能把列表清空成“未命中”，保留已有数据并提示
      setErrorMessage(error instanceof Error ? error.message : '数据导出配置列表读取失败')
    }

    try {
      const snapshotResponse = await request(`${ENDPOINT}/snapshots?${listQuery}`)
      if (snapshotResponse.ok) {
        const payload = await snapshotResponse.json()
        // 任务清单按生成时间倒序展示，最新快照排在最前
        setSnapshots((payload.items ?? []).slice().reverse())
      }
    } catch {
      // 任务清单拉取失败不阻断列表展示
    }
  }, [listQuery])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('数据导出配置动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据导出配置操作失败')
    }
  }

  function toggleField(field: string) {
    setFilters((prev) => ({
      ...prev,
      fields: prev.fields.includes(field)
        ? prev.fields.filter((item) => item !== field)
        : [...prev.fields, field],
    }))
  }

  async function downloadList() {
    setErrorMessage('')
    setDownloading(true)
    try {
      const params = new URLSearchParams()
      if (filters.keyword) params.set('keyword', filters.keyword)
      if (filters.status) params.set('status', filters.status)
      params.set('format', filters.format)
      filters.fields.forEach((field) => params.append('fields', field))
      if (filters.limit) params.set('limit', filters.limit)

      const response = await request(`${ENDPOINT}/export?${params.toString()}`)
      if (!response.ok) {
        let message = '导出清单下载失败'
        try {
          const payload = await response.json()
          message = payload.message ?? payload.detail ?? message
        } catch {
          // 非 JSON 错误响应时保留默认提示
        }
        throw new Error(message)
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `导出模板清单.${filters.format.toLowerCase()}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      // 下载完成后刷新任务清单，让最近快照同步显示
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '导出清单下载失败')
    } finally {
      setDownloading(false)
    }
  }

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
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照文件下载失败')
    }
  }

  return (
    <section className="page" data-module="export">
      <header className="page-head">
        <div>
          <h2>数据导出配置管理</h2>
          <p className="page-desc">维护分析结果的导出模板、格式配置与行数控制，实现分析数据灵活输出。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('导出模板登记入口尚未接入审批流')}>
            登记导出模板
          </button>
          <button className="btn" type="button" disabled={downloading} onClick={() => void downloadList()}>
            {downloading ? '生成中…' : '导出数据导出配置清单'}
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>模板编号</span>
          <input
            value={filters.keyword}
            placeholder="按模板编号检索"
            onChange={(event) => setFilters((prev) => ({ ...prev, keyword: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select
            value={filters.status}
            onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}
          >
            <option value="">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>导出格式</span>
          <select
            value={filters.format}
            onChange={(event) => setFilters((prev) => ({ ...prev, format: event.target.value }))}
          >
            {exportFormats.map((format) => <option key={format} value={format}>{format}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>导出上限</span>
          <input
            style={{ width: 110 }}
            value={filters.limit}
            inputMode="numeric"
            onChange={(event) => setFilters((prev) => ({ ...prev, limit: event.target.value }))}
          />
        </label>
        <div className="filter-item">
          <span>字段选择</span>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {columns.map((field) => (
              <label key={field} style={{ fontSize: 12, display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={filters.fields.includes(field)}
                  onChange={() => toggleField(field)}
                />
                {field}
              </label>
            ))}
          </div>
        </div>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => setFilters(EMPTY_FILTERS)}
        >
          重置条件
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column}>
                  {column === '模板编号' ? (
                    <Link className="link" to={`/export/${row.id}${detailSearch}`}>
                      {row[column] ?? '—'}
                    </Link>
                  ) : (
                    (row[column] ?? '—')
                  )}
                </td>
              ))}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>当前筛选条件下暂无导出模板，导出仍会下载仅含表头的空文件</td>
            </tr>
          )}
        </tbody>
      </table>

      <section style={{ marginTop: 16 }}>
        <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>导出任务清单（快照与当前列表筛选绑定）</h3>
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
                <td className="empty-state" colSpan={9}>当前筛选条件下还没有导出快照，点击上方导出按钮会生成第一条记录</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条数据导出配置记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
