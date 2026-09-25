import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import { RowMenu, ShowVoided, canCorrect, useCorrection, voidRow, voidedQuery } from '../../components/corrections'
import type { RowAction } from '../../components/corrections'
import type { DeliveryNote, Payment } from '../../types'
import type { TabProps } from '../NodePage'

export default function Payments({ nodeId, user }: TabProps) {
  const [payments, setPayments] = useState<Payment[]>([])
  const [deliveries, setDeliveries] = useState<DeliveryNote[]>([])
  const [form, setForm] = useState({ date: new Date().toISOString().slice(0, 10), amount: '', bank_reference: '' })
  const [matchSel, setMatchSel] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [showVoided, setShowVoided] = useState(false)

  const canMatch = user.role === 'audit' || user.role === 'admin'
  const canFix = canCorrect(user, 'audit')
  const dnById = Object.fromEntries(deliveries.map((d) => [d._id, d]))

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/payments${voidedQuery(showVoided)}`).then((r) => setPayments(r.data))
    // voided notes too, so a payment's delivery number always resolves
    api.get(`/api/nodes/${nodeId}/delivery-notes?include_voided=true`).then((r) => setDeliveries(r.data))
  }, [nodeId, showVoided])
  useEffect(load, [load])
  const { ask, modal } = useCorrection(nodeId, load)
  const money = (n: number) => `R ${n.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`

  const editPayment = (p: Payment) => {
    const draft = { date: p.date, amount: String(p.amount), bank_reference: p.bank_reference }
    ask({
      title: 'Edit payment',
      cascade: `Changes the ${money(p.amount)} receipt of ${p.date}. Recon will re-run.`,
      movesStock: false,
      confirmLabel: 'Save',
      fields: (
        <div className="grid grid-cols-3 gap-2">
          <input className="input" type="date" defaultValue={draft.date} onChange={(e) => { draft.date = e.target.value }} />
          <input className="input" type="number" step="0.01" min="0" defaultValue={draft.amount} onChange={(e) => { draft.amount = e.target.value }} />
          <input className="input" defaultValue={draft.bank_reference} placeholder="Reference" onChange={(e) => { draft.bank_reference = e.target.value }} />
        </div>
      ),
      run: (reason) => api.patch(`/api/payments/${p._id}`, {
        date: draft.date, amount: parseFloat(draft.amount), bank_reference: draft.bank_reference, reason }),
    })
  }

  const actions = (p: Payment): RowAction[] => {
    if (!canFix || p.void) return []
    const dn = p.matched_delivery_id ? dnById[p.matched_delivery_id]?.dn_number || 'its delivery' : ''
    if (p.matched_delivery_id) {
      return [{
        label: 'Unmatch', onClick: () => ask({
          title: 'Unmatch payment', movesStock: false, confirmLabel: 'Unmatch',
          cascade: `Unmatches ${money(p.amount)} from ${dn}. ${dn}'s paid amount and status are recomputed from its remaining payments. Flags raised by the match stay open. Recon will re-run.`,
          run: (reason) => api.post(`/api/payments/${p._id}/unmatch`, { reason }),
        }),
      }]
    }
    return [
      { label: 'Edit', onClick: () => editPayment(p) },
      {
        label: 'Void', danger: true, onClick: () => ask({
          title: 'Void payment', movesStock: false, confirmLabel: 'Void',
          cascade: `Voids the ${money(p.amount)} receipt of ${p.date}. It stays on file, excluded from every total. Recon will re-run.`,
          run: (reason) => api.post(`/api/payments/${p._id}/void`, { reason }),
        }),
      },
    ]
  }

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
      await api.post(`/api/payments/${paymentId}/match`, { delivery_id: matchSel[paymentId] })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div>
      <SectionTitle>Payments</SectionTitle>
      <p className="text-sm text-gray-500 mb-4">Record money received from the bank and match each receipt to the delivery it pays. Short / over payments flag for review.</p>
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
      <div className="flex justify-end mb-1"><ShowVoided value={showVoided} onChange={setShowVoided} /></div>
      {modal}
      <div className="card p-0 overflow-x-auto">
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
                <th className="th">Delivery</th>
                {canMatch && <th className="th text-right">Fenix (incl VAT)</th>}
                {canMatch && <th className="th text-right">Partner balance</th>}
                {canFix && <th className="th"></th>}
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p._id} {...voidRow(p)}>
                  <td className="td">{p.date}</td>
                  <td className="td text-right font-semibold">R {p.amount.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</td>
                  <td className="td text-gray-500">{p.bank_reference}</td>
                  <td className="td"><StatusBadge status={p.status} /></td>
                  <td className="td">
                    {p.matched_delivery_id ? (
                      <span className="font-semibold">{dnById[p.matched_delivery_id]?.dn_number || '—'}</span>
                    ) : canMatch && !p.void ? (
                      <span className="flex items-center gap-1">
                        <select className="input py-1" value={matchSel[p._id] || ''} onChange={(e) => setMatchSel({ ...matchSel, [p._id]: e.target.value })}>
                          <option value="">select…</option>
                          {deliveries.filter((d) => !d.void && (d.status ?? 'unpaid') !== 'paid').map((d) => (
                            <option key={d._id} value={d._id}>{d.dn_number} (R {(d.total ?? 0).toFixed(2)})</option>
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
                  {canFix && <td className="td text-right"><RowMenu actions={actions(p)} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
