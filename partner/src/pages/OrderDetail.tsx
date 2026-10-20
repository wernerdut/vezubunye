import { FileDown, Truck } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, errMsg, openPdf } from '../api'
import { PayLinkModal, RecordPayment, ScheduleDelivery, sendToDriver, ShareModal } from '../components/Actions'
import { cleanLines, Line, LineEditor } from '../components/Forms'
import { Chip, ErrorBox, Field, Modal, PageTitle, Spinner, Status, useAction } from '../components/ui'
import { colourOf, lineLabel, useApp } from '../lib/ctx'
import { date, label, money } from '../lib/format'

export default function OrderDetail() {
  const { id } = useParams()
  const { cfg, isAdmin, me } = useApp()
  const navigate = useNavigate()
  const [o, setO] = useState<any>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const a = useAction()
  const load = useCallback(() => api.get(`/api/p/orders/${id}`).then((r) => setO(r.data)), [id])
  useEffect(() => { load() }, [load])
  if (!o) return <Spinner />
  const close = () => { setModal(null); load() }
  const inv = o.invoices.find((i: any) => !i.void && i.status !== 'paid')
  const sched = o.delivery_notes.find((d: any) => !d.void && d.delivery_status === 'scheduled')
  const nextBtn = () => {
    switch (o.next_action.code) {
      case 'send_pay_link': return inv && me.features.yoco ? <button className="btn-primary" onClick={() => setModal(<PayLinkModal invoiceId={inv._id} onClose={close} />)}>Send pay link</button> : null
      case 'schedule_delivery': return <button className="btn-primary" onClick={() => setModal(<ScheduleDelivery orderId={o._id} onClose={() => setModal(null)} onDone={close} />)}><Truck size={16} /> Schedule delivery</button>
      case 'dispatch': return sched ? <button className="btn-primary" onClick={async () => setModal(<ShareModal title="Send to driver" data={await sendToDriver(sched._id)} onClose={close} />)}>Send to driver</button> : null
      default: return null
    }
  }
  return (
    <div>
      <PageTitle title={o.order_number || o.display_number} sub={<span className="flex flex-wrap items-center gap-2">
        <button className="font-semibold text-brand-primary" onClick={() => navigate(`/customers/${o.customer_id}`)}>{o.customer_name}</button>
        <Status s={o.payment_status} />{o.overdue && <Status s="overdue" />}<Status s={o.delivery_status} />
        <Chip tone={o.pay_on_delivery ? 'violet' : o.payment_basis === 'terms' ? 'blue' : 'grey'}>{o.pay_on_delivery ? 'Pay on delivery' : label(o.payment_basis)}</Chip>
        {o.order_status !== 'open' && <Status s={o.order_status} />}
      </span>} actions={<>{nextBtn()}</>} />
      <ErrorBox msg={a.error} />
      {o.refund_due && <div className="mb-3 rounded-lg bg-red-50 p-3 text-sm font-semibold text-red-800">Refund due {money(o.refund_due)} (done in the books by hand).</div>}

      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        {[['Value', money(o.value)], ['Invoiced', money(o.invoiced)], ['Paid', money(o.paid)], ['Balance', money(o.balance)]].map(([l, v]) => (
          <div key={l} className="card p-3"><div className="label">{l}</div><div className="text-lg font-bold tabular-nums">{v}</div></div>
        ))}
      </div>
      <div className="mb-2 text-sm font-semibold text-brand-primary">Next: {o.next_action.label}</div>

      <div className="card mb-4 overflow-x-auto">
        <div className="flex items-center justify-between border-b p-3"><h2 className="font-bold">Lines</h2>
          {o.order_status === 'open' && <div className="flex gap-2">
            <button className="btn-ghost btn-sm" onClick={() => setModal(<EditLines order={o} onClose={close} />)}>Edit</button>
            {isAdmin && <button className="btn-danger btn-sm" onClick={() => setModal(<Reason title="Cancel remainder" onClose={() => setModal(null)} onSave={(reason) => api.post(`/api/p/orders/${id}/cancel-remainder`, { reason }).then(close)} />)}>Cancel remainder</button>}
          </div>}
        </div>
        <table className="min-w-full">
          <thead className="bg-gray-50"><tr><th className="th">Item</th><th className="th text-right">Ordered</th><th className="th text-right">Delivered</th><th className="th text-right">Remaining</th><th className="th text-right">Total</th></tr></thead>
          <tbody>{o.lines.map((l: any) => (
            <tr key={l.line_id} className="border-t"><td className="td">
              {l.colour && <span className="mr-2 inline-block h-3 w-3 rounded-sm align-middle" style={{ background: colourOf(cfg, l.colour)?.swatch_hex }} />}
              {lineLabel(cfg, l)}{l.made_to_order && <span className="ml-2 text-xs text-violet-700">made to order</span>}</td>
              <td className="td text-right">{l.qty_ordered}{l.qty_cancelled ? <span className="text-xs text-gray-400"> (−{l.qty_cancelled})</span> : ''}</td>
              <td className="td text-right">{l.kind === 'charge' ? '' : l.qty_delivered}</td><td className="td text-right font-semibold">{l.kind === 'charge' ? '' : l.qty_remaining}</td>
              <td className="td text-right">{money(l.line_total)}</td></tr>
          ))}</tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card">
          <div className="flex items-center justify-between border-b p-3"><h2 className="font-bold">Deliveries</h2>
            {o.order_status === 'open' && o.qty_remaining > 0 && <button className="btn-ghost btn-sm" onClick={() => setModal(<ScheduleDelivery orderId={o._id} onClose={() => setModal(null)} onDone={close} />)}>Schedule</button>}
          </div>
          {o.delivery_notes.length === 0 && <p className="p-3 text-sm text-gray-400">No deliveries yet.</p>}
          {o.delivery_notes.map((d: any) => (
            <button key={d._id} onClick={() => navigate(`/deliveries/${d._id}`)} className={`flex w-full items-center justify-between gap-2 border-b p-3 text-left hover:bg-gray-50 ${d.void ? 'opacity-50' : ''}`}>
              <div className="min-w-0 text-sm"><div className="font-semibold">{d.dn_number} · {d.lines.reduce((s: number, l: any) => s + l.quantity, 0)} tanks</div>
                <div className="text-xs text-gray-500">{date(d.planned_date || d.date)}{d.driver?.name ? ` · ${d.driver.name}` : ''}{d.pod ? ' · POD' : ''}
                  {d.collect_amount ? ` · collect ${money(d.collect_amount)}` : ''}{d.collection ? ` · ${d.collection.method === 'not_collected' ? 'not collected' : 'collected ' + money(d.collection.amount)}` : ''}</div></div>
              <Status s={d.void ? 'void' : d.delivery_status} />
            </button>
          ))}
        </div>
        <div className="card">
          <div className="flex items-center justify-between border-b p-3"><h2 className="font-bold">Invoices and payments</h2>
            {inv && <button className="btn-ghost btn-sm" onClick={() => setModal(<RecordPayment customerId={o.customer_id} invoiceId={inv._id} defaultAmount={inv.total - inv.amount_paid} onClose={() => setModal(null)} onDone={close} />)}>Record payment</button>}
          </div>
          {o.invoices.map((i: any) => (
            <div key={i._id} className={`flex items-center justify-between gap-2 border-b p-3 ${i.void ? 'opacity-50' : ''}`}>
              <button className="min-w-0 text-left text-sm" onClick={() => navigate(`/invoices/${i._id}`)}>
                <div className="font-semibold">{i.display_number}</div><div className="text-xs text-gray-500">{date(i.date)} · due {date(i.due_date)} · {money(i.amount_paid)} of {money(i.total)}</div>
              </button>
              <div className="flex items-center gap-2">
                {!i.legacy && <button className="p-1 text-gray-500" onClick={() => openPdf(`/api/p/invoices/${i._id}/pdf`)}><FileDown size={16} /></button>}
                <Status s={i.void ? 'void' : i.status} />
              </div>
            </div>
          ))}
          {o.payments.map((p: any) => (
            <div key={p._id} className={`flex justify-between border-b px-3 py-2 text-sm ${p.void ? 'line-through opacity-50' : ''}`}>
              <span>{date(p.date)} · {label(p.method + (p.source ? '_' + p.source : ''))}{p.reference ? ` · ${p.reference}` : ''}</span><span className="font-semibold tabular-nums">{money(p.amount)}</span>
            </div>
          ))}
          {o.invoices.length === 0 && <p className="p-3 text-sm text-gray-400">{o.payment_basis === 'terms' ? 'Invoiced per delivery.' : 'No invoice.'}</p>}
        </div>
      </div>
      {modal}
    </div>
  )
}

