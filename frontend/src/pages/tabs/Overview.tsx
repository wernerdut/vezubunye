import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { SectionTitle } from '../../components/ui'
import type { NodeDashboardData } from '../../types'
import type { TabProps } from '../NodePage'

const n0 = (n: number) => n.toLocaleString('en-ZA', { maximumFractionDigits: 0 })
const n1 = (n: number) => n.toLocaleString('en-ZA', { maximumFractionDigits: 1 })
const rand = (n: number) => `R ${n.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`
const monthLabel = (m: string) => new Date(`${m}-01T00:00`).toLocaleDateString('en-ZA', { month: 'short', year: 'numeric' })

export default function Overview({ nodeId, config, user }: TabProps) {
  const [year, setYear] = useState(new Date().getFullYear().toString())
  const [data, setData] = useState<NodeDashboardData | null>(null)
  const [error, setError] = useState('')
  const isAdmin = user.role === 'admin'
  const tankTypes = config.tank_types.map((t) => t.code)
  const tankName = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/dashboard?year=${year}`).then((r) => setData(r.data)).catch((e) => setError(errMsg(e)))
  }, [nodeId, year])
  useEffect(load, [load])

  if (error) return <p className="text-brand-red">{error}</p>
  if (!data) return <p className="text-gray-400">Loading…</p>

  const qtyFor = (row: { tanks_by_type: { tank_type: string; total: number }[] }, code: string) =>
    row.tanks_by_type.find((t) => t.tank_type === code)?.total || 0
  const yt = data.year_totals

  const card = (value: string, label: string, accent = 'text-brand-blue') => (
    <div className="card">
      <div className={`font-headline text-3xl ${accent}`}>{value}</div>
      <div className="text-xs text-gray-500 uppercase tracking-wide">{label}</div>
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <SectionTitle>Overview</SectionTitle>
        <select className="input w-28" value={year} onChange={(e) => setYear(e.target.value)}>
          {(data.years.length ? data.years : [year]).map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <span className="text-xs text-gray-500">
          All time: <b>{n0(data.all_time.total_produced)}</b> produced · <b>{n0(data.all_time.total_sold)}</b> sold ·
          {' '}<b>{n1(data.all_time.total_material_kg)}</b> kg{isAdmin && data.all_time.material_cost !== undefined ? ` · ${rand(data.all_time.material_cost)}` : ''}
        </span>
      </div>

      {/* Year headline cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {card(n0(yt.total_produced), `Tanks produced ${year}`)}
        {card(n0(yt.total_sold), `Tanks sold ${year}`, 'text-brand-green')}
        {card(`${n1(yt.total_material_kg)} kg`, `Material ${year}`)}
        {isAdmin && yt.material_cost !== undefined && card(rand(yt.material_cost), `Material cost ${year}`)}
      </div>

      {/* Monthly tanks */}
      <div>
        <SectionTitle>Tanks per month</SectionTitle>
        <div className="card p-0 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Month</th>
                {tankTypes.map((c) => <th className="th text-right" key={c}>{tankName[c]}</th>)}
                <th className="th text-right">Produced</th>
                <th className="th text-right">Sold</th>
                <th className="th text-right">Material (kg)</th>
              </tr>
            </thead>
            <tbody>
              {data.months.length === 0 && (
                <tr><td className="td text-gray-400" colSpan={tankTypes.length + 4}>No production in {year}.</td></tr>
              )}
              {data.months.map((m) => (
                <tr key={m.month}>
                  <td className="td font-semibold">{monthLabel(m.month!)}</td>
                  {tankTypes.map((c) => <td className="td text-right" key={c}>{qtyFor(m, c) || '—'}</td>)}
                  <td className="td text-right font-semibold text-brand-blue">{m.total_produced}</td>
                  <td className="td text-right font-semibold text-brand-green">{m.total_sold}</td>
                  <td className="td text-right">{n1(m.total_material_kg)}</td>
                </tr>
              ))}
            </tbody>
            {data.months.length > 0 && (
              <tfoot>
                <tr className="border-t-2 border-gray-300">
                  <td className="td font-bold">{year} total</td>
                  {tankTypes.map((c) => <td className="td text-right font-bold" key={c}>{qtyFor(yt, c) || '—'}</td>)}
                  <td className="td text-right font-bold text-brand-blue">{yt.total_produced}</td>
                  <td className="td text-right font-bold text-brand-green">{yt.total_sold}</td>
                  <td className="td text-right font-bold">{n1(yt.total_material_kg)}</td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>

      {/* Material by grade for the year */}
      <div>
        <SectionTitle>Material by grade — {year}</SectionTitle>
        <div className="card p-0 overflow-hidden">
          <table className="w-full">
            <thead><tr><th className="th">Powder grade</th><th className="th text-right">kg</th></tr></thead>
            <tbody>
              {yt.material_by_colour.length === 0 && <tr><td className="td text-gray-400" colSpan={2}>No material drawn in {year}.</td></tr>}
              {yt.material_by_colour.map((m) => (
                <tr key={m.colour}>
                  <td className="td font-semibold">{m.colour}</td>
                  <td className="td text-right">{n1(m.kg)}</td>
                </tr>
              ))}
            </tbody>
            {yt.material_by_colour.length > 0 && (
              <tfoot><tr className="border-t-2 border-gray-300"><td className="td font-bold">Total</td><td className="td text-right font-bold">{n1(yt.total_material_kg)}</td></tr></tfoot>
            )}
          </table>
        </div>
      </div>
    </div>
  )
}
