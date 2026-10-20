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
      <p className="mb-4 text-sm text-gray-600">Dated {date(q.date)} · valid until {date(q.valid_until)}{q.site ? ` · ${q.site.label}` : ''}</p>
      {open && (
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
      {accepting && (
        <Modal title="Accept quote" onClose={() => setAccepting(false)}>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cod} onChange={(e) => setCod(e.target.checked)} /> Pay on delivery (cash or card at the site)</label>
          <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={() => { setAccepting(false); status('accepted', { order: { pay_on_delivery: cod } }) }}>Create order</button></div>
        </Modal>
      )}
    </div>
  )
}
