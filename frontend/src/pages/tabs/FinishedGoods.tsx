import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { FGEntry, OnHand, ScrapEntry } from '../../types'
import type { TabProps } from '../NodePage'

export default function FinishedGoods({ nodeId, config, user }: TabProps) {
  const [entries, setEntries] = useState<FGEntry[]>([])
  const [onHand, setOnHand] = useState<OnHand[]>([])
  const [scrap, setScrap] = useState<ScrapEntry[]>([])
  const [adj, setAdj] = useState({ date: new Date().toISOString().slice(0, 10), tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: '', notes: '' })
  const [error, setError] = useState('')

  const names = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/finished-goods`).then((r) => {
      setEntries([...r.data.entries].reverse())
      setOnHand(r.data.on_hand)
    })
    api.get(`/api/nodes/${nodeId}/scrap`).then((r) => setScrap(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const adjust = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/finished-goods/adjustment`, {
        ...adj, quantity: parseInt(adj.quantity),
      })
      setAdj({ ...adj, quantity: '', notes: '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <SectionTitle>On Hand</SectionTitle>
        <div className="flex flex-wrap gap-3">
          {onHand.length === 0 && <p className="text-sm text-gray-400">Nothing in stock yet.</p>}
          {onHand.map((o) => (
            <div key={`${o.tank_type}-${o.grade}`} className={`card text-center min-w-36 ${o.quantity < 0 ? 'border-brand-red' : ''}`}>
              <div className={`font-headline text-4xl ${o.quantity < 0 ? 'text-brand-red' : 'text-brand-blue'}`}>{o.quantity}</div>
              <div className="text-sm font-semibold">{names[o.tank_type] || o.tank_type}</div>
              <div className={`text-xs font-semibold ${o.grade === 'A' ? 'text-brand-green' : 'text-brand-orange'}`}>Grade {o.grade}</div>
            </div>
          ))}
        </div>
      </div>

      {(user.role === 'controller' || user.role === 'admin') && (
        <form onSubmit={adjust} className="card flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Adjustment date</label>
            <input className="input" type="date" value={adj.date} onChange={(e) => setAdj({ ...adj, date: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Tank</label>
            <select className="input" value={adj.tank_type} onChange={(e) => setAdj({ ...adj, tank_type: e.target.value })}>
              {config.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Grade</label>
            <select className="input" value={adj.grade} onChange={(e) => setAdj({ ...adj, grade: e.target.value })}>
              <option value="A">A</option>
              <option value="B">B</option>
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
        <SectionTitle>Movements</SectionTitle>
        <div className="card p-0 overflow-hidden">
          {entries.length === 0 ? (
            <Empty text="No movements yet" />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Tank</th>
                  <th className="th">Grade</th>
                  <th className="th">Type</th>
                  <th className="th text-right">Qty</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e._id}>
                    <td className="td">{e.date}</td>
                    <td className="td font-semibold">{names[e.tank_type] || e.tank_type}</td>
                    <td className="td">{e.grade}</td>
                    <td className="td">
                      <span className={`font-semibold ${e.type === 'produced' ? 'text-brand-green' : e.type === 'delivered' ? 'text-brand-blue' : 'text-brand-orange'}`}>
                        {e.type.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="td text-right font-semibold">{e.type === 'delivered' ? '−' : '+'}{Math.abs(e.quantity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div>
        <SectionTitle>Scrap Log</SectionTitle>
        <p className="text-sm text-gray-500 mb-3">Rejects exit the system here. A scrapped tank still accounts for its powder.</p>
        <div className="card p-0 overflow-hidden">
          {scrap.length === 0 ? (
            <Empty text="No scrap recorded" />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Tank</th>
                  <th className="th text-right">Qty</th>
                  <th className="th text-right">kg lost</th>
                  {user.role === 'admin' && <th className="th text-right">Material cost</th>}
                </tr>
              </thead>
              <tbody>
                {scrap.map((s) => (
                  <tr key={s._id}>
                    <td className="td">{s.date}</td>
                    <td className="td font-semibold">{names[s.tank_type] || s.tank_type}</td>
                    <td className="td text-right">{s.quantity}</td>
                    <td className="td text-right text-brand-red font-semibold">{s.kg_lost.toLocaleString('en-ZA')}</td>
                    {user.role === 'admin' && (
                      <td className="td text-right text-brand-red font-semibold">
                        R {(s.material_cost_lost ?? 0).toLocaleString('en-ZA', { minimumFractionDigits: 2 })}
                      </td>
                    )}
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
