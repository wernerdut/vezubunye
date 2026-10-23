import { Copy, Mail, MessageCircle, Share2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { lineLabel, useApp } from '../lib/ctx'
import { money, siteLine, today } from '../lib/format'
import { ErrorBox, Field, Modal, useAction } from './ui'

/** WhatsApp, the device share sheet, and copy: the three ways a link leaves the app. */
export function ShareBar({ whatsapp, email, message, url, pdfPath, filename }: {
  whatsapp?: string; email?: string; message: string; url?: string; pdfPath?: string; filename?: string
}) {
  const [copied, setCopied] = useState(false)
  const share = async () => {
    try {
      if (pdfPath && navigator.canShare) {
        const r = await api.get(pdfPath, { responseType: 'blob' })
        const file = new File([r.data], `${filename || 'document'}.pdf`, { type: 'application/pdf' })
        if (navigator.canShare({ files: [file] })) {
          await navigator.share({ files: [file], text: message })
          return
        }
      }
      if (navigator.share) await navigator.share({ text: message, url })
    } catch { /* user cancelled */ }
  }
  return (
    <div className="flex flex-wrap gap-2">
      {whatsapp && <a className="btn-primary" href={whatsapp} target="_blank" rel="noreferrer"><MessageCircle size={16} /> WhatsApp</a>}
      {email && <a className="btn-ghost" href={email}><Mail size={16} /> Email</a>}
      {typeof navigator !== 'undefined' && 'share' in navigator && <button className="btn-ghost" onClick={share}><Share2 size={16} /> Share</button>}
      <button className="btn-ghost" onClick={async () => { await navigator.clipboard.writeText(message); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>
        <Copy size={16} /> {copied ? 'Copied' : url ? 'Copy message' : 'Copy'}
      </button>
    </div>
  )
}

export function ShareModal({ title, data, onClose, pdfPath, filename }: {
  title: string; data: { whatsapp?: string; message: string; url?: string }; onClose: () => void; pdfPath?: string; filename?: string
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <pre className="mb-4 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm text-gray-800">{data.message}</pre>
      <ShareBar {...data} pdfPath={pdfPath} filename={filename} />
    </Modal>
  )
}

/** Schedule a full or part delivery against an order. */
export function ScheduleDelivery({ orderId, onClose, onDone }: { orderId: string; onClose: () => void; onDone: (dn: any) => void }) {
  const { cfg, isAdmin } = useApp()
  const [order, setOrder] = useState<any>(null)
  const [qty, setQty] = useState<Record<string, number>>({})
  const [siteId, setSiteId] = useState('')
  const [planned, setPlanned] = useState(today())
  const [driverIdx, setDriverIdx] = useState(-1)
  const [adhoc, setAdhoc] = useState({ name: '', phone: '' })
  const [reg, setReg] = useState('')
  const [invoiceNow, setInvoiceNow] = useState(true)
  const [release, setRelease] = useState('')
  const [customer, setCustomer] = useState<any>(null)
  const a = useAction()
  useEffect(() => {
    api.get(`/api/p/orders/${orderId}`).then(async (r) => {
      setOrder(r.data)
      const q: Record<string, number> = {}
      r.data.lines.filter((l: any) => l.kind !== 'charge').forEach((l: any) => { q[l.line_id] = l.qty_remaining })
      setQty(q)
      setCustomer((await api.get(`/api/p/customers/${r.data.customer_id}`)).data)
    })
  }, [orderId])
  if (!order) return null
  const tankLines = order.lines.filter((l: any) => l.kind !== 'charge' && l.qty_remaining > 0)
  const driver = driverIdx >= 0 ? cfg.drivers[driverIdx] : adhoc.name ? adhoc : null
  const save = () => a.run(async () => {
    const r = await api.post(`/api/p/orders/${orderId}/deliveries`, {
      lines: Object.entries(qty).filter(([, n]) => n > 0).map(([order_line_id, quantity]) => ({ order_line_id, quantity })),
      planned_date: planned, date: planned, site_id: siteId || undefined,
      driver: driver ? { name: driver.name, phone: driver.phone } : undefined,
      vehicle_reg: reg || (driverIdx >= 0 ? cfg.drivers[driverIdx].vehicle_reg : '') || '',
      invoice_now: invoiceNow, release_reason: release || undefined,
    })
    onDone(r.data)
  }, errMsg)
  const needsRelease = a.error?.startsWith('Paid R')
  return (
    <Modal title={`Schedule delivery · ${order.order_number}`} onClose={onClose} wide>
      <div className="space-y-4">
        {order.releasable_value != null && (
          <p className="rounded-lg bg-gray-50 p-2 text-sm">Paid {money(order.paid)} · already out {money(order.dispatched_value)} · can release {money(order.releasable_value)}</p>
        )}
        <div className="space-y-2">
          {tankLines.length === 0 && <p className="text-sm text-gray-500">Nothing left to deliver.</p>}
          {tankLines.map((l: any) => (
            <div key={l.line_id} className="flex items-center justify-between gap-3 rounded-lg border p-2">
              <div className="min-w-0 text-sm"><div className="font-semibold">{lineLabel(cfg, l)}</div><div className="text-xs text-gray-500">{l.qty_remaining} remaining</div></div>
              <input type="number" min={0} max={l.qty_remaining} className="input w-20" value={qty[l.line_id] ?? 0}
                onChange={(e) => setQty({ ...qty, [l.line_id]: Math.max(0, Math.min(l.qty_remaining, Number(e.target.value))) })} />
            </div>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Delivery site">
            <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
              <option value="">{siteLine(order.site) || 'Order default'}</option>
              {(customer?.sites || []).map((s: any) => <option key={s.site_id} value={s.site_id}>{siteLine(s)}{s.lat == null ? ' (not pinned)' : ''}</option>)}
            </select>
          </Field>
          <Field label="Planned date"><input type="date" className="input" value={planned} onChange={(e) => setPlanned(e.target.value)} /></Field>
          <Field label="Driver">
            <select className="input" value={driverIdx} onChange={(e) => setDriverIdx(Number(e.target.value))}>
              <option value={-1}>Other / later</option>
              {cfg.drivers.map((d, i) => <option key={i} value={i}>{d.name}</option>)}
            </select>
          </Field>
          {driverIdx < 0 ? (
            <div className="grid grid-cols-2 gap-2">
              <Field label="Driver name"><input className="input" value={adhoc.name} onChange={(e) => setAdhoc({ ...adhoc, name: e.target.value })} /></Field>
              <Field label="Phone"><input className="input" value={adhoc.phone} onChange={(e) => setAdhoc({ ...adhoc, phone: e.target.value })} /></Field>
            </div>
          ) : (
            <Field label="Vehicle reg"><input className="input" value={reg} placeholder={cfg.drivers[driverIdx].vehicle_reg || ''} onChange={(e) => setReg(e.target.value)} /></Field>
          )}
        </div>
        {order.payment_basis === 'terms' && (
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={invoiceNow} onChange={(e) => setInvoiceNow(e.target.checked)} /> Invoice now</label>
        )}
        {order.pay_on_delivery && <p className="text-sm text-violet-700">Pay on delivery: the driver collects at the site.</p>}
        <ErrorBox msg={a.error} />
        {needsRelease && isAdmin && (
          <Field label="Release beyond what is paid (reason)"><input className="input" value={release} onChange={(e) => setRelease(e.target.value)} placeholder="Why this goes out unpaid" /></Field>
        )}
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={a.busy || tankLines.length === 0} onClick={save}>Schedule</button>
        </div>
      </div>
    </Modal>
  )
}

/** Cash, Yoco card machine or EFT (non-Xero) receipt, with editable allocation. */
export function RecordPayment({ customerId, invoiceId, defaultAmount, onClose, onDone, methods }: {
  customerId: string; invoiceId?: string; defaultAmount?: number; onClose: () => void; onDone: () => void; methods?: string[]
}) {
  const { me } = useApp()
  const allowed = methods || ['cash', 'yoco_machine', ...(me.features.xero ? [] : ['eft'])]
  const [method, setMethod] = useState(allowed[0])
  const [amount, setAmount] = useState<number>(defaultAmount || 0)
  const [reference, setReference] = useState('')
  const [d, setD] = useState(today())
  const [preview, setPreview] = useState<any>(null)
  const [alloc, setAlloc] = useState<Record<string, number>>({})
  const a = useAction()
  useEffect(() => {
    if (!amount) return
    api.get('/api/p/payments/allocation-preview', { params: { customer_id: customerId, amount } }).then((r) => {
      setPreview(r.data)
      const m: Record<string, number> = {}
      if (invoiceId) {
        m[invoiceId] = Math.min(amount, r.data.open_invoices.find((i: any) => i._id === invoiceId)?.balance ?? amount)
      } else r.data.allocations.forEach((x: any) => { m[x.invoice_id] = x.amount })
      setAlloc(m)
    })
  }, [amount, customerId, invoiceId])
  const total = Object.values(alloc).reduce((s, v) => s + (v || 0), 0)
  const save = () => a.run(async () => {
    await api.post('/api/p/payments', {
      method, amount, reference, date: d, customer_id: customerId,
      allocations: Object.entries(alloc).filter(([, v]) => v > 0).map(([invoice_id, amount]) => ({ invoice_id, amount })),
    })
    onDone()
  }, errMsg)
  return (
    <Modal title="Record payment" onClose={onClose}>
      <div className="space-y-3">
        <div className="flex gap-2">
          {allowed.map((m) => (
            <button key={m} className={method === m ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setMethod(m)}>
              {m === 'cash' ? 'Cash' : m === 'yoco_machine' ? 'Card machine' : 'EFT'}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount"><input type="number" step="0.01" className="input" value={amount || ''} onChange={(e) => setAmount(Number(e.target.value))} /></Field>
          <Field label="Date"><input type="date" className="input" value={d} onChange={(e) => setD(e.target.value)} /></Field>
        </div>
        {method !== 'cash' && <Field label={method === 'eft' ? 'Bank reference' : 'Slip reference'}><input className="input" value={reference} onChange={(e) => setReference(e.target.value)} /></Field>}
        {preview && preview.open_invoices.length > 0 && (
          <div>
            <div className="label">Allocate to</div>
            <div className="space-y-1">
              {preview.open_invoices.map((i: any) => (
                <div key={i._id} className="flex items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 truncate">{i.number} · due {money(i.balance)}</span>
                  <input type="number" step="0.01" className="input w-28" value={alloc[i._id] ?? 0}
                    onChange={(e) => setAlloc({ ...alloc, [i._id]: Number(e.target.value) })} />
                </div>
              ))}
            </div>
            {amount - total > 0.004 && <p className="mt-1 text-xs text-amber-700">{money(amount - total)} stays on the customer as credit.</p>}
            {total - amount > 0.004 && <p className="mt-1 text-xs text-red-700">Allocated more than received.</p>}
          </div>
        )}
        <ErrorBox msg={a.error} />
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={a.busy || !amount || total - amount > 0.004} onClick={save}>Save</button>
        </div>
      </div>
    </Modal>
  )
}

/** Pay link, then WhatsApp. */
export function PayLinkModal({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    api.post(`/api/p/invoices/${invoiceId}/pay-link`).then((r) => setData(r.data)).catch((e) => setErr(errMsg(e)))
  }, [invoiceId])
  if (err) return <Modal title="Pay link" onClose={onClose}><ErrorBox msg={err} /></Modal>
  if (!data) return null
  return <ShareModal title="Send pay link" data={data} onClose={onClose} />
}

export async function sendToDriver(dnId: string): Promise<any> {
  const r = await api.post(`/api/p/deliveries/${dnId}/send-to-driver`, {})
  return r.data
}
