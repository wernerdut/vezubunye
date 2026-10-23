import { FileDown } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, errMsg, openPdf } from '../api'
import { ShareBar } from '../components/Actions'
import { ErrorBox, Field, Modal, PageTitle, Spinner, Status, useAction } from '../components/ui'
import { colourOf, lineLabel, useApp } from '../lib/ctx'
import { date, money } from '../lib/format'
import { QuoteForm } from './Quotes'

export default function QuoteDetail() {
  const { id } = useParams()
  const { cfg } = useApp()
  const pf = !!cfg.proforma
  const [paying, setPaying] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const navigate = useNavigate()
  const [q, setQ] = useState<any>(null)
  const [share, setShare] = useState<any>(null)
  const [editing, setEditing] = useState(false)
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')
  const [accepting, setAccepting] = useState(false)
  const [cod, setCod] = useState(false)
  const a = useAction()
  const load = useCallback(() => api.get(`/api/p/quotes/${id}`).then((r) => setQ(r.data)), [id])
  useEffect(() => { load() }, [load])
  if (!q) return <Spinner />
  const status = (s: string, extra: any = {}) => a.run(async () => {
    const r = await api.post(`/api/p/quotes/${id}/status`, { status: s, ...extra })
    if (s === 'accepted' && r.data.order) navigate(`/orders/${r.data.order._id}`)
    else load()
  }, errMsg)
  const open = q.status === 'draft' || q.status === 'sent'
  return (
    <div>
      <PageTitle title={`${q.quote_number}${q.rev > 1 ? ` rev ${q.rev}` : ''}`} sub={<span className="flex items-center gap-2">{q.customer_name} <Status s={q.status} /></span>}
        actions={<>
          <button className="btn-ghost" onClick={() => openPdf(`/api/p/quotes/${id}/pdf`)}><FileDown size={16} /> PDF</button>
          {open && <button className="btn-ghost" onClick={() => setEditing(true)}>Revise</button>}
          {open && <button className="btn-primary" onClick={() => a.run(async () => { setShare((await api.post(`/api/p/quotes/${id}/share`)).data); load() }, errMsg)}>Send</button>}
        </>} />
      <ErrorBox msg={a.error} />
      {share && <div className="card mb-4 p-4"><pre className="mb-3 whitespace-pre-wrap text-sm">{share.message}</pre><ShareBar {...share} pdfPath={`/api/p/quotes/${id}/pdf`} filename={q.quote_number} /></div>}
      <div className="card mb-4 overflow-x-auto">
        <table className="min-w-full">
          <thead className="border-b bg-gray-50"><tr><th className="th">Item</th><th className="th text-right">Qty</th><th className="th text-right">Unit</th><th className="th text-right">Total</th></tr></thead>
          <tbody>{q.lines.map((l: any) => (
            <tr key={l.line_id} className="border-b"><td className="td">
              {l.colour && <span className="mr-2 inline-block h-3 w-3 rounded-sm align-middle" style={{ background: colourOf(cfg, l.colour)?.swatch_hex }} />}
              {lineLabel(cfg, l)}{l.made_to_order && <span className="ml-2 text-xs text-violet-700">made to order</span>}</td>
              <td className="td text-right">{l.qty}</td><td className="td text-right">{money(l.price_includes_vat ? l.unit_incl : l.unit_ex)}</td><td className="td text-right">{money(l.line_total)}</td></tr>
          ))}</tbody>
          <tfoot><tr><td className="td" colSpan={3}>Excl VAT {money(q.subtotal)} · VAT {money(q.vat)}</td><td className="td text-right font-bold">{money(q.total)}</td></tr></tfoot>
        </table>
      </div>
      <p className="mb-4 text-sm text-gray-600">Dated {date(q.date)}{pf ? '' : ` · valid until ${date(q.valid_until)}`}{q.site ? ` · ${[q.site.label, q.site.address, q.site.town].filter(Boolean).join(', ')}` : ''}</p>
      {q.status === 'cancelled' && <p className="mb-4 text-sm text-gray-600">Cancelled: {q.cancelled_reason}</p>}
      {open && pf && (
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={a.busy} onClick={() => setPaying(true)}>Create tax invoice</button>
          <button className="btn-danger" onClick={() => setCancelling(true)}>Cancel proforma</button>
        </div>
      )}
      {open && !pf && (
        <div className="flex flex-wrap gap-2">
          {q.status === 'draft' && <button className="btn-ghost" onClick={() => status('sent')}>Mark sent</button>}
          <button className="btn-primary" disabled={a.busy} onClick={() => setAccepting(true)}>Accept → order</button>
          <button className="btn-danger" onClick={() => setDeclining(true)}>Declined</button>
        </div>
      )}
      {q.order_id && <button className="btn-ghost" onClick={() => navigate(`/orders/${q.order_id}`)}>Open order {q.order_number}</button>}
      {q.revisions?.length > 0 && <p className="mt-4 text-xs text-gray-500">Earlier revisions: {q.revisions.map((r: any) => `rev ${r.rev} ${money(r.total)}`).join(' · ')}</p>}
      {editing && <QuoteForm initial={q} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load() }} />}
      {declining && (
        <Modal title="Quote declined" onClose={() => setDeclining(false)}>
          <Field label="Reason (optional)"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={() => { setDeclining(false); status('declined', { reason }) }}>Save</button></div>
        </Modal>
      )}
      {paying && <CreateTaxInvoice q={q} onClose={() => { setPaying(false); load() }} onOpenOrder={(orderId) => navigate(`/orders/${orderId}`)} />}
      {cancelling && (
        <Modal title="Cancel proforma" onClose={() => setCancelling(false)}>
          <Field label="Reason"><input className="input" autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Bought elsewhere, no reply, duplicate…" /></Field>
          <div className="mt-4 flex justify-end"><button className="btn-danger" disabled={!reason.trim()} onClick={() => { setCancelling(false); status('cancelled', { reason }) }}>Cancel proforma</button></div>
        </Modal>
      )}
      {accepting && (
        <Modal title="Accept quote" onClose={() => setAccepting(false)}>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cod} onChange={(e) => setCod(e.target.checked)} /> Pay on delivery (cash or card at the site)</label>
          <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={() => { setAccepting(false); status('accepted', { order: { pay_on_delivery: cod } }) }}>Create order</button></div>
        </Modal>
      )}
    </div>
  )
}

