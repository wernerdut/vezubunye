import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { ErrorBox, Field, Modal, PageTitle, Spinner, useAction } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { date } from '../lib/format'
import { StocktakeHistory } from './Stocktake'

const kg = (v: number) => v ? `${Math.round(v).toLocaleString('en-ZA')} kg` : '–'

export default function Stock() {
  const { cfg } = useApp()
  const [s, setS] = useState<any>(null)
  const [regrade, setRegrade] = useState<any>(null)
  const load = () => api.get('/api/p/stock').then((r) => setS(r.data))
  useEffect(() => { load() }, [])
  if (!s) return <Spinner />
  const what = (r: any) => [tankName(cfg, r.tank_type), colourOf(cfg, r.colour)?.name || r.colour].filter(Boolean).join(' ')
  return (
    <div>
      <PageTitle title="Stock" sub="Finished tanks against what is still to deliver." />
      <div className="card mb-4 overflow-x-auto">
        <table className="min-w-full">
          <thead className="border-b bg-gray-50"><tr><th className="th">Tank</th><th className="th">Colour</th><th className="th">Grade</th><th className="th text-right">On hand</th><th className="th text-right">To deliver</th><th className="th text-right">Free</th><th className="th" /></tr></thead>
          <tbody>{s.finished_goods.map((r: any, i: number) => {
            const c = colourOf(cfg, r.colour)
            return <tr key={i} className="border-b"><td className="td">{tankName(cfg, r.tank_type)}</td>
              <td className="td">{c && <span className="mr-2 inline-block h-3 w-3 rounded-sm align-middle" style={{ background: c.swatch_hex }} />}{c?.name || r.colour || '—'}</td>
              <td className="td">{r.grade}</td><td className="td text-right tabular-nums">{r.on_hand}</td><td className="td text-right tabular-nums">{r.to_deliver || ''}</td>
              <td className={`td text-right font-semibold tabular-nums ${r.free < 0 ? 'text-red-700' : ''}`}>{r.free}</td>
              <td className="td text-right">{r.on_hand > 0 && <button className="btn-ghost btn-sm whitespace-nowrap" onClick={() => setRegrade(r)}>Change grade</button>}</td></tr>
          })}</tbody>
        </table>
      </div>
      {(s.grade_changes || []).length > 0 && <div className="card mb-4 overflow-x-auto"><h2 className="border-b p-3 font-bold">Grade changes</h2>
        <table className="min-w-full text-sm">
          <thead className="border-b bg-gray-50"><tr><th className="th">Date</th><th className="th">Tank</th><th className="th">Change</th><th className="th">Why</th><th className="th">By</th></tr></thead>
          <tbody>{s.grade_changes.map((g: any) => (
            <tr key={g.id} className="border-b"><td className="td whitespace-nowrap">{date(g.date)}</td><td className="td">{g.quantity} × {what(g)}</td>
              <td className="td whitespace-nowrap">{g.from} to {g.to}</td><td className="td">{g.reason}</td><td className="td">{g.by_name}</td></tr>))}</tbody>
        </table></div>}
      {regrade && <Regrade row={regrade} name={what(regrade)} onClose={() => setRegrade(null)} onDone={() => { setRegrade(null); load() }} />}
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

function Regrade({ row, name, onClose, onDone }: { row: any; name: string; onClose: () => void; onDone: () => void }) {
  const a = useAction()
  const to = row.grade === 'A' ? 'B' : 'A'
  const [qty, setQty] = useState('1')
  const [reason, setReason] = useState('')
  const n = Number(qty)
  const ok = Number.isInteger(n) && n >= 1 && n <= row.on_hand && reason.trim().length > 0
  const left = row.free - (Number.isInteger(n) && n > 0 ? n : 0)
  return (
    <Modal title={`Change grade: ${name}`} onClose={onClose}>
      <p className="mb-3 text-sm text-gray-600">{row.on_hand} in stock as grade {row.grade}. The tanks you change move to grade {to}.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`How many change to grade ${to}`}><input className="input" type="number" min={1} max={row.on_hand} value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
        <Field label="Why"><input className="input" placeholder={to === 'B' ? 'Scratched in the yard' : 'Repaired'} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
      {row.to_deliver > 0 && left < 0 && <p className="mt-3 text-sm text-red-700">Open orders still need {row.to_deliver} of grade {row.grade}. After this change {-left} will be short.</p>}
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!ok || a.busy} onClick={() => a.run(async () => {
          await api.post('/api/p/stock/regrade', { tank_type: row.tank_type, colour: row.colour || '', from_grade: row.grade, to_grade: to, quantity: n, reason: reason.trim() })
          onDone()
        }, errMsg)}>Change to grade {to}</button>
      </div>
    </Modal>
  )
}
