import { FileDown, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, errMsg, openPdf } from '../api'
import { Empty, ErrorBox, Field, PageTitle, Spinner, Status, useAction } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { date, today } from '../lib/format'

interface ProdLine { tank_type: string; tank_colour: string; quantity_a: number; quantity_b: number; quantity_reject: number }
interface PowderLine { powder_type: string; received_kg: number; issued_kg: number }

/** Daily production capture: one sheet per day, colour on every line. */
export default function Production() {
  const { cfg } = useApp()
  const [list, setList] = useState<any[] | null>(null)
  const [d, setD] = useState(today())
  const [prod, setProd] = useState<ProdLine[]>([])
  const [powder, setPowder] = useState<PowderLine[]>([])
  const [notes, setNotes] = useState('')
  const [reason, setReason] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const a = useAction()
  const load = () => api.get('/api/p/production/captures').then((r) => setList(r.data))
  useEffect(() => { load() }, [])
  const existing = list?.find((c) => c.date === d && c.status === 'captured')
  useEffect(() => {
    const e = existing?.entries
    setProd(e?.production || [])
    setPowder(e?.powder || [])
    setNotes(e?.notes || '')
  }, [d, existing?._id]) // eslint-disable-line
  const capturable = cfg.colours.filter((c) => c.powder_product)
  const save = () => a.run(async () => {
    const cap = (await api.post('/api/p/production/captures', null, { params: { date: d } })).data
    await api.post(`/api/p/production/captures/${cap._id}/entries`, { production: prod, powder, notes, reason: existing ? reason : undefined })
    if (photo) { const fd = new FormData(); fd.append('file', photo); await api.post(`/api/p/production/captures/${cap._id}/photo`, fd) }
    setMsg(`Captured ${date(d)}.`)
    load()
  }, errMsg)
  return (
    <div>
      <PageTitle title="Production" actions={<button className="btn-ghost" onClick={() => openPdf('/api/p/production/capture-sheet.pdf')}><FileDown size={16} /> Blank sheet</button>} />
      <div className="card mb-4 space-y-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Day"><input type="date" className="input" value={d} max={today()} onChange={(e) => { setD(e.target.value); setMsg(null) }} /></Field>
          {existing && <p className="text-sm text-amber-700">Already captured. Saving again is a same-day correction and needs a reason.</p>}
        </div>
        <div>
          <div className="label">Tanks moulded</div>
          {prod.map((l, i) => (
            <div key={i} className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-12">
              <select className="input col-span-2 sm:col-span-3" value={l.tank_type} onChange={(e) => setProd(prod.map((x, j) => j === i ? { ...x, tank_type: e.target.value } : x))}>
                {cfg.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}</select>
              <select className="input col-span-2 sm:col-span-3" value={l.tank_colour} onChange={(e) => setProd(prod.map((x, j) => j === i ? { ...x, tank_colour: e.target.value } : x))}>
                <option value="">Colour…</option>{capturable.filter((c) => { const t = cfg.tank_types.find((x) => x.code === l.tank_type); return !t?.allowed_colours?.length || t.allowed_colours.includes(c.code) }).map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
              {(['quantity_a', 'quantity_b', 'quantity_reject'] as const).map((k) => (
                <input key={k} type="number" min={0} className="input sm:col-span-2" placeholder={k === 'quantity_a' ? 'A' : k === 'quantity_b' ? 'B' : 'Reject'} value={l[k] || ''}
                  onChange={(e) => setProd(prod.map((x, j) => j === i ? { ...x, [k]: Number(e.target.value) } : x))} />
              ))}
              <button className="text-gray-400" onClick={() => setProd(prod.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
            </div>
          ))}
          <button className="btn-ghost btn-sm" onClick={() => setProd([...prod, { tank_type: cfg.tank_types[0].code, tank_colour: '', quantity_a: 0, quantity_b: 0, quantity_reject: 0 }])}><Plus size={14} /> Line</button>
        </div>
        <div>
          <div className="label">Powder (kg)</div>
          {powder.map((l, i) => (
            <div key={i} className="mb-2 grid grid-cols-3 gap-2">
              <select className="input" value={l.powder_type} onChange={(e) => setPowder(powder.map((x, j) => j === i ? { ...x, powder_type: e.target.value } : x))}>
                {cfg.powder_products.map((p) => <option key={p.code} value={p.code}>{p.code}</option>)}</select>
              <input type="number" className="input" placeholder="Received" value={l.received_kg || ''} onChange={(e) => setPowder(powder.map((x, j) => j === i ? { ...x, received_kg: Number(e.target.value) } : x))} />
              <input type="number" className="input" placeholder="Issued" value={l.issued_kg || ''} onChange={(e) => setPowder(powder.map((x, j) => j === i ? { ...x, issued_kg: Number(e.target.value) } : x))} />
            </div>
          ))}
          <button className="btn-ghost btn-sm" onClick={() => setPowder([...powder, { powder_type: cfg.powder_products[0]?.code, received_kg: 0, issued_kg: 0 }])}><Plus size={14} /> Powder</button>
        </div>
        <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <Field label="Photo of the sheet"><input type="file" accept="image/*" capture="environment" className="input" onChange={(e) => setPhoto(e.target.files?.[0] || null)} /></Field>
        {existing && <Field label="Reason for the correction"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
        <ErrorBox msg={a.error} />{msg && <p className="text-sm text-emerald-700">{msg}</p>}
        <div className="flex justify-end"><button className="btn-primary" disabled={a.busy || (!!existing && !reason.trim())} onClick={save}>Save capture</button></div>
      </div>
      <div className="card divide-y">
        {list === null ? <Spinner /> : list.length === 0 ? <Empty text="No captures yet." /> : list.slice(0, 30).map((c) => {
          const lines = c.entries?.production || []
          return <button key={c._id} onClick={() => setD(c.date)} className="flex w-full items-center justify-between gap-2 p-3 text-left text-sm hover:bg-gray-50">
            <span><b>{date(c.date)}</b> · {lines.map((l: any) => `${(l.quantity_a || 0) + (l.quantity_b || 0)} × ${tankName(cfg, l.tank_type)} ${colourOf(cfg, l.tank_colour)?.name || ''}`).join(', ') || 'nothing moulded'}</span><Status s={c.status === 'captured' ? 'complete' : 'draft'} /></button>
        })}
      </div>
    </div>
  )
}
