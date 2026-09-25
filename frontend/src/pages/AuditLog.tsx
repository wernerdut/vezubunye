import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { Empty } from '../components/ui'
import type { AuditEntry, NodeInfo } from '../types'

const COLLECTIONS = [
  'daily_captures', 'delivery_notes', 'payments', 'powder_ledger', 'fittings_ledger',
  'finished_goods_ledger', 'physical_counts', 'flags', 'delivery_documents', 'node_config', 'nodes', 'users',
]
const ACTIONS = ['create', 'update', 'edit', 'void', 'reissue', 'match', 'unmatch', 'resolve', 'reopen', 'delete']

type Flat = Record<string, string>

/** Flatten nested before/after into dotted paths so a diff reads line by line. */
function flatten(v: unknown, prefix = '', out: Flat = {}): Flat {
  if (v !== null && typeof v === 'object') {
    const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as object)
    if (entries.length === 0) out[prefix || '(empty)'] = Array.isArray(v) ? '[]' : '{}'
    for (const [k, x] of entries) flatten(x, prefix ? `${prefix}.${k}` : k, out)
  } else if (prefix) {
    out[prefix] = v === null || v === undefined ? '—' : String(v)
  }
  return out
}

function Diff({ before, after }: { before: unknown; after: unknown }) {
  const b = flatten(before)
  const a = flatten(after)
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort()
  if (keys.length === 0) return <p className="text-xs text-gray-400">No detail recorded.</p>
  return (
    <table className="w-full text-xs font-mono">
      <thead><tr><th className="th">Field</th><th className="th">Before</th><th className="th">After</th></tr></thead>
      <tbody>
        {keys.map((k) => {
          const changed = b[k] !== a[k]
          return (
            <tr key={k} className={changed ? 'bg-yellow-50' : ''}>
              <td className="px-3 py-1 text-gray-500 align-top">{k}</td>
              <td className={`px-3 py-1 align-top break-all ${changed && b[k] !== undefined ? 'text-brand-red' : ''}`}>{b[k] ?? ''}</td>
              <td className={`px-3 py-1 align-top break-all ${changed && a[k] !== undefined ? 'text-brand-green' : ''}`}>{a[k] ?? ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function AuditLog() {
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [rows, setRows] = useState<AuditEntry[]>([])
  const [f, setF] = useState({ node_id: '', collection: '', action: '', by: '', date_from: '', date_to: '' })
  const [openId, setOpenId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => { api.get('/api/nodes').then((r) => setNodes(r.data)) }, [])

  const load = useCallback(() => {
    const params = Object.fromEntries(Object.entries(f).filter(([, v]) => v))
    setLoading(true); setError('')
    api.get('/api/audit', { params: { ...params, limit: 500 } })
      .then((r) => setRows(r.data))
      .catch((e) => setError(errMsg(e)))
      .finally(() => setLoading(false))
  }, [f])
  useEffect(load, [load])

  const sel = (key: keyof typeof f, label: string, options: string[]) => (
    <div>
      <label className="block text-xs font-semibold text-gray-600 mb-1">{label}</label>
      <select className="input" value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })}>
        <option value="">all</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500">Every write, who made it, and the record before and after. Corrections carry their reason and the ledger rows they voided.</p>
      <div className="card grid grid-cols-2 md:grid-cols-6 gap-3">
        {sel('node_id', 'Node', nodes.map((n) => n.node_id))}
        {sel('collection', 'Collection', COLLECTIONS)}
        {sel('action', 'Action', ACTIONS)}
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">User</label>
          <input className="input" placeholder="email" value={f.by} onChange={(e) => setF({ ...f, by: e.target.value.trim() })} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">From</label>
          <input className="input" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">To</label>
          <input className="input" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
        </div>
      </div>
      {error && <p className="text-sm text-brand-red">{error}</p>}
      <div className="card p-0 overflow-x-auto">
        {rows.length === 0 ? (
          <Empty text={loading ? 'Loading…' : 'No audit entries match'} />
        ) : (
          <table className="w-full">
            <thead>
              <tr><th className="th">When</th><th className="th">Node</th><th className="th">User</th><th className="th">Action</th><th className="th">Collection</th><th className="th">Record</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r._id}>
                  <tr className="cursor-pointer hover:bg-gray-50" onClick={() => setOpenId(openId === r._id ? null : r._id)}>
                    <td className="td whitespace-nowrap">{String(r.at).slice(0, 19).replace('T', ' ')}</td>
                    <td className="td">{r.node_id}</td>
                    <td className="td text-gray-600">{r.by} <span className="text-xs text-gray-400">({r.role})</span></td>
                    <td className={`td font-semibold ${['void', 'unmatch', 'reissue', 'reopen', 'edit', 'delete'].includes(r.action) ? 'text-brand-orange' : 'text-brand-blue'}`}>{r.action}</td>
                    <td className="td">{r.collection}</td>
                    <td className="td text-xs text-gray-400 font-mono">{r.doc_id?.slice(0, 8)}</td>
                  </tr>
                  {openId === r._id && (
                    <tr><td className="td bg-gray-50" colSpan={6}><Diff before={r.before} after={r.after} /></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
