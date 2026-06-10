import { useCallback, useEffect, useState } from 'react'
import { FileDown } from 'lucide-react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, StatusBadge } from '../../components/ui'
import type { Capture, ProductionLine } from '../../types'
import type { TabProps } from '../NodePage'

export default function DailyCapture({ nodeId, config, user }: TabProps) {
  const [captures, setCaptures] = useState<Capture[]>([])
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [powderIn, setPowderIn] = useState('')
  const [powderDrawn, setPowderDrawn] = useState('')
  const [lines, setLines] = useState<ProductionLine[]>(
    config.tank_types.map((t) => ({ tank_type: t.code, quantity_a: 0, quantity_b: 0, quantity_reject: 0 })),
  )
  const [photo, setPhoto] = useState<File | null>(null)
  const [notes, setNotes] = useState('')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const canCapture = user.role === 'operations' || user.role === 'admin'

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/captures`).then((r) => setCaptures(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const impliedKg = lines.reduce((sum, l) => {
    const w = config.tank_types.find((t) => t.code === l.tank_type)?.weight_kg || 0
    return sum + (l.quantity_a + l.quantity_b + l.quantity_reject) * w
  }, 0)
  const drawn = parseFloat(powderDrawn) || 0
  const gap = drawn - impliedKg

  const setLine = (i: number, field: keyof ProductionLine, value: number) => {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, [field]: value } : l)))
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const cap = (await api.post(`/api/nodes/${nodeId}/captures?date=${date}`)).data
      if (photo) {
        const form = new FormData()
        form.append('file', photo)
        await api.post(`/api/captures/${cap._id}/photo`, form)
      }
      const r = await api.post(`/api/captures/${cap._id}/entries`, {
        powder_in_kg: parseFloat(powderIn) || 0,
        powder_drawn_kg: drawn,
        production: lines,
        notes,
      })
      if (r.data.status === 'reconciled') {
        setMsg({ kind: 'ok', text: `Captured and reconciled. Powder balances exactly.` })
      } else {
        setMsg({ kind: 'warn', text: `Captured with ${r.data.flags_raised.length} flag(s) raised. The audit role will see them.` })
      }
      setPowderIn(''); setPowderDrawn(''); setNotes(''); setPhoto(null)
      setLines(config.tank_types.map((t) => ({ tank_type: t.code, quantity_a: 0, quantity_b: 0, quantity_reject: 0 })))
      load()
    } catch (err) {
      setMsg({ kind: 'err', text: errMsg(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid lg:grid-cols-2 gap-6">
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-headline text-3xl text-brand-blue">Capture the Day</h2>
          <button className="btn-secondary flex items-center gap-2" onClick={() => openAuthed(`/api/nodes/${nodeId}/capture-sheet.pdf`)}>
            <FileDown size={16} /> Blank sheet (PDF)
          </button>
        </div>
        {canCapture ? (
          <form onSubmit={submit} className="card space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
                <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Powder in (kg)</label>
                <input className="input" type="number" step="0.1" min="0" value={powderIn} onChange={(e) => setPowderIn(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Powder drawn (kg)</label>
                <input className="input" type="number" step="0.1" min="0" value={powderDrawn} onChange={(e) => setPowderDrawn(e.target.value)} />
              </div>
            </div>
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Tank</th>
                  <th className="th">A-grade</th>
                  <th className="th">B-grade</th>
                  <th className="th">Reject</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={l.tank_type}>
                    <td className="td font-semibold">{config.tank_types[i].name}</td>
                    {(['quantity_a', 'quantity_b', 'quantity_reject'] as const).map((f) => (
                      <td className="td" key={f}>
                        <input
                          className="input w-20"
                          type="number"
                          min="0"
                          value={l[f]}
                          onChange={(e) => setLine(i, f, parseInt(e.target.value) || 0)}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className={`text-sm rounded px-3 py-2 ${Math.abs(gap) < 0.001 && drawn > 0 ? 'bg-green-50 text-brand-green' : drawn > 0 ? 'bg-red-50 text-brand-red' : 'bg-gray-50 text-gray-500'}`}>
              Implied powder: <b>{impliedKg.toFixed(1)} kg</b> · drawn: <b>{drawn.toFixed(1)} kg</b>
              {drawn > 0 && (Math.abs(gap) < 0.001
                ? ' · balances exactly'
                : ` · gap ${gap > 0 ? '+' : ''}${gap.toFixed(1)} kg, this will raise a flag (zero tolerance)`)}
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">WhatsApp photo of the sheet</label>
              <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] || null)} className="text-sm" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Notes</label>
              <input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            {msg && (
              <p className={`text-sm font-semibold ${msg.kind === 'ok' ? 'text-brand-green' : msg.kind === 'warn' ? 'text-brand-orange' : 'text-brand-red'}`}>
                {msg.text}
              </p>
            )}
            <button className="btn-primary w-full" disabled={busy}>
              {busy ? 'Saving…' : 'Save capture'}
            </button>
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
              <thead>
                <tr>
                  <th className="th">Date</th>
                  <th className="th">Status</th>
                  <th className="th">By</th>
                  <th className="th">Photo</th>
                </tr>
              </thead>
              <tbody>
                {captures.map((c) => (
                  <tr key={c._id}>
                    <td className="td font-semibold">{c.date}</td>
                    <td className="td"><StatusBadge status={c.status} /></td>
                    <td className="td text-gray-500">{c.captured_by}</td>
                    <td className="td">
                      {c.photo_url ? (
                        <button
                          className="text-brand-light font-semibold"
                          onClick={() =>
                            c.photo_url!.startsWith('http')
                              ? window.open(c.photo_url!, '_blank')
                              : openAuthed(c.photo_url!)
                          }
                        >
                          view
                        </button>
                      ) : (
                        <span className="text-gray-300">none</span>
                      )}
                    </td>
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
