import { Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { ErrorBox, Field, Modal, Status, useAction } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { date as fmtDate, today } from '../lib/format'

type TankLine = { tank_type: string; colour: string; grade: 'A' | 'B'; store: string; floor: string }

const KIND: Record<string, string> = {
  tank_store: 'tanks', tank_floor: 'on the floor', powder_stock: 'kg', powder_warehouse: 'kg in the store',
  powder_floor: 'kg on the floor', fittings: 'fittings',
}

/** Stocktakes for this business: counted here (blind: no system figures), signed off by Fenix
 * before stock changes. The first one signed off is the opening stock. */
export function StocktakeHistory({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<any>(null)
  const [counting, setCounting] = useState(false)
  const a = useAction()
  const load = useCallback(() => api.get('/api/p/stocktakes').then((r) => setData(r.data)), [])
  useEffect(() => { load() }, [load])
  if (!data) return null
  const waiting = data.stocktakes.find((s: any) => s.status === 'submitted')
  return (
    <div className="card mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
        <div>
          <h2 className="font-bold">Stocktakes</h2>
          <p className="text-xs text-gray-500">{data.positions.opening ? 'No stock has been counted in yet. The first stocktake signed off by Fenix becomes the opening stock.' : 'Count what is physically here. Stock changes once Fenix signs the count off.'}</p>
        </div>
        {!waiting && <button className="btn-primary" onClick={() => setCounting(true)}>{data.positions.opening ? 'Count opening stock' : 'New stocktake'}</button>}
      </div>
      <ErrorBox msg={a.error} />
      {data.stocktakes.length === 0 && <p className="p-3 text-sm text-gray-400">No stocktakes yet.</p>}
      {data.stocktakes.map((s: any) => (
        <div key={s._id} className="border-b p-3 text-sm last:border-b-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-semibold">{fmtDate(s.date)}{s.opening ? ' · opening stock' : ''} · {s.submitted_by_name}</span>
            <span className="flex items-center gap-2">
              <Status s={s.status === 'submitted' ? 'waiting_for_sign_off' : s.status} />
              {s.status === 'submitted' && <button className="btn-ghost btn-sm" disabled={a.busy}
                onClick={() => a.run(async () => { await api.post(`/api/p/stocktakes/${s._id}/withdraw`); load() }, errMsg)}>Withdraw</button>}
            </span>
          </div>
          {s.status === 'returned' && <p className="mt-1 text-amber-800">Sent back: {s.review?.note}</p>}
          {s.status === 'approved' && (s.changes?.length
            ? <ul className="mt-1 list-disc pl-5 text-gray-700">{s.changes.map((c: any, i: number) => (
                <li key={i}>{c.label} {KIND[c.kind] || ''}: {c.counted} counted ({c.variance > 0 ? '+' : ''}{c.variance})</li>))}</ul>
            : <p className="mt-1 text-green-700">Matched the system exactly.</p>)}
        </div>
      ))}
      {counting && <CountForm positions={data.positions} onClose={() => setCounting(false)} onSaved={() => { setCounting(false); load(); onChanged?.() }} />}
    </div>
  )
}

function CountForm({ positions, onClose, onSaved }: { positions: any; onClose: () => void; onSaved: () => void }) {
  const { cfg } = useApp()
  const [day, setDay] = useState(today())
  const [tanks, setTanks] = useState<TankLine[]>(
    positions.tanks.length
      ? positions.tanks.map((t: any) => ({ ...t, store: '', floor: '' }))
      : [{ tank_type: cfg.tank_types[0]?.code || '', colour: '', grade: 'A', store: '', floor: '' }])
  const [powder, setPowder] = useState<Record<string, string>>({})
  const [fits, setFits] = useState<Record<string, string>>({})
  const [notes, setNotes] = useState('')
  const a = useAction()
  const colours = (code: string) => {
    const allowed = cfg.tank_types.find((t) => t.code === code)?.allowed_colours
    return (cfg.colours || []).filter((c) => !allowed || allowed.includes(c.code))
  }
  const set = (i: number, patch: Partial<TankLine>) => setTanks(tanks.map((t, j) => (j === i ? { ...t, ...patch } : t)))
  const save = () => a.run(async () => {
    await api.post('/api/p/stocktakes', {
      date: day, notes,
      tanks: tanks.filter((t) => t.tank_type && Number(t.store))
        .map((t) => ({ tank_type: t.tank_type, colour: t.colour, grade: t.grade, store: Number(t.store) || 0, floor: 0 })),
      powder: positions.powder.map((p: string) => ({ powder_type: p, kg: Number(powder[p]) || 0 }))
        .filter((p: any) => p.kg),
      fittings: positions.fittings.map((f: string) => ({ fitting_type: f, qty: Number(fits[f]) || 0 })).filter((f: any) => f.qty),
    })
    onSaved()
  }, errMsg)
  const powderName = (code: string) => cfg.powder_products.find((p) => p.code === code)?.colour || code
  return (
    <Modal title={positions.opening ? 'Opening stock count' : 'Stocktake'} onClose={onClose} wide>
      <p className="mb-3 text-sm text-gray-600">Count what is physically here. Anything you leave out is counted as zero. Fenix checks the count before stock changes.</p>
      <Field label="Count date"><input type="date" className="input w-44" value={day} max={today()} onChange={(e) => setDay(e.target.value)} /></Field>

      <h3 className="mb-1 mt-4 text-sm font-bold">Tanks</h3>
      <p className="mb-2 text-xs text-gray-500">Every finished tank on site, wherever it stands. These are the tanks you can deliver.</p>
      <div className="space-y-2">
        {tanks.map((t, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <select className="input w-40" value={t.tank_type} onChange={(e) => set(i, { tank_type: e.target.value, colour: '' })}>
              {cfg.tank_types.map((tt) => <option key={tt.code} value={tt.code}>{tankName(cfg, tt.code)}</option>)}
            </select>
            {cfg.track_colour_stock && <select className="input w-40" value={t.colour} onChange={(e) => set(i, { colour: e.target.value })}>
              <option value="">Colour…</option>
              {colours(t.tank_type).map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>}
            {t.colour && <span className="inline-block h-4 w-4 rounded-sm" style={{ background: colourOf(cfg, t.colour)?.swatch_hex }} />}
            <select className="input w-20" value={t.grade} onChange={(e) => set(i, { grade: e.target.value as 'A' | 'B' })}><option>A</option><option>B</option></select>
            <input className="input w-24" type="number" min="0" placeholder="Count" value={t.store} onChange={(e) => set(i, { store: e.target.value })} />
            <button className="btn-ghost btn-sm" onClick={() => setTanks(tanks.filter((_, j) => j !== i))} aria-label="Remove line"><Trash2 size={14} /></button>
          </div>
        ))}
      </div>
      <button className="btn-ghost btn-sm mt-2" onClick={() => setTanks([...tanks, { tank_type: cfg.tank_types[0]?.code || '', colour: '', grade: 'A', store: '', floor: '' }])}><Plus size={14} /> Add tank line</button>

      {positions.powder.length > 0 && <>
        <h3 className="mb-1 mt-5 text-sm font-bold">Powder (kg)</h3>
        <div className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
          {positions.powder.map((p: string) => (
            <div key={p} className="flex items-center gap-2 text-sm">
              <span className="w-36 truncate" title={p}>{powderName(p)}</span>
              <input className="input w-28" type="number" min="0" step="0.1" placeholder="kg" value={powder[p] || ''} onChange={(e) => setPowder({ ...powder, [p]: e.target.value })} />
            </div>
          ))}
        </div>
      </>}

      {positions.fittings.length > 0 && <>
        <h3 className="mb-1 mt-5 text-sm font-bold">Fittings</h3>
        <div className="grid gap-2 sm:grid-cols-2">
          {positions.fittings.map((f: string) => (
            <div key={f} className="flex items-center gap-2 text-sm"><span className="w-36">{f}</span>
              <input className="input w-24" type="number" min="0" value={fits[f] || ''} onChange={(e) => setFits({ ...fits, [f]: e.target.value })} /></div>
          ))}
        </div>
      </>}

      <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Back</button>
        <button className="btn-primary" disabled={a.busy} onClick={save}>Send to Fenix for sign-off</button>
      </div>
    </Modal>
  )
}
