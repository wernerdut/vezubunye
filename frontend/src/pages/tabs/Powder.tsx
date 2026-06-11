import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { PowderData } from '../../types'
import type { TabProps } from '../NodePage'

export default function Powder({ nodeId, config, user }: TabProps) {
  const [data, setData] = useState<PowderData | null>(null)
  const [adj, setAdj] = useState({ date: new Date().toISOString().slice(0, 10), powder_type: '', scope: 'warehouse', kg: '', notes: '' })
  const [error, setError] = useState('')

  const canAdjust = user.role === 'audit' || user.role === 'admin'
  const colourOf = (code: string) => config.powder_products.find((p) => p.code === code)?.colour || code

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/powder`).then((r) => setData(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const adjust = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/powder/adjustment`, { ...adj, kg: parseFloat(adj.kg) })
      setAdj({ ...adj, kg: '', notes: '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  if (!data) return <p className="text-gray-400">Loading…</p>
  const fmt = (n: number) => n.toLocaleString('en-ZA', { maximumFractionDigits: 1 })

  return (
    <div className="space-y-6">
      <div>
        <SectionTitle>Powder Warehouse</SectionTitle>
        <p className="text-sm text-gray-500 mb-3">Per powder type: received from Fenix, less issued to production.</p>
        <div className="flex flex-wrap gap-3">
          {data.warehouse.length === 0 && <p className="text-sm text-gray-400">No powder received yet.</p>}
          {data.warehouse.map((w) => (
            <div key={w.powder_type} className={`card text-center min-w-40 ${w.balance < 0 ? 'border-brand-red' : ''}`}>
              <div className={`font-headline text-3xl ${w.balance < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{fmt(w.balance)}</div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">kg in store</div>
              <div className="text-sm font-semibold mt-1">{w.colour}{w.is_black && w.colour.toLowerCase() !== 'black' ? ' · black stock' : ''}</div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <SectionTitle>Production Floor</SectionTitle>
        <p className="text-sm text-gray-500 mb-3">
          Powder issued to the floor, less powder moulded into tanks. Each tank uses half colour, half black,
          plus a black lid. Either pool going negative means more was moulded than issued — that flags.
        </p>
        <div className="flex flex-wrap gap-3">
          {(['black', 'colour'] as const).map((pool) => (
            <div key={pool} className={`card text-center min-w-40 ${data.floor[pool] < 0 ? 'border-brand-red' : ''}`}>
              <div className={`font-headline text-3xl ${data.floor[pool] < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{fmt(data.floor[pool])}</div>
              <div className="text-xs text-gray-500 uppercase tracking-wide">kg on floor</div>
              <div className="text-sm font-semibold mt-1 capitalize">{pool} pool</div>
            </div>
          ))}
        </div>
      </div>

      {canAdjust && (
        <form onSubmit={adjust} className="card flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Adjustment date</label>
            <input className="input" type="date" value={adj.date} onChange={(e) => setAdj({ ...adj, date: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Powder</label>
            <select className="input" value={adj.powder_type} onChange={(e) => setAdj({ ...adj, powder_type: e.target.value })} required>
              <option value="">select…</option>
              {config.powder_products.map((p) => <option key={p.code} value={p.code}>{colourOf(p.code)}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Position</label>
            <select className="input" value={adj.scope} onChange={(e) => setAdj({ ...adj, scope: e.target.value })}>
              <option value="warehouse">warehouse</option>
              <option value="floor">floor</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">kg (+/-)</label>
            <input className="input w-28" type="number" step="0.1" value={adj.kg} onChange={(e) => setAdj({ ...adj, kg: e.target.value })} required />
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
        <SectionTitle>Movements</SectionTitle>
        <div className="card p-0 overflow-hidden">
          {data.entries.length === 0 ? (
            <Empty text="No powder movements yet" />
          ) : (
            <table className="w-full">
              <thead><tr><th className="th">Date</th><th className="th">Powder</th><th className="th">Type</th><th className="th text-right">kg</th><th className="th">Notes</th></tr></thead>
              <tbody>
                {[...data.entries].reverse().map((e) => (
                  <tr key={e._id}>
                    <td className="td">{e.date}</td>
                    <td className="td font-semibold">{colourOf(e.powder_type)}</td>
                    <td className="td">
                      <span className={`font-semibold ${e.type === 'received' ? 'text-brand-green' : e.type === 'issued' ? 'text-brand-blue' : 'text-brand-orange'}`}>
                        {e.type}
                      </span>
                    </td>
                    <td className="td text-right">{e.type === 'issued' ? '−' : '+'}{Math.abs(e.kg).toLocaleString('en-ZA')}</td>
                    <td className="td text-gray-500">{e.notes}</td>
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
