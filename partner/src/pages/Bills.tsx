import { Camera, Check, Plus, Sparkles } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { api, errMsg, openPdf } from '../api'
import { Chip, Empty, ErrorBox, Field, Modal, PageTitle, Spinner, Status, Tabs, useAction } from '../components/ui'
import { useApp } from '../lib/ctx'
import { date, money } from '../lib/format'

export default function Bills() {
  const { isAdmin } = useApp()
  const [tab, setTab] = useState<'mine' | 'to_approve' | 'all' | 'suppliers'>('mine')
  const [rows, setRows] = useState<any[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [sel, setSel] = useState<string[]>([])
  const load = useCallback(() => {
    setRows(null)
    if (tab !== 'suppliers') api.get('/api/p/bills', { params: { scope: tab } }).then((r) => setRows(r.data))
  }, [tab])
  useEffect(() => { load() }, [load])
  const tabs = [{ key: 'mine' as const, label: 'My bills' }, ...(isAdmin ? [{ key: 'to_approve' as const, label: 'To approve' }, { key: 'all' as const, label: 'All' }, { key: 'suppliers' as const, label: 'Suppliers' }] : [])]
  return (
    <div>
      <PageTitle title="Bills" actions={<button className="btn-primary" onClick={async () => setOpen((await api.post('/api/p/bills')).data._id)}><Camera size={16} /> Capture bill</button>} />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'suppliers' ? <Suppliers /> : (
        <>
          {tab === 'to_approve' && sel.length > 0 && <button className="btn-primary mb-3" onClick={async () => { await api.post('/api/p/bills/approve-bulk', { bill_ids: sel }); setSel([]); load() }}><Check size={16} /> Approve {sel.length}</button>}
          <div className="card divide-y">
            {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text="No bills." /> : rows.map((b) => (
              <div key={b._id} className="flex items-center gap-3 p-3 hover:bg-gray-50">
                {tab === 'to_approve' && <input type="checkbox" checked={sel.includes(b._id)} onChange={() => setSel(sel.includes(b._id) ? sel.filter((x) => x !== b._id) : [...sel, b._id])} />}
                <button className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left" onClick={() => setOpen(b._id)}>
                  <div className="min-w-0"><div className="font-semibold">{b.bill_number} · {b.supplier_name || 'No supplier yet'}</div>
                    <div className="text-xs text-gray-500">{b.invoice_number} · {date(b.invoice_date)} · {b.captured_by}{(b.flags || []).filter((f: any) => !f.cleared_at).map((f: any) => ` · ${f.code.replace(/_/g, ' ')}`).join('')}</div></div>
                  <div className="flex items-center gap-2"><span className="font-bold tabular-nums">{money(b.total_incl)}</span><Status s={b.status} /></div>
                </button>
              </div>
            ))}
          </div>
        </>
      )}
      {open && <BillEditor id={open} onClose={() => { setOpen(null); load() }} />}
    </div>
  )
}

