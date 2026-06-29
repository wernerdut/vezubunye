import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileDown } from 'lucide-react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, StatusBadge } from '../../components/ui'
import type { Capture, FittingMoveLine, PowderMoveLine, ProductionLine } from '../../types'
import type { TabProps } from '../NodePage'

export default function DailyCapture({ nodeId, config, user }: TabProps) {
  const [captures, setCaptures] = useState<Capture[]>([])
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [photo, setPhoto] = useState<File | null>(null)
  const [notes, setNotes] = useState('')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const canCapture = user.role === 'operations' || user.role === 'admin'

  const blankPowder = (): PowderMoveLine[] =>
    config.powder_products.length
      ? config.powder_products.map((p) => ({ powder_type: p.code, received_kg: 0, issued_kg: 0 }))
      : [{ powder_type: '', received_kg: 0, issued_kg: 0 }]
  const blankFittings = (): FittingMoveLine[] =>
    config.fitting_types.map((f) => ({ fitting_type: f.code, received_qty: 0, issued_qty: 0 }))
  const colours = config.powder_products.filter((p) => !p.is_black)
  const blackCode = config.powder_products.find((p) => p.is_black)?.code || ''
  const blankProd = (): ProductionLine[] =>
    config.tank_types.map((t) => ({ tank_type: t.code, colour: colours[0]?.code || '', quantity_a: 0, quantity_b: 0, quantity_reject: 0 }))

  const [powder, setPowder] = useState<PowderMoveLine[]>(blankPowder)
  const [fittings, setFittings] = useState<FittingMoveLine[]>(blankFittings)
  const [prod, setProd] = useState<ProductionLine[]>(blankProd)
  const [paraffin, setParaffin] = useState('')

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/captures`).then((r) => setCaptures(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const tankByCode = useMemo(() => Object.fromEntries(config.tank_types.map((t) => [t.code, t])), [config])
  const colourName = (code: string) => config.powder_products.find((p) => p.code === code)?.colour || code

  // live floor change this capture, per powder grade (issued − consumed). Each tank draws
  // W/2 of its colour + W/2 + lid of black.
  const floorDelta = useMemo(() => {
    const d: Record<string, number> = {}
    powder.forEach((p) => { if (p.powder_type) d[p.powder_type] = (d[p.powder_type] || 0) + p.issued_kg })
    prod.forEach((l) => {
      const t = tankByCode[l.tank_type]
      if (!t) return
      const n = l.quantity_a + l.quantity_b + l.quantity_reject
      if (blackCode) d[blackCode] = (d[blackCode] || 0) - n * (t.weight_kg / 2 + t.lid_weight_kg)
      if (l.colour) d[l.colour] = (d[l.colour] || 0) - n * (t.weight_kg / 2)
    })
    return d
  }, [powder, prod, tankByCode, blackCode])
  const negativeGrades = Object.entries(floorDelta).filter(([, v]) => v < -0.001)
  const tanksThisCapture = prod.reduce((s, l) => s + l.quantity_a + l.quantity_b + l.quantity_reject, 0)

  const reset = () => {
    setPowder(blankPowder()); setFittings(blankFittings()); setProd(blankProd())
    setNotes(''); setPhoto(null); setParaffin('')
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg(null)
    try {
      const cap = (await api.post(`/api/nodes/${nodeId}/captures?date=${date}`)).data
      if (photo) {
        const form = new FormData(); form.append('file', photo)
        await api.post(`/api/captures/${cap._id}/photo`, form)
      }
      await api.post(`/api/captures/${cap._id}/entries`, {
        powder: powder.filter((p) => p.powder_type),
        fittings, production: prod,
        paraffin_received: parseFloat(paraffin) || 0,
        notes,
      })
      setMsg({ kind: 'ok', text: 'Captured. Produced tanks are in stock. Reconciliation happens at stocktake.' })
      reset(); load()
    } catch (err) {
      setMsg({ kind: 'err', text: errMsg(err) })
    } finally {
      setBusy(false)
    }
  }

  const numCell = (val: number, on: (n: number) => void, step = '1') => (
    <input className="input w-20" type="number" min="0" step={step}
           value={val || ''} onChange={(e) => on(parseFloat(e.target.value) || 0)} />
  )

  return (
    <div className="grid xl:grid-cols-2 gap-6">
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-headline text-3xl text-brand-blue">Capture the Day</h2>
          <button className="btn-secondary flex items-center gap-2" onClick={() => openAuthed(`/api/nodes/${nodeId}/capture-sheet.pdf`)}>
            <FileDown size={16} /> Blank sheet (PDF)
          </button>
        </div>
        {canCapture ? (
          <form onSubmit={submit} className="card space-y-5">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
              <input className="input w-48" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>

            {/* Powder — one line per grade received and/or issued */}
            <section>
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-sm font-bold text-brand-blue">Powder — received &amp; issued by grade</h3>
                <button type="button" className="text-xs font-semibold text-brand-blue"
                        onClick={() => setPowder((ls) => [...ls, { powder_type: '', received_kg: 0, issued_kg: 0 }])}>
                  + add powder grade
                </button>
              </div>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Powder grade</th><th className="th">Received from Fenix (kg)</th><th className="th">Issued to production (kg)</th><th className="th"></th></tr></thead>
                <tbody>
                  {powder.map((p, i) => (
                    <tr key={i}>
                      <td className="td">
                        {config.powder_products.length ? (
                          <select className="input w-40" value={p.powder_type}
                                  onChange={(e) => setPowder((ls) => ls.map((l, j) => j === i ? { ...l, powder_type: e.target.value } : l))}>
                            <option value="">select grade…</option>
                            {config.powder_products.map((x) => <option key={x.code} value={x.code}>{x.colour}</option>)}
                          </select>
                        ) : (
                          <input className="input w-40" placeholder="grade name" value={p.powder_type}
                                 onChange={(e) => setPowder((ls) => ls.map((l, j) => j === i ? { ...l, powder_type: e.target.value } : l))} />
                        )}
                      </td>
                      <td className="td">{numCell(p.received_kg, (n) => setPowder((ls) => ls.map((l, j) => j === i ? { ...l, received_kg: n } : l)), '0.1')}</td>
                      <td className="td">{numCell(p.issued_kg, (n) => setPowder((ls) => ls.map((l, j) => j === i ? { ...l, issued_kg: n } : l)), '0.1')}</td>
                      <td className="td">{powder.length > 1 && <button type="button" className="text-xs text-brand-red" onClick={() => setPowder((ls) => ls.filter((_, j) => j !== i))}>×</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {config.powder_products.length <= 1 && (
                <p className="text-xs text-gray-400 mt-1">Add your colour grades (e.g. 6840 Emerald Green) in Admin → Config → Powder products, then pick them here and on moulded lines.</p>
              )}
              <div className={`text-xs mt-1 rounded px-2 py-1 ${negativeGrades.length ? 'bg-red-50 text-brand-red' : 'bg-gray-50 text-gray-500'}`}>
                Floor change this capture: {Object.entries(floorDelta).filter(([, v]) => Math.abs(v) > 0.001).map(([code, v]) => `${colourName(code)} ${v >= 0 ? '+' : ''}${v.toFixed(1)}kg`).join(', ') || 'none'}
                {negativeGrades.length > 0 && ` · more moulded than issued (${negativeGrades.map(([c]) => colourName(c)).join(', ')}) — double-check powder issued`}
              </div>
            </section>

            {/* Paraffin (release agent) — received here, drawn down per tank moulded */}
            <section>
              <h3 className="text-sm font-bold text-brand-blue mb-1">Paraffin</h3>
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Received today (litres)</label>
                  <input className="input w-32" type="number" min="0" step="0.1" value={paraffin}
                         onChange={(e) => setParaffin(e.target.value)} placeholder="0" />
                </div>
                <div className="text-xs text-gray-500 rounded bg-gray-50 px-2 py-1">
                  Used this capture:{' '}
                  <b>{(tanksThisCapture * (config.paraffin_litres_per_tank || 0)).toLocaleString('en-ZA', { maximumFractionDigits: 1 })} L</b>
                  {' '}({tanksThisCapture} tank{tanksThisCapture === 1 ? '' : 's'} × {config.paraffin_litres_per_tank || 0} L)
                </div>
              </div>
            </section>

            {/* Fittings */}
            {config.fitting_types.length > 0 && (
              <section>
                <h3 className="text-sm font-bold text-brand-blue mb-1">Fittings</h3>
                <table className="w-full text-sm">
                  <thead><tr><th className="th">Fitting</th><th className="th">Received (qty)</th><th className="th">Issued (qty)</th></tr></thead>
                  <tbody>
                    {fittings.map((f, i) => (
                      <tr key={f.fitting_type}>
                        <td className="td font-semibold">{config.fitting_types.find((x) => x.code === f.fitting_type)?.name || f.fitting_type}</td>
                        <td className="td">{numCell(f.received_qty, (n) => setFittings((ls) => ls.map((l, j) => j === i ? { ...l, received_qty: n } : l)))}</td>
                        <td className="td">{numCell(f.issued_qty, (n) => setFittings((ls) => ls.map((l, j) => j === i ? { ...l, issued_qty: n } : l)))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            {/* Tanks moulded — navy. Each line records the powder colour/grade used. */}
            <section>
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-sm font-bold text-brand-blue">Tanks Produced</h3>

                <button type="button" className="text-xs font-semibold text-brand-blue"
                        onClick={() => setProd((ls) => [...ls, { tank_type: config.tank_types[0]?.code || '', colour: colours[0]?.code || '', quantity_a: 0, quantity_b: 0, quantity_reject: 0 }])}>
                  + add production line
                </button>
              </div>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Tank</th><th className="th">Colour / grade</th><th className="th">A Grade</th><th className="th">B Grade</th><th className="th">Reject</th><th className="th"></th></tr></thead>
                <tbody>
                  {prod.map((l, i) => (
                    <tr key={i}>
                      <td className="td">
                        <select className="input w-24" value={l.tank_type} onChange={(e) => setProd((ls) => ls.map((x, j) => j === i ? { ...x, tank_type: e.target.value } : x))}>
                          {config.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
                        </select>
                      </td>
                      <td className="td">
                        {colours.length ? (
                          <select className="input w-32" value={l.colour} onChange={(e) => setProd((ls) => ls.map((x, j) => j === i ? { ...x, colour: e.target.value } : x))}>
                            <option value="">select…</option>
                            {colours.map((p) => <option key={p.code} value={p.code}>{p.colour}</option>)}
                          </select>
                        ) : (
                          <input className="input w-32" placeholder="colour grade" value={l.colour} onChange={(e) => setProd((ls) => ls.map((x, j) => j === i ? { ...x, colour: e.target.value } : x))} />
                        )}
                      </td>
                      {(['quantity_a', 'quantity_b', 'quantity_reject'] as const).map((f) => (
                        <td className="td" key={f}>{numCell(l[f], (n) => setProd((ls) => ls.map((x, j) => j === i ? { ...x, [f]: n } : x)))}</td>
                      ))}
                      <td className="td">{prod.length > 1 && <button type="button" className="text-xs text-brand-red" onClick={() => setProd((ls) => ls.filter((_, j) => j !== i))}>×</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>


            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">WhatsApp photo of the sheet</label>
              <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] || null)} className="text-sm" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Production notes — what happened on the floor today?</label>
              <textarea className="input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                        placeholder="Breakdowns, delays, quality or material issues, machine downtime, staff — anything worth flagging. Optional." />
            </div>
            {msg && (
              <p className={`text-sm font-semibold ${msg.kind === 'ok' ? 'text-brand-green' : msg.kind === 'warn' ? 'text-brand-orange' : 'text-brand-red'}`}>{msg.text}</p>
            )}
            <button className="btn-primary w-full" disabled={busy}>{busy ? 'Saving…' : 'Save capture'}</button>
          </form>
        ) : (
          <p className="text-sm text-gray-500 card">Only the operations role keys in daily sheets. You can review captures and the documents they produced.</p>
        )}
      </div>

      <div>
        <h2 className="font-headline text-3xl text-brand-blue mb-3">Recent Captures</h2>
        <div className="card p-0 overflow-hidden">
          {captures.length === 0 ? (
            <Empty text="No captures yet" />
          ) : (
            <table className="w-full">
              <thead><tr><th className="th">Date</th><th className="th">Status</th><th className="th">By</th><th className="th">Production notes</th><th className="th">Photo</th></tr></thead>
              <tbody>
                {captures.map((c) => (
                  <tr key={c._id} className="align-top">
                    <td className="td font-semibold whitespace-nowrap">{c.date}</td>
                    <td className="td"><StatusBadge status={c.status} /></td>
                    <td className="td text-gray-500">{c.captured_by}</td>
                    <td className="td text-gray-600 max-w-xs whitespace-pre-wrap break-words">
                      {c.entries?.notes ? c.entries.notes : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="td">
                      {c.photo_url ? (
                        <button className="text-brand-light font-semibold"
                                onClick={() => c.photo_url!.startsWith('http') ? window.open(c.photo_url!, '_blank') : openAuthed(c.photo_url!)}>
                          view
                        </button>
                      ) : <span className="text-gray-300">none</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="mt-4 text-xs text-gray-500">
          Produced tanks go straight into stock. Tank dispatch happens on the Deliveries tab.
        </div>
      </div>
    </div>
  )
}