function EditLines({ order, onClose }: { order: any; onClose: () => void }) {
  const [lines, setLines] = useState<Line[]>(order.order_lines.map((l: any) => ({ ...l })))
  const [reason, setReason] = useState('')
  const a = useAction()
  return (
    <Modal title="Edit order lines" onClose={onClose} wide>
      <p className="mb-3 text-sm text-gray-500">Lines cannot go below what has been dispatched. If the value changes, the invoice is reissued and payments move to it.</p>
      <LineEditor lines={lines} setLines={setLines} tier={order.price_tier} />
      <div className="mt-3"><Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!reason || a.busy} onClick={() => a.run(async () => { await api.put(`/api/p/orders/${order._id}/lines`, { lines: cleanLines(lines), reason }); onClose() }, errMsg)}>Save</button></div>
    </Modal>
  )
}

export function Reason({ title, onClose, onSave, cta = 'Confirm' }: { title: string; onClose: () => void; onSave: (r: string) => Promise<unknown>; cta?: string }) {
  const [reason, setReason] = useState('')
  const a = useAction()
  return (
    <Modal title={title} onClose={onClose}>
      <Field label="Reason"><input className="input" autoFocus value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Back</button>
        <button className="btn-primary" disabled={!reason.trim() || a.busy} onClick={() => a.run(() => onSave(reason), errMsg)}>{cta}</button></div>
    </Modal>
  )
}
