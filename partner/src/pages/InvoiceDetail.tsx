import { ExternalLink, FileDown } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, errMsg, openPdf } from '../api'
import { PayLinkModal, RecordPayment, ShareModal } from '../components/Actions'
import { ErrorBox, PageTitle, Spinner, Status, useAction } from '../components/ui'
import { lineLabel, useApp } from '../lib/ctx'
import { date, label, money } from '../lib/format'
import { Reason } from './OrderDetail'

export default function InvoiceDetail() {
  const { id } = useParams()
  const { cfg, isAdmin, me } = useApp()
  const navigate = useNavigate()
  const [inv, setInv] = useState<any>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const a = useAction()
  const load = useCallback(() => api.get(`/api/p/invoices/${id}`).then((r) => setInv(r.data)), [id])
  useEffect(() => { load() }, [load])
  if (!inv) return <Spinner />
  const close = () => { setModal(null); load() }
  const open = !inv.void && inv.status !== 'paid'
  return (
    <div>
      <PageTitle title={inv.display_number} sub={<span className="flex flex-wrap items-center gap-2">{inv.customer_name} <Status s={inv.void ? 'void' : inv.status} />
        <button className="text-brand-primary" onClick={() => navigate(`/orders/${inv.order_id}`)}>Order {inv.order_number || ''}</button>
        {inv.xero?.url && <a className="inline-flex items-center gap-1 text-brand-primary" href={inv.xero.url} target="_blank" rel="noreferrer">Xero <ExternalLink size={12} /></a>}
        {inv.xero?.status === 'waiting' && <span className="text-xs text-amber-700">waiting for Xero</span>}</span>}
        actions={!inv.legacy && <>
          <button className="btn-ghost" onClick={() => openPdf(`/api/p/invoices/${id}/pdf`)}><FileDown size={16} /> PDF</button>
          <button className="btn-ghost" onClick={() => a.run(async () => setModal(<ShareModal title="Share invoice" data={(await api.post(`/api/p/invoices/${id}/share`)).data} onClose={close} pdfPath={`/api/p/invoices/${id}/pdf`} filename={inv.display_number} />), errMsg)}>Share</button>
          {open && me.features.yoco && <button className="btn-primary" onClick={() => setModal(<PayLinkModal invoiceId={inv._id} onClose={close} />)}>Send pay link</button>}
        </>} />
      <ErrorBox msg={a.error} />
      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        {[['Total', money(inv.total)], ['Paid', money(inv.amount_paid)], ['Due', money(inv.balance)], ['Due date', date(inv.due_date)]].map(([l, v]) => (
          <div key={l} className="card p-3"><div className="label">{l}</div><div className="text-lg font-bold tabular-nums">{v}</div></div>
        ))}
      </div>
      <div className="card mb-4 overflow-x-auto">
        <table className="min-w-full">
          <thead className="bg-gray-50"><tr><th className="th">Item</th><th className="th text-right">Qty</th><th className="th text-right">Excl VAT</th><th className="th text-right">VAT</th><th className="th text-right">Total</th></tr></thead>
          <tbody>{inv.lines.map((l: any, i: number) => <tr key={i} className="border-t"><td className="td">{lineLabel(cfg, l)}</td><td className="td text-right">{l.qty ?? l.quantity}</td>
            <td className="td text-right">{money(l.line_ex)}</td><td className="td text-right">{money(l.line_vat)}</td><td className="td text-right">{money(l.line_total)}</td></tr>)}</tbody>
          <tfoot className="font-semibold"><tr className="border-t"><td className="td" colSpan={2}>Totals</td><td className="td text-right">{money(inv.subtotal)}</td><td className="td text-right">{money(inv.vat)}</td><td className="td text-right">{money(inv.total)}</td></tr></tfoot>
        </table>
      </div>
      {inv.fenix_value != null && isAdmin && <p className="mb-4 text-sm text-gray-600">Due to Fenix on this invoice: <b>{money(inv.fenix_value)}</b> (falls due as it is paid)</p>}
      <div className="card">
        <div className="flex items-center justify-between border-b p-3"><h2 className="font-bold">Payments</h2>
          {open && <button className="btn-ghost btn-sm" onClick={() => setModal(<RecordPayment customerId={inv.customer_id} invoiceId={inv._id} defaultAmount={inv.balance} onClose={() => setModal(null)} onDone={close} />)}>Record payment</button>}</div>
        {inv.payments.length === 0 && <p className="p-3 text-sm text-gray-400">None yet.</p>}
        {inv.payments.map((p: any) => {
          const al = p.allocations.find((x: any) => x.invoice_id === inv._id)
          return <div key={p._id} className={`flex justify-between border-b px-3 py-2 text-sm ${p.void ? 'line-through opacity-50' : ''}`}>
            <span>{date(p.date)} · {label(p.method + (p.source ? '_' + p.source : ''))}{p.reference ? ` · ${p.reference}` : ''}</span><span className="font-semibold tabular-nums">{money(al?.amount)}</span></div>
        })}
        {inv.pay_links?.map((pl: any) => <div key={pl._id} className="px-3 py-2 text-xs text-gray-500">Pay link {pl.status}{pl.sent_at ? `, sent ${date(pl.sent_at)}` : ''}: {pl.url}</div>)}
      </div>
      {isAdmin && !inv.void && !inv.legacy && (
        <div className="mt-4 flex gap-2">
          <button className="btn-danger" onClick={() => setModal(<Reason title="Void and reissue (payments move to the new invoice)" onClose={() => setModal(null)} cta="Void and reissue" onSave={(reason) => api.post(`/api/p/invoices/${id}/void`, { reason, reissue: true }).then((r) => { setModal(null); navigate(`/invoices/${r.data.new._id}`) })} />)}>Void and reissue</button>
          {inv.amount_paid === 0 && <button className="btn-danger" onClick={() => setModal(<Reason title="Void invoice" onClose={() => setModal(null)} cta="Void" onSave={(reason) => api.post(`/api/p/invoices/${id}/void`, { reason }).then(close)} />)}>Void</button>}
        </div>
      )}
      {modal}
    </div>
  )
}
