import { useEffect, useState } from 'react'
import { api } from '../api'
import { PageTitle, Spinner } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { StocktakeHistory } from './Stocktake'

const kg = (v: number) => v ? `${Math.round(v).toLocaleString('en-ZA')} kg` : '–'

export default function Stock() {
  const { cfg } = useApp()
  const [s, setS] = useState<any>(null)
  const load = () => api.get('/api/p/stock').then((r) => setS(r.data))
  useEffect(() => { load() }, [])
  if (!s) return <Spinner />
  return (
    <div>
      <PageTitle title="Stock" sub="Finished tanks against what is still to deliver." />
      <div className="card mb-4 overflow-x-auto">
        <table className="min-w-full">
          <thead className="border-b bg-gray-50"><tr><th className="th">Tank</th><th className="th">Colour</th><th className="th">Grade</th><th className="th text-right">On hand</th><th className="th text-right">To deliver</th><th className="th text-right">Free</th></tr></thead>
          <tbody>{s.finished_goods.map((r: any, i: number) => {
            const c = colourOf(cfg, r.colour)
            return <tr key={i} className="border-b"><td className="td">{tankName(cfg, r.tank_type)}</td>
              <td className="td">{c && <span className="mr-2 inline-block h-3 w-3 rounded-sm align-middle" style={{ background: c.swatch_hex }} />}{c?.name || r.colour || '—'}</td>
              <td className="td">{r.grade}</td><td className="td text-right tabular-nums">{r.on_hand}</td><td className="td text-right tabular-nums">{r.to_deliver || ''}</td>
              <td className={`td text-right font-semibold tabular-nums ${r.free < 0 ? 'text-red-700' : ''}`}>{r.free}</td></tr>
          })}</tbody>
        </table>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="card overflow-x-auto"><h2 className="border-b p-3 font-bold">Powder</h2>
          <table className="min-w-full text-sm">
            <thead className="border-b bg-gray-50"><tr><th className="th">Colour</th><th className="th text-right">Stock</th></tr></thead>
            <tbody>{(s.powder_positions || []).map((p: any) => (
              <tr key={p.key} className="border-b"><td className="td">{p.name}</td>
                <td className={`td text-right font-semibold tabular-nums ${p.kg < 0 ? 'text-red-700' : ''}`}>{kg(p.kg)}</td></tr>))}</tbody>
          </table>
          <p className="p-3 text-xs text-gray-500">Powder received, less what the captured tanks used, corrected by stocktakes.</p></div>
        {s.fittings.length > 0 && <div className="card"><h2 className="border-b p-3 font-bold">Fittings</h2>
          {s.fittings.map((p: any) => <div key={p.fitting_type} className="flex justify-between border-b px-3 py-2 text-sm"><span>{p.fitting_type}</span><b>{p.qty}</b></div>)}</div>}
      </div>
      <StocktakeHistory onChanged={load} />
    </div>
  )
}
