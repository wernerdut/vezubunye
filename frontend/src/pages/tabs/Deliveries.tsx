import { useCallback, useEffect, useState } from 'react'
import { FileDown, Plus, Trash2 } from 'lucide-react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { DNLine, DeliveryNote } from '../../types'
import type { TabProps } from '../NodePage'

export default function Deliveries({ nodeId, config, user }: TabProps) {
  const [notes, setNotes] = useState<DeliveryNote[]>([])
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10), client_name: '', client_details: '' })
  const [lines, setLines] = useState<DNLine[]>([{ tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1 }])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const canCreate = user.role === 'operations' || user.role === 'admin'
  const names = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/delivery-notes`).then((r) => setNotes(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/delivery-notes`, { ...form, lines })
      setForm({ ...form, client_name: '', client_details: '' })
      setLines([{ tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1 }])
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
          <SectionTitle>New Delivery Note</SectionTitle>
          <p className="text-sm text-gray-500 mb-3">No tank leaves without a delivery note and an invoice. B-grade tanks carry their grade.</p>
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
            {lines.map((l, i) => (
              <div key={i} className="flex items-end gap-2">
                <div className="flex-1">
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Tank</label>
                  <select className="input" value={l.tank_type} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, tank_type: e.target.value } : x))}>
                    {config.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Grade</label>
                  <select className="input" value={l.grade} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, grade: e.target.value as 'A' | 'B' } : x))}>
                    <option value="A">A</option>
                    <option value="B">B</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Qty</label>
                  <input className="input w-20" type="number" min="1" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, quantity: parseInt(e.target.value) || 1 } : x))} />
                </div>
                {lines.length > 1 && (
                  <button type="button" className="p-2 text-brand-red" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            ))}
            <button type="button" className="btn-secondary flex items-center gap-1" onClick={() => setLines([...lines, { tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1 }])}>
              <Plus size={14} /> Line
            </button>
            {error && <p className="text-sm text-brand-red">{error}</p>}
            <button className="btn-primary w-full" disabled={busy}>{busy ? 'Creating…' : 'Create delivery note'}</button>
          </form>
        </div>
      )}
      <div className={canCreate ? '' : 'lg:col-span-2'}>
        <SectionTitle>Delivery Notes</SectionTitle>
        <div className="card p-0 overflow-hidden">
          {notes.length === 0 ? (
            <Empty text="No delivery notes yet" />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Number</th>
                  <th className="th">Date</th>
                  <th className="th">Client</th>
                  <th className="th">Lines</th>
                  <th className="th">Invoice</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {notes.map((n) => (
                  <tr key={n._id}>
                    <td className="td font-semibold">{n.dn_number}</td>
                    <td className="td">{n.date}</td>
                    <td className="td">{n.client_name}</td>
                    <td className="td text-gray-500">
                      {n.lines.map((l) => `${l.quantity}× ${names[l.tank_type] || l.tank_type} (${l.grade})`).join(', ')}
                    </td>
                    <td className="td">
                      {n.linked_invoice_id
                        ? <span className="text-brand-green font-semibold">linked</span>
                        : <span className="text-brand-red font-semibold">none</span>}
                    </td>
                    <td className="td">
                      <button className="text-brand-light" title="PDF" onClick={() => openAuthed(`/api/delivery-notes/${n._id}/pdf`)}>
                        <FileDown size={16} />
                      </button>
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
