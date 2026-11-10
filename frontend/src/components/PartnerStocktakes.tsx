import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import type { User } from '../types'
import { StatusBadge } from './ui'

const KIND: Record<string, string> = {
  tank_store: 'store', tank_floor: 'floor', powder_stock: 'kg', powder_warehouse: 'store kg', powder_floor: 'floor kg', fittings: 'fittings',
}

/** Stocktakes counted in the partner app. Nothing moves until audit or admin signs off here:
 * sign-off sets each position to the count (one adjustment per difference, struck against the
 * system as it stands now). Send back asks for a recount and changes nothing. */
export default function PartnerStocktakes({ nodeId, user, onChanged }: { nodeId: string; user: User; onChanged?: () => void }) {
  const [rows, setRows] = useState<any[] | null>(null)
  const [note, setNote] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const canSign = user.role === 'audit' || user.role === 'admin'
  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/stocktakes`).then((r) => setRows(r.data)).catch(() => setRows([]))
  }, [nodeId])
  useEffect(load, [load])
  if (!rows || rows.length === 0) return null
  const act = async (id: string, what: 'approve' | 'return') => {
    setError(''); setBusy(true)
    try {
      await api.post(`/api/stocktakes/${id}/${what}`, { note: note[id] || '' })
      load(); onChanged?.()
    } catch (e) { setError(errMsg(e)) } finally { setBusy(false) }
  }
  return (
    <div className="card mb-6">
      <h3 className="text-sm font-bold text-brand-blue mb-1">Stocktakes from the partner app</h3>
      <p className="text-xs text-gray-500 mb-3">Counted blind by the partner. Signing off sets stock to the count; sending back asks for a recount and changes nothing.</p>
      {error && <p className="text-sm text-brand-red mb-2">{error}</p>}
      <div className="space-y-4">
        {rows.slice(0, 6).map((s) => {
          const diffs = (s.variances || []).filter((v: any) => v.variance !== 0)
          return (
            <div key={s._id} className="border-t pt-3">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <span className="font-semibold">{s.date}{s.opening ? ' · opening stock' : ''} · {s.submitted_by_name}</span>
                <StatusBadge status={s.status === 'submitted' ? 'pending' : s.status === 'approved' ? 'resolved' : s.status} />
              </div>
              {s.notes && <p className="text-xs text-gray-600 mb-1">“{s.notes}”</p>}
              {s.status === 'returned' && <p className="text-xs text-brand-orange mb-1">Sent back: {s.review?.note}</p>}
              {(s.status === 'submitted' || s.status === 'approved') && (
                diffs.length === 0 ? <p className="text-sm text-brand-green font-semibold">Count matches the system.</p> : (
                  <table className="w-full text-sm">
                    <thead><tr><th className="th">Position</th><th className="th text-right">System</th><th className="th text-right">Counted</th><th className="th text-right">Change</th></tr></thead>
                    <tbody>{diffs.map((v: any, i: number) => (
                      <tr key={i}><td className="td">{v.label} <span className="text-gray-400">{KIND[v.kind]}</span></td>
                        <td className="td text-right tabular-nums">{v.system}</td><td className="td text-right tabular-nums">{v.counted}</td>
                        <td className={`td text-right font-semibold tabular-nums ${v.variance < 0 ? 'text-brand-red' : 'text-brand-green'}`}>{v.variance > 0 ? '+' : ''}{v.variance}</td></tr>
                    ))}</tbody>
                  </table>
                ))}
              {s.status === 'submitted' && canSign && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <input className="input flex-1 min-w-[12rem]" placeholder="Note (required to send back)" value={note[s._id] || ''} onChange={(e) => setNote({ ...note, [s._id]: e.target.value })} />
                  <button className="btn-secondary" disabled={busy || !(note[s._id] || '').trim()} onClick={() => act(s._id, 'return')}>Send back</button>
                  <button className="btn-primary" disabled={busy} onClick={() => act(s._id, 'approve')}>Sign off: set stock to count</button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
