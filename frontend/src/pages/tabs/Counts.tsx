import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { PhysicalCount } from '../../types'
import type { TabProps } from '../NodePage'

export default function Counts({ nodeId, config, user }: TabProps) {
  const [counts, setCounts] = useState<PhysicalCount[]>([])
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [powderKg, setPowderKg] = useState('')
  const [fg, setFg] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const canCount = user.role === 'controller' || user.role === 'admin'
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
        powder_kg_counted: parseFloat(powderKg),
        finished_goods_counted: cells.map((c) => ({
          tank_type: c.code, grade: c.grade, quantity: parseInt(fg[`${c.code}-${c.grade}`] || '0') || 0,
        })),
      })
      const n = r.data.flags_raised.length
      setMsg(n === 0 ? 'Count matches the system exactly.' : `Count saved. ${n} variance flag(s) raised.`)
      setPowderKg(''); setFg({})
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div className="grid lg:grid-cols-2 gap-6">
      {canCount && (
        <div>
          <SectionTitle>New Physical Count</SectionTitle>
          <form onSubmit={submit} className="card space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
                <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Powder counted (kg)</label>
                <input className="input" type="number" step="0.1" min="0" value={powderKg} onChange={(e) => setPowderKg(e.target.value)} required />
              </div>
            </div>
            <table className="w-full">
              <thead>
                <tr><th className="th">Tank</th><th className="th">Grade</th><th className="th">Counted</th></tr>
              </thead>
              <tbody>
                {cells.map((c) => (
                  <tr key={`${c.code}-${c.grade}`}>
                    <td className="td font-semibold">{names[c.code]}</td>
                    <td className="td">{c.grade}</td>
                    <td className="td">
                      <input className="input w-24" type="number" min="0" value={fg[`${c.code}-${c.grade}`] || ''}
                             onChange={(e) => setFg({ ...fg, [`${c.code}-${c.grade}`]: e.target.value })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {msg && <p className="text-sm font-semibold text-brand-green">{msg}</p>}
            {error && <p className="text-sm text-brand-red">{error}</p>}
            <button className="btn-primary w-full">Save count</button>
          </form>
        </div>
      )}
      <div className={canCount ? '' : 'lg:col-span-2'}>
        <SectionTitle>Count History</SectionTitle>
        <div className="space-y-3">
          {counts.length === 0 && <div className="card"><Empty text="No physical counts yet" /></div>}
          {counts.map((c) => (
            <div key={c._id} className="card">
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold">{c.date}</span>
                <span className="text-xs text-gray-500">{c.counted_by}</span>
              </div>
              <p className="text-sm">
                Powder: counted <b>{c.powder_kg_counted.toLocaleString('en-ZA')} kg</b> vs system{' '}
                <b>{c.system_values_at_count.powder_kg.toLocaleString('en-ZA')} kg</b>{' '}
                <span className={c.variances.powder_kg === 0 ? 'text-brand-green font-semibold' : 'text-brand-red font-semibold'}>
                  ({c.variances.powder_kg >= 0 ? '+' : ''}{c.variances.powder_kg} kg)
                </span>
              </p>
              {c.variances.finished_goods.length > 0 ? (
                <ul className="text-sm text-brand-red mt-1">
                  {c.variances.finished_goods.map((v, i) => (
                    <li key={i}>
                      {names[v.tank_type] || v.tank_type} {v.grade}: counted {v.counted} vs system {v.system} ({v.variance > 0 ? '+' : ''}{v.variance})
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-brand-green mt-1">Finished goods match the system.</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
