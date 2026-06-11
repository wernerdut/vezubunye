import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { PhysicalCount } from '../../types'
import type { TabProps } from '../NodePage'

export default function Counts({ nodeId, config, user }: TabProps) {
  const [counts, setCounts] = useState<PhysicalCount[]>([])
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [powder, setPowder] = useState<Record<string, { w: string; f: string }>>({})
  const [store, setStore] = useState<Record<string, string>>({})
  const [floor, setFloor] = useState<Record<string, string>>({})
  const [fit, setFit] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const canCount = user.role === 'audit' || user.role === 'admin'
  const names = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))
  const cells = config.tank_types.flatMap((t) => (['A', 'B'] as const).map((g) => ({ code: t.code, grade: g })))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/counts`).then((r) => setCounts(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(''); setMsg('')
    try {
      const r = await api.post(`/api/nodes/${nodeId}/counts`, {
        date,
        powder_counted: config.powder_products.map((p) => ({
          powder_type: p.code,
          warehouse_kg: parseFloat(powder[p.code]?.w || '0') || 0,
          floor_kg: parseFloat(powder[p.code]?.f || '0') || 0,
        })),
        fg_warehouse_counted: cells.map((c) => ({ tank_type: c.code, grade: c.grade, quantity: parseInt(store[`${c.code}-${c.grade}`] || '0') || 0 })),
        tank_floor_counted: cells.map((c) => ({ tank_type: c.code, grade: c.grade, quantity: parseInt(floor[`${c.code}-${c.grade}`] || '0') || 0 })),
        fittings_counted: config.fitting_types.map((f) => ({ fitting_type: f.code, warehouse_qty: parseInt(fit[f.code] || '0') || 0 })),
      })
      const n = r.data.flags_raised.length
      setMsg(n === 0 ? 'Count matches the system exactly.' : `Count saved. ${n} variance flag(s) raised.`)
      setPowder({}); setStore({}); setFloor({}); setFit({})
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div className="grid xl:grid-cols-2 gap-6">
      {canCount && (
        <div>
          <SectionTitle>New Stocktake</SectionTitle>
          <form onSubmit={submit} className="card space-y-4">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
              <input className="input w-48" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>

            <section>
              <h3 className="text-sm font-bold text-brand-blue mb-1">Powder — warehouse + floor</h3>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Powder</th><th className="th">Warehouse (kg)</th><th className="th">Floor (kg)</th></tr></thead>
                <tbody>
                  {config.powder_products.map((p) => (
                    <tr key={p.code}>
                      <td className="td font-semibold">{p.colour}{p.is_black && p.colour.toLowerCase() !== 'black' ? ' · black stock' : ''}</td>
                      <td className="td"><input className="input w-24" type="number" step="0.1" value={powder[p.code]?.w || ''} onChange={(e) => setPowder({ ...powder, [p.code]: { ...powder[p.code], w: e.target.value, f: powder[p.code]?.f || '' } })} /></td>
                      <td className="td"><input className="input w-24" type="number" step="0.1" value={powder[p.code]?.f || ''} onChange={(e) => setPowder({ ...powder, [p.code]: { ...powder[p.code], f: e.target.value, w: powder[p.code]?.w || '' } })} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section>
              <h3 className="text-sm font-bold text-brand-blue mb-1">Tanks — store + floor</h3>
              <p className="text-xs text-gray-500 mb-1">Count both: tanks in the store and tanks on the floor (moulded, not yet booked). The store alone is incomplete.</p>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Tank</th><th className="th">Grade</th><th className="th">Store</th><th className="th">Floor</th></tr></thead>
                <tbody>
                  {cells.map((c) => (
                    <tr key={`${c.code}-${c.grade}`}>
                      <td className="td font-semibold">{names[c.code]}</td>
                      <td className="td">{c.grade}</td>
                      <td className="td"><input className="input w-20" type="number" value={store[`${c.code}-${c.grade}`] || ''} onChange={(e) => setStore({ ...store, [`${c.code}-${c.grade}`]: e.target.value })} /></td>
                      <td className="td"><input className="input w-20" type="number" value={floor[`${c.code}-${c.grade}`] || ''} onChange={(e) => setFloor({ ...floor, [`${c.code}-${c.grade}`]: e.target.value })} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {config.fitting_types.length > 0 && (
              <section>
                <h3 className="text-sm font-bold text-brand-blue mb-1">Fittings — warehouse</h3>
                <table className="w-full text-sm">
                  <thead><tr><th className="th">Fitting</th><th className="th">Warehouse (qty)</th></tr></thead>
                  <tbody>
                    {config.fitting_types.map((f) => (
                      <tr key={f.code}>
                        <td className="td font-semibold">{f.name}</td>
                        <td className="td"><input className="input w-24" type="number" value={fit[f.code] || ''} onChange={(e) => setFit({ ...fit, [f.code]: e.target.value })} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            {msg && <p className="text-sm font-semibold text-brand-green">{msg}</p>}
            {error && <p className="text-sm text-brand-red">{error}</p>}
            <button className="btn-primary w-full">Save stocktake</button>
          </form>
        </div>
      )}

      <div className={canCount ? '' : 'xl:col-span-2'}>
        <SectionTitle>Count History</SectionTitle>
        <div className="space-y-3">
          {counts.length === 0 && <div className="card"><Empty text="No stocktakes yet" /></div>}
          {counts.map((c) => {
            const v = c.variances
            const probs = [
              ...v.powder_warehouse.filter((x) => x.variance !== 0).map((x) => `Powder ${x.powder_type} store ${x.variance > 0 ? '+' : ''}${x.variance} kg`),
              ...v.powder_floor.filter((x) => x.variance !== 0).map((x) => `${x.pool} floor ${x.variance > 0 ? '+' : ''}${x.variance} kg`),
              ...v.tanks.filter((x) => x.variance !== 0).map((x) => `${names[x.tank_type] || x.tank_type} ${x.grade} ${x.variance > 0 ? '+' : ''}${x.variance}`),
              ...v.fittings.filter((x) => x.variance !== 0).map((x) => `Fitting ${x.fitting_type} ${x.variance > 0 ? '+' : ''}${x.variance}`),
            ]
            return (
              <div key={c._id} className="card">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold">{c.date}</span>
                  <span className="text-xs text-gray-500">{c.counted_by}</span>
                </div>
                {probs.length === 0 ? (
                  <p className="text-sm text-brand-green font-semibold">Everything reconciles (store + floor = system).</p>
                ) : (
                  <ul className="text-sm text-brand-red list-disc ml-5">{probs.map((p, i) => <li key={i}>{p}</li>)}</ul>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
