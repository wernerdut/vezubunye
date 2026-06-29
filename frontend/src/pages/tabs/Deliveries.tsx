import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileDown, Plus, Trash2 } from 'lucide-react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import type { DNLine, DeliveryNote, FGPosition } from '../../types'
import type { TabProps } from '../NodePage'

const rand = (n: number) => `R ${n.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`
const blankLine = (code: string): DNLine => ({ tank_type: code, grade: 'A', quantity: 1, unit_price: 0 })

export default function Deliveries({ nodeId, config, user }: TabProps) {
  const [notes, setNotes] = useState<DeliveryNote[]>([])
  const [stock, setStock] = useState<FGPosition[]>([])
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10), client_name: '', client_details: '' })
  const [lines, setLines] = useState<DNLine[]>([blankLine(config.tank_types[0]?.code || '')])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const canCreate = user.role === 'operations' || user.role === 'admin'
  const names = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))
  const vatRate = config.vat_rate ?? 15

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/delivery-notes`).then((r) => setNotes(r.data))
    api.get(`/api/nodes/${nodeId}/finished-goods`).then((r) => setStock(r.data.positions))
  }, [nodeId])
  useEffect(load, [load])

  const onHand = (tt: string, gr: string) => stock.find((s) => s.tank_type === tt && s.grade === gr)?.total ?? 0
  const subtotal = useMemo(() => lines.reduce((s, l) => s + l.quantity * l.unit_price, 0), [lines])
  const vat = subtotal * vatRate / 100
  const total = subtotal + vat
  const setLine = (i: number, patch: Partial<DNLine>) => setLines(lines.map((x, j) => j === i ? { ...x, ...patch } : x))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/delivery-notes`, { ...form, lines })
      setForm({ ...form, client_name: '', client_details: '' })
      setLines([blankLine(config.tank_types[0]?.code || '')])
      load()
    } catch (err) {
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid lg:grid-cols-2 gap-6">
      {canCreate && (
        <div>
          <SectionTitle>New Delivery</SectionTitle>
          <p className="text-sm text-gray-500 mb-3">A delivery takes the tanks out of stock and records the price. The printed note shows no prices; the value here is what you reconcile against the bank.</p>
          <form onSubmit={submit} className="card space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
                <input className="input" type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} required />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Client</label>
                <input className="input" value={form.client_name} onChange={(e) => setForm({ ...form, client_name: e.target.value })} required />
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Client details (address, contact)</label>
              <textarea className="input" rows={2} value={form.client_details} onChange={(e) => setForm({ ...form, client_details: e.target.value })} />
            </div>
            <table className="w-full text-sm">
              <thead><tr><th className="th">Tank</th><th className="th">Grade</th><th className="th">Qty</th><th className="th text-right">In stock</th><th className="th">Unit price</th><th className="th"></th></tr></thead>
              <tbody>
                {lines.map((l, i) => {
                  const avail = onHand(l.tank_type, l.grade)
                  const short = l.quantity > avail
                  return (
                    <tr key={i}>
                      <td className="td">
                        <select className="input w-28" value={l.tank_type} onChange={(e) => setLine(i, { tank_type: e.target.value })}>
                          {config.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
                        </select>
                      </td>
                      <td className="td">
                        <select className="input w-14" value={l.grade} onChange={(e) => setLine(i, { grade: e.target.value as 'A' | 'B' })}>
                          <option value="A">A</option><option value="B">B</option>
                        </select>
                      </td>
                      <td className="td"><input className="input w-16" type="number" min="1" value={l.quantity || ''} onChange={(e) => setLine(i, { quantity: parseInt(e.target.value) || 0 })} /></td>
                      <td className={`td text-right ${short ? 'text-brand-red font-semibold' : 'text-gray-400'}`}>{avail}</td>
                      <td className="td"><input className="input w-24" type="number" min="0" step="0.01" value={l.unit_price || ''} onChange={(e) => setLine(i, { unit_price: parseFloat(e.target.value) || 0 })} /></td>
                      <td className="td">{lines.length > 1 && <button type="button" className="text-brand-red" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={14} /></button>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <button type="button" className="btn-secondary flex items-center gap-1" onClick={() => setLines([...lines, blankLine(config.tank_types[0]?.code || '')])}>
              <Plus size={14} /> Line
            </button>
            <div className="text-sm border-t border-gray-100 pt-2 space-y-1">
              <div className="flex justify-between text-gray-500"><span>Subtotal (ex VAT)</span><span>{rand(subtotal)}</span></div>
              <div className="flex justify-between text-gray-500"><span>VAT ({vatRate}%)</span><span>{rand(vat)}</span></div>
              <div className="flex justify-between font-bold text-brand-blue"><span>Expected in bank</span><span>{rand(total)}</span></div>
            </div>
            {error && <p className="text-sm text-brand-red">{error}</p>}
            <button className="btn-primary w-full" disabled={busy}>{busy ? 'Creating…' : 'Create delivery'}</button>
          </form>
        </div>
      )}
      <div className={canCreate ? '' : 'lg:col-span-2'}>
        <SectionTitle>Deliveries</SectionTitle>
        <div className="card p-0 overflow-x-auto">
          {notes.length === 0 ? (
            <Empty text="No deliveries yet" />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Number</th>
                  <th className="th">Date</th>
                  <th className="th">Client</th>
                  <th className="th">Tanks</th>
                  <th className="th text-right">Total</th>
                  <th className="th text-right">Paid</th>
                  <th className="th">Status</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {notes.map((n) => (
                  <tr key={n._id}>
                    <td className="td font-semibold whitespace-nowrap">{n.dn_number}</td>
                    <td className="td whitespace-nowrap">{n.date}</td>
                    <td className="td">{n.client_name}</td>
                    <td className="td text-gray-500">{n.lines.map((l) => `${l.quantity}× ${names[l.tank_type] || l.tank_type} (${l.grade})`).join(', ')}</td>
                    <td className="td text-right font-semibold">{rand(n.total ?? 0)}</td>
                    <td className="td text-right text-gray-500">{rand(n.amount_paid ?? 0)}</td>
                    <td className="td"><StatusBadge status={n.status || 'unpaid'} /></td>
                    <td className="td"><button className="text-brand-light" title="Delivery note PDF (no prices)" onClick={() => openAuthed(`/api/delivery-notes/${n._id}/pdf`)}><FileDown size={16} /></button></td>
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
