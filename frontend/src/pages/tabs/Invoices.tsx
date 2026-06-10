import { useCallback, useEffect, useState } from 'react'
import { FileDown, Plus, Trash2 } from 'lucide-react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import type { DeliveryNote, Invoice, InvoiceLine } from '../../types'
import type { TabProps } from '../NodePage'

export default function Invoices({ nodeId, config, user }: TabProps) {
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [dns, setDns] = useState<DeliveryNote[]>([])
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10), client_name: '', client_details: '' })
  const [lines, setLines] = useState<InvoiceLine[]>([{ tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1, unit_price: 0 }])
  const [selectedDns, setSelectedDns] = useState<string[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const canCreate = user.role === 'operations' || user.role === 'admin'

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/invoices`).then((r) => setInvoices(r.data))
    api.get(`/api/nodes/${nodeId}/delivery-notes`).then((r) => setDns(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const subtotal = lines.reduce((s, l) => s + l.quantity * l.unit_price, 0)
  const vat = subtotal * config.vat_rate / 100
  const unlinkedDns = dns.filter((d) => !d.linked_invoice_id)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/invoices`, { ...form, lines, delivery_note_ids: selectedDns })
      setForm({ ...form, client_name: '', client_details: '' })
      setLines([{ tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1, unit_price: 0 }])
      setSelectedDns([])
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
          <SectionTitle>New Invoice</SectionTitle>
          <p className="text-sm text-gray-500 mb-3">Unit price is the partner's sale price. B-grade lines carry the reduced price actually charged.</p>
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
              <label className="block text-xs font-semibold text-gray-600 mb-1">Client details (address, VAT no.)</label>
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
                  <input className="input w-16" type="number" min="1" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, quantity: parseInt(e.target.value) || 1 } : x))} />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Unit price (ex VAT)</label>
                  <input className="input w-28" type="number" step="0.01" min="0" value={l.unit_price} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, unit_price: parseFloat(e.target.value) || 0 } : x))} />
                </div>
                {lines.length > 1 && (
                  <button type="button" className="p-2 text-brand-red" onClick={() => setLines(lines.filter((_, j) => j !== i))}>
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            ))}
            <button type="button" className="btn-secondary flex items-center gap-1" onClick={() => setLines([...lines, { tank_type: config.tank_types[0]?.code || '', grade: 'A', quantity: 1, unit_price: 0 }])}>
              <Plus size={14} /> Line
            </button>
            {unlinkedDns.length > 0 && (
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Link delivery notes</label>
                <div className="space-y-1">
                  {unlinkedDns.map((d) => (
                    <label key={d._id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={selectedDns.includes(d._id)}
                        onChange={(e) => setSelectedDns(e.target.checked ? [...selectedDns, d._id] : selectedDns.filter((x) => x !== d._id))}
                      />
                      {d.dn_number} — {d.client_name} ({d.date})
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className="text-sm bg-gray-50 rounded px-3 py-2">
              Subtotal <b>R {subtotal.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</b> ·
              VAT ({config.vat_rate}%) <b>R {vat.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</b> ·
              Total <b className="text-brand-blue">R {(subtotal + vat).toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</b>
            </div>
            {error && <p className="text-sm text-brand-red">{error}</p>}
            <button className="btn-primary w-full" disabled={busy}>{busy ? 'Creating…' : 'Raise invoice'}</button>
          </form>
        </div>
      )}
      <div className={canCreate ? '' : 'lg:col-span-2'}>
        <SectionTitle>Invoices</SectionTitle>
        <div className="card p-0 overflow-hidden">
          {invoices.length === 0 ? (
            <Empty text="No invoices yet" />
          ) : (
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Number</th>
                  <th className="th">Date</th>
                  <th className="th">Client</th>
                  <th className="th text-right">Total</th>
                  <th className="th">Status</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((i) => (
                  <tr key={i._id}>
                    <td className="td font-semibold">{i.invoice_number}</td>
                    <td className="td">{i.date}</td>
                    <td className="td">{i.client_name}</td>
                    <td className="td text-right font-semibold">R {i.total.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</td>
                    <td className="td"><StatusBadge status={i.status} /></td>
                    <td className="td">
                      <button className="text-brand-light" title="PDF" onClick={() => openAuthed(`/api/invoices/${i._id}/pdf`)}>
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
