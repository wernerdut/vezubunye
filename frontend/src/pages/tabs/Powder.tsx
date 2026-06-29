import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { ParaffinData, PowderData } from '../../types'
import type { TabProps } from '../NodePage'

export default function Powder({ nodeId, config, user }: TabProps) {
  const [data, setData] = useState<PowderData | null>(null)
  const [paraffin, setParaffin] = useState<ParaffinData | null>(null)
  const [adj, setAdj] = useState({ date: new Date().toISOString().slice(0, 10), powder_type: '', scope: 'warehouse', kg: '', notes: '' })
  const [error, setError] = useState('')

  const canAdjust = user.role === 'audit' || user.role === 'admin'
  const colourOf = (code: string) => config.powder_products.find((p) => p.code === code)?.colour || code

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/powder`).then((r) => setData(r.data))
    api.get(`/api/nodes/${nodeId}/paraffin`).then((r) => setParaffin(r.data)).catch(() => {})
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
        <SectionTitle>Powder Stock</SectionTitle>
        <p className="text-sm text-gray-500 mb-3">
          Per powder grade. <b>Warehouse</b> = received from Fenix less issued. <b>Floor</b> = issued less
          moulded into tanks (each tank draws half colour, half black, plus a black lid). A negative floor
          means more of that grade was moulded than issued — that flags.
        </p>
        <div className="card p-0 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr><th className="th">Powder grade</th><th className="th text-right">Warehouse (kg)</th><th className="th text-right">Floor (kg)</th><th className="th text-right">Total on site (kg)</th></tr>
            </thead>
            <tbody>
              {data.stock.length === 0 && <tr><td className="td" colSpan={4}><Empty text="No powder yet" /></td></tr>}
              {data.stock.map((s) => (
                <tr key={s.powder_type}>
                  <td className="td font-semibold">{s.colour}{s.is_black && s.colour.toLowerCase() !== 'black' ? ' · black stock' : ''}{s.is_black ? '' : ''}</td>
                  <td className={`td text-right font-semibold ${s.warehouse < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{fmt(s.warehouse)}</td>
                  <td className={`td text-right font-semibold ${s.floor < 0 ? 'text-brand-red' : ''}`}>{fmt(s.floor)}</td>
                  <td className="td text-right">{fmt(s.warehouse + s.floor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {paraffin && (
        <div>
          <SectionTitle>Paraffin</SectionTitle>
          <p className="text-sm text-gray-500 mb-3">
            Release agent. <b>On hand</b> = received less used, drawn at <b>{paraffin.litres_per_tank} L per tank</b> moulded.
          </p>
          <div className="card p-0 overflow-hidden">
            <table className="w-full">
              <thead>
                <tr><th className="th text-right">Received (L)</th><th className="th text-right">Used (L)</th><th className="th text-right">On hand (L)</th><th className="th text-right">Tanks moulded</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td className="td text-right font-semibold text-brand-green">{fmt(paraffin.received)}</td>
                  <td className="td text-right font-semibold">{fmt(paraffin.consumed)}</td>
                  <td className={`td text-right font-semibold ${paraffin.balance < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{fmt(paraffin.balance)}</td>
                  <td className="td text-right text-gray-500">{paraffin.tanks}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

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