function BillEditor({ id, onClose }: { id: string; onClose: () => void }) {
  const { isAdmin } = useApp()
  const [b, setB] = useState<any>(null)
  const [suppliers, setSuppliers] = useState<any[]>([])
  const [xero, setXero] = useState<any>(null)
  const [newSup, setNewSup] = useState('')
  const [extra, setExtra] = useState<ReactNode>(null)
  const a = useAction()
  const load = useCallback(() => api.get(`/api/p/bills/${id}`).then((r) => setB(r.data)), [id])
  useEffect(() => { load(); api.get('/api/p/bills/suppliers').then((r) => setSuppliers(r.data)); api.get('/api/p/xero').then((r) => setXero(r.data)) }, [load])
  if (!b) return null
  const editable = ['draft', 'queried'].includes(b.status) || (b.status === 'submitted' && isAdmin)
  const patch = (body: any) => a.run(async () => setB((await api.patch(`/api/p/bills/${id}`, body)).data), errMsg)
  const line = b.lines?.[0] || {}
  const accounts = xero?.reference?.accounts || []
  const taxes = xero?.reference?.tax_rates || []
  return (
    <Modal title={`${b.bill_number}${b.status !== 'draft' ? '' : ' (draft)'}`} onClose={onClose} wide>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2"><Status s={b.status} />{b.query_note && <span className="text-sm text-amber-700">Query: {b.query_note}</span>}{b.post_error && <span className="text-sm text-red-700">{b.post_error}</span>}
          {b.xero_url && <a className="text-sm text-brand-primary" href={b.xero_url} target="_blank" rel="noreferrer">Open in Xero</a>}</div>
        <div className="flex flex-wrap gap-2">
          {(b.documents || []).map((d: string, i: number) => <button key={d} className="btn-ghost btn-sm" onClick={() => openPdf(`/api/p/bills/files/${d}`)}>Document {i + 1}</button>)}
          {editable && <label className="btn-ghost btn-sm cursor-pointer"><Plus size={14} /> Photo or PDF
            <input type="file" accept="image/*,application/pdf" capture="environment" className="hidden" onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return
              const fd = new FormData(); fd.append('file', f)
              await a.run(async () => { await api.post(`/api/p/bills/${id}/upload`, fd); await load() }, errMsg)
            }} /></label>}
          {editable && (b.documents || []).length > 0 && <button className="btn-ghost btn-sm" disabled={a.busy} onClick={() => a.run(async () => setB((await api.post(`/api/p/bills/${id}/extract`)).data), errMsg)}><Sparkles size={14} /> Read document</button>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Supplier">
            <select className="input" disabled={!editable} value={b.supplier_id || ''} onChange={(e) => e.target.value === '__new' ? setNewSup(' ') : patch({ supplier_id: e.target.value })}>
              <option value="">Pick…</option>{suppliers.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}<option value="__new">Supplier not listed…</option>
            </select>
          </Field>
          {newSup && <Field label="New supplier name"><div className="flex gap-2"><input className="input" value={newSup.trim()} onChange={(e) => setNewSup(e.target.value || ' ')} />
            <button className="btn-ghost btn-sm" onClick={() => { patch({ new_supplier: { name: newSup.trim() } }); setNewSup('') }}>Add</button></div></Field>}
          <Field label="Invoice number"><input className="input" disabled={!editable} defaultValue={b.invoice_number || ''} onBlur={(e) => patch({ invoice_number: e.target.value })} /></Field>
          <Field label="Invoice date"><input type="date" className="input" disabled={!editable} defaultValue={b.invoice_date || ''} onBlur={(e) => patch({ invoice_date: e.target.value })} /></Field>
          <Field label="Total incl VAT"><input type="number" step="0.01" className="input" disabled={!editable} defaultValue={line.unit_amount ?? ''} onBlur={(e) => patch({ lines: [{ ...line, description: b.description || '', quantity: 1, unit_amount: Number(e.target.value) }], amounts_are: 'inclusive' })} /></Field>
          <Field label="What it is for"><input className="input" disabled={!editable} defaultValue={b.description || ''} onBlur={(e) => patch({ description: e.target.value })} /></Field>
          <Field label="Paid already?">
            <select className="input" disabled={!editable} value={b.payment_state || ''} onChange={(e) => patch({ payment_state: e.target.value })}>
              <option value="">Choose…</option><option value="unpaid">Not paid</option><option value="paid_cash">Paid cash</option><option value="paid_card">Paid card / EFT</option>
            </select>
          </Field>
          {(b.payment_state === 'paid_cash' || b.payment_state === 'paid_card') && <Field label="Paid date"><input type="date" className="input" disabled={!editable} defaultValue={b.paid_date || ''} onBlur={(e) => patch({ paid_date: e.target.value })} /></Field>}
          <Field label="Due date" hint={b.due_source ? `From ${b.due_source.replace(/_/g, ' ')}` : undefined}><input type="date" className="input" disabled={!editable} defaultValue={b.due_date || ''} onBlur={(e) => patch({ due_date: e.target.value })} /></Field>
          {isAdmin && accounts.length > 0 && <Field label="Account"><select className="input" value={line.account_code || ''} onChange={(e) => patch({ lines: [{ ...line, account_code: e.target.value }] })}><option value="">Choose…</option>{accounts.map((x: any) => <option key={x.code} value={x.code}>{x.code} {x.name}</option>)}</select></Field>}
          {isAdmin && taxes.length > 0 && <Field label="Tax"><select className="input" value={line.tax_type || ''} onChange={(e) => patch({ lines: [{ ...line, tax_type: e.target.value }] })}>{taxes.map((x: any) => <option key={x.tax_type} value={x.tax_type}>{x.name}</option>)}</select></Field>}
        </div>
        {b.extraction && <p className="text-xs text-gray-500">Read from the document; check the highlighted fields before submitting.</p>}
        <div className="flex flex-wrap gap-1.5">{(b.flags || []).map((f: any) => <span key={f.code} className="inline-flex items-center gap-1"><Chip tone={f.level === 'block' ? 'red' : f.cleared_at ? 'grey' : 'amber'}>{f.message}</Chip>
          {isAdmin && f.level === 'warn' && !f.cleared_at && <button className="text-xs underline" onClick={() => a.run(async () => setB((await api.post(`/api/p/bills/${id}/flags/${f.code}/clear`)).data), errMsg)}>clear</button>}</span>)}</div>
        <p className="text-sm">Total <b>{money(b.total_incl)}</b> · VAT {money(b.total_tax)}</p>
        <ErrorBox msg={a.error} />
        <div className="flex flex-wrap justify-end gap-2">
          {['draft', 'queried'].includes(b.status) && <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => setB((await api.post(`/api/p/bills/${id}/submit`)).data), errMsg)}>Submit</button>}
          {isAdmin && ['submitted', 'post_failed'].includes(b.status) && <>
            <button className="btn-ghost" onClick={() => setExtra(<Note title="Query to the capturer" onClose={() => setExtra(null)} onSave={(note) => api.post(`/api/p/bills/${id}/query`, { note }).then(() => { setExtra(null); load() })} />)}>Query</button>
            <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => setB((await api.post(`/api/p/bills/${id}/approve`)).data), errMsg)}>Approve{xero?.connected ? ' and post' : ''}</button>
          </>}
          {isAdmin && b.status !== 'void' && <button className="btn-danger" onClick={() => setExtra(<Note title="Void bill" onClose={() => setExtra(null)} onSave={(note) => api.post(`/api/p/bills/${id}/void`, { note }).then(() => { setExtra(null); load() })} />)}>Void</button>}
        </div>
      </div>
      {extra}
    </Modal>
  )
}