/** Steven decides when the tax invoice is made. Record what has been paid so far (or nothing:
 * trade on account, pay on delivery), then send the invoice by WhatsApp or email. */
function CreateTaxInvoice({ q, onClose, onOpenOrder }: { q: any; onClose: () => void; onOpenOrder: (orderId: string) => void }) {
  const [method, setMethod] = useState('eft')
  const [amount, setAmount] = useState(String(q.total))
  const [reference, setReference] = useState('')
  const [cod, setCod] = useState(false)
  const [done, setDone] = useState<any>(null)
  const a = useAction()
  const [codMethod, setCodMethod] = useState('either')
  const paid = method !== 'none'
  const owed = paid ? q.total - (Number(amount) || 0) : q.total
  const save = () => a.run(async () => {
    const r = await api.post(`/api/p/quotes/${q._id}/invoice`, {
      payment: paid ? { method, amount: Number(amount), reference: reference || undefined } : null, pay_on_delivery: cod && owed > 0.004, cod_method: codMethod })
    const share = (await api.post(`/api/p/invoices/${r.data.invoice_id}/share`)).data
    setDone({ ...r.data, share })
  }, errMsg)
  if (done) {
    return (
      <Modal title={`Tax invoice ${done.invoice_number}`} onClose={onClose}>
        <p className="mb-3 text-sm text-gray-600">Order {done.order_number} is made{done.payment ? ' with the payment on it' : ''}. Send the invoice:</p>
        <pre className="mb-3 whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm">{done.share.message}</pre>
        <ShareBar {...done.share} pdfPath={`/api/p/invoices/${done.invoice_id}/pdf`} filename={done.invoice_number} />
        <div className="mt-4 flex justify-end"><button className="btn-ghost" onClick={() => onOpenOrder(done.order_id)}>Open order</button></div>
      </Modal>
    )
  }
  return (
    <Modal title={`Tax invoice from ${q.quote_number}`} onClose={onClose}>
      <div className="label">Paid so far</div>
      <div className="mb-3 flex flex-wrap gap-2">
        {[['eft', 'EFT in the bank'], ['cash', 'Cash'], ['yoco_machine', 'Card machine'], ['none', 'Nothing yet']].map(([k, l]) => (
          <button key={k} className={method === k ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setMethod(k)}>{l}</button>
        ))}
      </div>
      {paid && <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Amount" hint={Number(amount) < q.total ? `Part payment: ${money(q.total - Number(amount))} still owed` : undefined}>
          <input type="number" step="0.01" className="input" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label={method === 'yoco_machine' ? 'Slip reference' : 'Reference'}><input className="input" placeholder={method === 'yoco_machine' ? '' : q.quote_number} value={reference} onChange={(e) => setReference(e.target.value)} /></Field>
      </div>}
      {owed > 0.004 && <div className="mt-3 rounded-lg bg-gray-50 p-3">
        <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={cod} onChange={(e) => setCod(e.target.checked)} />
          {paid ? `Balance of ${money(owed)} paid on delivery` : 'Pay on delivery'}</label>
        {cod ? <div className="mt-2 flex gap-2">
          {['cash', 'yoco', 'either'].map((m) => <button key={m} className={codMethod === m ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setCodMethod(m)}>{m === 'yoco' ? 'Card (Yoco)' : m === 'either' ? 'Cash or card' : 'Cash'}</button>)}
        </div> : <p className="mt-1 text-xs text-gray-500">Not ticked: the tanks only leave once paid.</p>}
      </div>}
      <p className="mt-3 text-xs text-gray-500">This makes the order and its tax invoice, same lines and prices as the proforma.</p>
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Back</button>
        <button className="btn-primary" disabled={a.busy || (paid && !(Number(amount) > 0)) || (method === 'yoco_machine' && !reference)} onClick={save}>Create tax invoice</button></div>
    </Modal>
  )
}
