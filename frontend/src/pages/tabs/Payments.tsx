import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import type { Invoice, Payment } from '../../types'
import type { TabProps } from '../NodePage'

export default function Payments({ nodeId, user }: TabProps) {
  const [payments, setPayments] = useState<Payment[]>([])
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10), amount: '', bank_reference: '' })
  const [matchSel, setMatchSel] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  const canMatch = user.role === 'audit' || user.role === 'admin'
  const invByid = Object.fromEntries(invoices.map((i) => [i._id, i]))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/payments`).then((r) => setPayments(r.data))
    api.get(`/api/nodes/${nodeId}/invoices`).then((r) => setInvoices(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const record = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/payments`, { ...form, amount: parseFloat(form.amount) })
      setForm({ ...form, amount: '', bank_reference: '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  const match = async (paymentId: string) => {
    setError('')
    try {
      await api.post(`/api/payments/${paymentId}/match`, { invoice_id: matchSel[paymentId] })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div>
      <SectionTitle>Payments</SectionTitle>
      {canMatch ? (
        <form onSubmit={record} className="card mb-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Date</label>
            <input className="input" type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Amount (R)</label>
            <input className="input w-32" type="number" step="0.01" min="0" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} required />
          </div>
          <div className="flex-1 min-w-48">
            <label className="block text-xs font-semibold text-gray-600 mb-1">Bank reference</label>
            <input className="input" value={form.bank_reference} onChange={(e) => setForm({ ...form, bank_reference: e.target.value })} />
          </div>
          <button className="btn-primary">Record payment</button>
        </form>
      ) : (
        <p className="text-sm text-gray-500 mb-4">Payment recording and matching is the audit role's job. You can see statuses here.</p>
      )}
      {error && <p className="text-sm text-brand-red mb-3">{error}</p>}
      <div className="card p-0 overflow-hidden">
        {payments.length === 0 ? (
          <Empty text="No payments recorded" />
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Date</th>
                <th className="th text-right">Amount</th>
                <th className="th">Reference</th>
                <th className="th">Status</th>
                <th className="th">Invoice</th>
                {canMatch && <th className="th text-right">Fenix ex-works</th>}
                {canMatch && <th className="th text-right">Partner balance</th>}
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p._id}>
                  <td className="td">{p.date}</td>
                  <td className="td text-right font-semibold">R {p.amount.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</td>
                  <td className="td text-gray-500">{p.bank_reference}</td>
                  <td className="td"><StatusBadge status={p.status} /></td>
                  <td className="td">
                    {p.matched_invoice_id ? (
                      <span className="font-semibold">{invByid[p.matched_invoice_id]?.invoice_number || '—'}</span>
                    ) : canMatch ? (
                      <span className="flex items-center gap-1">
                        <select className="input py-1" value={matchSel[p._id] || ''} onChange={(e) => setMatchSel({ ...matchSel, [p._id]: e.target.value })}>
                          <option value="">select…</option>
                          {invoices.filter((i) => i.status !== 'paid').map((i) => (
                            <option key={i._id} value={i._id}>{i.invoice_number} (R {i.total.toFixed(2)})</option>
                          ))}
                        </select>
                        <button className="btn-secondary py-1" disabled={!matchSel[p._id]} onClick={() => match(p._id)} type="button">Match</button>
                      </span>
                    ) : (
                      <span className="text-gray-300">unmatched</span>
                    )}
                  </td>
                  {canMatch && (
                    <td className="td text-right text-brand-blue font-semibold">
                      {p.split ? `R ${p.split.fenix_exworks_value.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}` : '—'}
                    </td>
                  )}
                  {canMatch && (
                    <td className="td text-right">
                      {p.split ? `R ${p.split.partner_balance.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}` : '—'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