function Note({ title, onClose, onSave }: { title: string; onClose: () => void; onSave: (n: string) => Promise<unknown> }) {
  const [n, setN] = useState('')
  return <Modal title={title} onClose={onClose}><textarea className="input" rows={3} value={n} onChange={(e) => setN(e.target.value)} /><div className="mt-3 flex justify-end"><button className="btn-primary" disabled={!n.trim()} onClick={() => onSave(n)}>Save</button></div></Modal>
}

function Suppliers() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [edit, setEdit] = useState<any>(null)
  const load = () => api.get('/api/p/bills/suppliers').then((r) => setRows(r.data))
  useEffect(() => { load() }, [])
  return (
    <div>
      <button className="btn-ghost mb-3" onClick={() => setEdit({ name: '' })}><Plus size={16} /> Supplier</button>
      <div className="card divide-y">{rows === null ? <Spinner /> : rows.map((s) => (
        <button key={s._id} className="flex w-full justify-between p-3 text-left text-sm hover:bg-gray-50" onClick={() => setEdit(s)}><span className="font-semibold">{s.name}</span><span className="text-gray-500">{s.vat_number}{s.xero_contact_id ? ' · in Xero' : ''}</span></button>
      ))}</div>
      {edit && (
        <Modal title={edit._id ? edit.name : 'New supplier'} onClose={() => setEdit(null)}>
          <div className="grid gap-3 sm:grid-cols-2">
            {['name', 'vat_number', 'email', 'phone', 'default_account_code', 'default_tax_type'].map((k) => <Field key={k} label={k.replace(/_/g, ' ')}><input className="input" value={edit[k] || ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></Field>)}
            <Field label="Terms"><select className="input" value={edit.payment_terms_type || ''} onChange={(e) => setEdit({ ...edit, payment_terms_type: e.target.value || null })}><option value="">From Xero / 30 days</option><option value="on_receipt">On receipt</option><option value="days_from_invoice">Days from invoice</option><option value="days_from_statement">Days from statement</option></select></Field>
            <Field label="Days"><input type="number" className="input" value={edit.payment_terms_days || ''} onChange={(e) => setEdit({ ...edit, payment_terms_days: Number(e.target.value) })} /></Field>
          </div>
          <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={async () => { await api.put('/api/p/bills/suppliers', edit); setEdit(null); load() }}>Save</button></div>
        </Modal>
      )}
    </div>
  )
}
