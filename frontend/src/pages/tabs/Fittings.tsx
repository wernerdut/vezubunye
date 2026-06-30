import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { FittingsData } from '../../types'
import type { TabProps } from '../NodePage'

export default function Fittings({ nodeId, config, user }: TabProps) {
  const [data, setData] = useState<FittingsData | null>(null)
  const [adj, setAdj] = useState({ date: new Date().toISOString().slice(0, 10), fitting_type: '', quantity: '', notes: '' })
  const [error, setError] = useState('')

  const canAdjust = user.role === 'audit' || user.role === 'admin'
  const nameOf = (code: string) => config.fitting_types.find((f) => f.code === code)?.name || code

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/fittings`).then((r) => setData(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const adjust = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/fittings/adjustment`, { ...adj, quantity: parseInt(adj.quantity) })
      setAdj({ ...adj, quantity: '', notes: '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  if (!data) return <p className="text-gray-400">Loading…</p>

  if (config.fitting_types.length === 0 && data.warehouse.length === 0) {
    return (
      <div>
        <SectionTitle>Fittings</SectionTitle>
        <div className="card text-sm text-gray-500">
          No fitting types configured yet. An admin can add them (and the fittings-per-tank counts) in Admin → Config,
          after which fittings stock and the consumption-vs-tanks-produced check appear here.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <SectionTitle>Fittings Warehouse</SectionTitle>
        <p className="text-sm text-gray-500 mb-3">
          Per fitting type: received from Fenix less issued. Issued should match tanks produced × the
          fittings-per-tank standard; any gap flags.
        </p>
        <div className="card p-0 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Fitting</th>
                <th className="th text-right">In store</th>
                <th className="th text-right">Issued</th>
                <th className="th text-right">Expected (per tanks)</th>
                <th className="th text-right">Variance</th>
              </tr>
            </thead>
            <tbody>
              {data.warehouse.length === 0 && <tr><td className="td" colSpan={5}><Empty text="No fittings yet" /></td></tr>}
              {data.warehouse.map((w) => (
                <tr key={w.fitting_type}>
                  <td className="td font-semibold">{w.name}</td>
                  <td className={`td text-right font-semibold ${w.balance < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{w.balance}</td>
                  <td className="td text-right">{w.issued}</td>
                  <td className="td text-right">{w.expected}</td>
                  <td className={`td text-right font-semibold ${w.variance === 0 ? 'text-brand-green' : 'text-brand-red'}`}>
                    {w.variance > 0 ? '+' : ''}{w.variance}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {canAdjust && (
        <form onSubmit={adjust} className="card flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Adjustment date</label>
            <input className="input" type="date" value={adj.date} onChange={(e) => setAdj({ ...adj, date: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Fitting</label>
            <select className="input" value={adj.fitting_type} onChange={(e) => setAdj({ ...adj, fitting_type: e.target.value })} required>
              <option value="">select…</option>
              {config.fitting_types.map((f) => <option key={f.code} value={f.code}>{f.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Qty (+/-)</label>
            <input className="input w-24" type="number" value={adj.quantity} onChange={(e) => setAdj({ ...adj, quantity: e.target.value })} required />
          </div>
          <div className="flex-1 min-w-48">
            <label className="block text-xs font-semibold text-gray-600 mb-1">Reason</label>
            <input className="input" value={adj.notes} onChange={(e) => setAdj({ ...adj, notes: e.target.value })} required />
          </div>
          <button className="btn-secondary">Post adjustment</button>
          {error && <p className="text-sm text-brand-red w-full">{error}</p>}
        </form>
      )}

      <div>
        <SectionTitle>Fittings movements</SectionTitle>
        <div className="card p-0 overflow-hidden">
          {data.entries.length === 0 ? (
            <Empty text="No fittings movements yet" />
          ) : (
            <table className="w-full">
              <thead><tr><th className="th">Date</th><th className="th">Fitting</th><th className="th">Type</th><th className="th text-right">Qty</th></tr></thead>
              <tbody>
                {[...data.entries].reverse().map((e) => (
                  <tr key={e._id}>
                    <td className="td">{e.date}</td>
                    <td className="td font-semibold">{nameOf(e.fitting_type)}</td>
                    <td className="td">
                      <span className={`font-semibold ${e.type === 'received' ? 'text-brand-green' : e.type === 'issued' ? 'text-brand-blue' : 'text-brand-orange'}`}>{e.type}</span>
                    </td>
                    <td className="td text-right">{e.type === 'issued' ? '−' : '+'}{Math.abs(e.quantity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
