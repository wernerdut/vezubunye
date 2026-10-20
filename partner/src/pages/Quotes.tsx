import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errMsg } from '../api'
import { cleanLines, CustomerPicker, Line, LineEditor } from '../components/Forms'
import { Empty, ErrorBox, Field, Modal, PageTitle, Spinner, Status, Tabs, useAction } from '../components/ui'
import { date, money } from '../lib/format'

const TABS = [
  { key: 'open', label: 'Open' }, { key: 'accepted', label: 'Accepted' }, { key: 'declined,expired', label: 'Lost' }, { key: '', label: 'All' },
] as const

export default function Quotes() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<string>('open')
  const [rows, setRows] = useState<any[] | null>(null)
  const [creating, setCreating] = useState(false)
  useEffect(() => {
    setRows(null)
    api.get('/api/p/quotes', { params: { status: tab === 'open' ? 'draft,sent' : tab || undefined } }).then((r) => setRows(r.data))
  }, [tab])
  return (
    <div>
      <PageTitle title="Quotes" actions={<button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} /> New quote</button>} />
      <Tabs tabs={TABS.map((t) => ({ key: t.key as string, label: t.label }))} value={tab} onChange={setTab} />
      <div className="card divide-y">
        {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text="No quotes here." /> : rows.map((q) => (
          <button key={q._id} onClick={() => navigate(`/quotes/${q._id}`)} className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-gray-50">
            <div className="min-w-0">
              <div className="font-semibold text-brand-dark">{q.quote_number}{q.rev > 1 ? ` rev ${q.rev}` : ''} · {q.customer_name}</div>
              <div className="text-xs text-gray-500">{date(q.date)} · valid until {date(q.valid_until)}</div>
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
  const [customer, setCustomer] = useState<any>(null)
  const [lines, setLines] = useState<Line[]>(initial?.lines || [{ kind: 'tank', qty: 1, grade: 'A' }])
  const [siteId, setSiteId] = useState('')
  const [notes, setNotes] = useState(initial?.notes || '')
  const [validUntil, setValidUntil] = useState(initial?.valid_until || '')
  const a = useAction()
  useEffect(() => { if (initial) api.get(`/api/p/customers/${initial.customer_id}`).then((r) => setCustomer(r.data)) }, [initial])
  const save = () => a.run(async () => {
    const body = { customer_id: customer._id, lines: cleanLines(lines), site_id: siteId || undefined, notes, valid_until: validUntil || undefined }
    const r = initial ? await api.put(`/api/p/quotes/${initial._id}`, body) : await api.post('/api/p/quotes', body)
    onSaved(r.data)
  }, errMsg)
  return (
    <Modal title={initial ? `Revise ${initial.quote_number}` : 'New quote'} onClose={onClose} wide>
      <div className="space-y-4">
        {initial ? <div className="font-semibold">{initial.customer_name}</div> : <CustomerPicker value={customer} onChange={setCustomer} />}
        {customer && <>
          <LineEditor lines={lines} setLines={setLines} tier={customer.price_tier || 'retail'} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Delivery site (optional)">
              <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
                <option value="">Not yet</option>
                {(customer.sites || []).map((s: any) => <option key={s.site_id} value={s.site_id}>{s.label}</option>)}
              </select>
            </Field>
            <Field label="Valid until"><input type="date" className="input" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} /></Field>
          </div>
          <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        </>}
        <ErrorBox msg={a.error} />
        <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!customer || a.busy} onClick={save}>{initial ? 'Save revision' : 'Create quote'}</button></div>
      </div>
    </Modal>
  )
}
