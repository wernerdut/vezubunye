import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errMsg } from '../api'
import { cleanLines, CustomerPicker, DeliverTo, Line, LineEditor, siteIdFor } from '../components/Forms'
import { Empty, ErrorBox, Field, Modal, PageTitle, Spinner, Status, Tabs, useAction } from '../components/ui'
import { useApp } from '../lib/ctx'
import { date, money } from '../lib/format'

const TABS = [
  { key: 'open', label: 'Open' }, { key: 'accepted', label: 'Accepted' }, { key: 'declined,expired,cancelled', label: 'Lost' }, { key: '', label: 'All' },
] as const
const PF_TABS = [
  { key: 'open', label: 'Open' }, { key: 'accepted', label: 'Invoiced' }, { key: 'cancelled,declined,expired', label: 'Cancelled' }, { key: '', label: 'All' },
]

export default function Quotes() {
  const navigate = useNavigate()
  const pf = !!useApp().cfg.proforma
  const [tab, setTab] = useState<string>('open')
  const [rows, setRows] = useState<any[] | null>(null)
  const [creating, setCreating] = useState(false)
  useEffect(() => {
    setRows(null)
    api.get('/api/p/quotes', { params: { status: tab === 'open' ? 'draft,sent' : tab || undefined } }).then((r) => setRows(r.data))
  }, [tab])
  return (
    <div>
      <PageTitle title={pf ? 'Proformas' : 'Quotes'} actions={<button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} /> {pf ? 'New proforma' : 'New quote'}</button>} />
      <Tabs tabs={(pf ? PF_TABS : TABS).map((t) => ({ key: t.key as string, label: t.label }))} value={tab} onChange={setTab} />
      <div className="card divide-y">
        {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text={pf ? 'No proformas here.' : 'No quotes here.'} /> : rows.map((q) => (
          <button key={q._id} onClick={() => navigate(`/quotes/${q._id}`)} className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-gray-50">
            <div className="min-w-0">
              <div className="font-semibold text-brand-dark">{q.quote_number}{q.rev > 1 ? ` rev ${q.rev}` : ''} · {q.customer_name}</div>
              <div className="text-xs text-gray-500">{date(q.date)}{pf ? (q.order_number ? ` · ${q.order_number}` : '') : ` · valid until ${date(q.valid_until)}`}</div>
            </div>
            <div className="flex items-center gap-2"><span className="font-bold tabular-nums">{money(q.total)}</span><Status s={q.status} /></div>
          </button>
        ))}
      </div>
      {creating && <QuoteForm onClose={() => setCreating(false)} onSaved={(q) => navigate(`/quotes/${q._id}`)} />}
    </div>
  )
}

export function QuoteForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (q: any) => void }) {
  const pf = !!useApp().cfg.proforma
  const [customer, setCustomer] = useState<any>(null)
  const [lines, setLines] = useState<Line[]>(initial?.lines || [{ kind: 'tank', qty: 1, grade: 'A' }])
  const [siteId, setSiteId] = useState(initial?.site?.site_id || '')
  const [newSite, setNewSite] = useState<any>(null)
  const [notes, setNotes] = useState(initial?.notes || '')
  const [validUntil, setValidUntil] = useState(initial?.valid_until || '')
  const a = useAction()
  useEffect(() => { if (initial) api.get(`/api/p/customers/${initial.customer_id}`, { params: { brief: true } }).then((r) => setCustomer(r.data)) }, [initial])
  const save = () => a.run(async () => {
    const site_id = await siteIdFor(customer, siteId, newSite, (st) => { setCustomer({ ...customer, sites: [...(customer.sites || []), st] }); setSiteId(st.site_id); setNewSite(null) })
    const body = { customer_id: customer._id, lines: cleanLines(lines), site_id, notes, valid_until: validUntil || undefined }
    const r = initial ? await api.put(`/api/p/quotes/${initial._id}`, body) : await api.post('/api/p/quotes', body)
    onSaved(r.data)
  }, errMsg)
  return (
    <Modal title={initial ? `Revise ${initial.quote_number}` : pf ? 'New proforma' : 'New quote'} onClose={onClose} wide>
      <div className="space-y-4">
        {initial ? <div className="font-semibold">{initial.customer_name}</div> : <CustomerPicker value={customer} onChange={(c) => { setCustomer(c); setSiteId(''); setNewSite(null) }} />}
        {customer && <>
          <LineEditor lines={lines} setLines={setLines} tier={customer.price_tier || 'retail'} />
          <div className="grid gap-3 sm:grid-cols-2">
            <DeliverTo customer={customer} siteId={siteId} setSiteId={setSiteId} site={newSite} setSite={setNewSite} optional />
            {!pf && <Field label="Valid until"><input type="date" className="input" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} /></Field>}
          </div>
          <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        </>}
        <ErrorBox msg={a.error} />
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!customer || a.busy} onClick={save}>{initial ? 'Save revision' : pf ? 'Create proforma' : 'Create quote'}</button></div>
      </div>
    </Modal>
  )
}
