import { Plus } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { RecordPayment } from '../components/Actions'
import { CustomerPicker } from '../components/Forms'
import { Empty, ErrorBox, Field, Modal, PageTitle, Spinner, Tabs, useAction } from '../components/ui'
import { useApp } from '../lib/ctx'
import { date, label, money, today } from '../lib/format'

export default function Payments() {
  const { isAdmin, cfg } = useApp()
  const [tab, setTab] = useState<'all' | 'cash'>('all')
  const [method, setMethod] = useState('')
  const [rows, setRows] = useState<any[] | null>(null)
  const [cash, setCash] = useState<any>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const load = useCallback(() => {
    api.get('/api/p/payments', { params: { method: method || undefined } }).then((r) => setRows(r.data))
    api.get('/api/p/cash').then((r) => setCash(r.data))
  }, [method])
  useEffect(() => { load() }, [load])
  const close = () => { setModal(null); load() }
  return (
    <div>
      <PageTitle title="Payments" actions={<button className="btn-primary" onClick={() => setModal(<Capture onClose={close} />)}><Plus size={16} /> Record payment</button>} />
      <Tabs tabs={[{ key: 'all', label: 'Receipts' }, { key: 'cash', label: 'Cash on hand' }]} value={tab} onChange={setTab} />
      {tab === 'all' && <>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {['', 'yoco', 'cash', 'eft'].map((m) => <button key={m} onClick={() => setMethod(m)} className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ${method === m ? 'bg-brand-dark text-white ring-brand-dark' : 'bg-white ring-gray-300'}`}>{m ? label(m) : 'All'}</button>)}
        </div>
        <div className="card divide-y">
          {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text="No payments." /> : rows.map((p) => (
            <div key={p._id} className={`flex items-center justify-between gap-3 p-3 ${p.void ? 'opacity-50 line-through' : ''}`}>
              <div className="min-w-0 text-sm">
                <div className="font-semibold">{p.customer_name || 'Unknown customer'} · {label(p.method + (p.source ? '_' + p.source : ''))}</div>
                <div className="text-xs text-gray-500">{date(p.date)}{p.reference ? ` · ${p.reference}` : ''} · {p.allocations.map((a: any) => `${a.invoice_number} ${money(a.amount)}`).join(', ') || 'unallocated'}
                  {p.unallocated > 0.004 ? ` · credit ${money(p.unallocated)}` : ''}{p.holder ? ` · held by ${p.holder.name}` : ''}{p.deposit_id ? ' · banked' : ''}</div>
              </div>
              <div className="font-bold tabular-nums">{money(p.amount)}</div>
            </div>
          ))}
        </div>
      </>}
      {tab === 'cash' && cash && (
        <div className="space-y-4">
          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between"><h2 className="font-bold">Not yet banked</h2>
              {cash.receipts.length > 0 && <button className="btn-primary btn-sm" onClick={() => setModal(<BankCash receipts={cash.receipts} onClose={close} />)}>Bank cash</button>}</div>
            <p className="mb-2 text-xs text-gray-500">Bank within {cfg.cash_banking_days} days. Older receipts are flagged.</p>
            {cash.on_hand.map((h: any, i: number) => <div key={i} className="flex justify-between text-sm"><span>{h.holder.name} ({h.holder.type}) · {h.receipts} receipts since {date(h.oldest)}</span>{isAdmin && <b>{money(h.amount)}</b>}</div>)}
            {cash.on_hand.length === 0 && <p className="text-sm text-gray-400">All cash is banked.</p>}
          </div>
          <div className="card">
            <h2 className="border-b p-3 font-bold">Deposits</h2>
            {cash.deposits.length === 0 && <p className="p-3 text-sm text-gray-400">None yet.</p>}
            {cash.deposits.map((d: any) => <div key={d._id} className="flex justify-between border-b p-3 text-sm"><span>{date(d.date)} · {d.reference} · {d.payment_ids.length} receipts{Math.abs(d.amount - d.receipts_total) > 0.004 ? <b className="text-red-700"> · receipts {money(d.receipts_total)}</b> : ''}</span><b>{money(d.amount)}</b></div>)}
          </div>
        </div>
      )}
      {modal}
    </div>
  )
}

function Capture({ onClose }: { onClose: () => void }) {
  const [c, setC] = useState<any>(null)
  if (c) return <RecordPayment customerId={c._id} onClose={onClose} onDone={onClose} />
  return <Modal title="Who paid?" onClose={onClose}><CustomerPicker value={null} onChange={setC} /></Modal>
}

function BankCash({ receipts, onClose }: { receipts: any[]; onClose: () => void }) {
  const [sel, setSel] = useState<string[]>(receipts.map((r) => r._id))
  const covered = receipts.filter((r) => sel.includes(r._id)).reduce((s, r) => s + r.amount, 0)
  const [amount, setAmount] = useState<number>(covered)
  const [ref, setRef] = useState('')
  const [d, setD] = useState(today())
  const [slip, setSlip] = useState<File | null>(null)
  const a = useAction()
  const save = () => a.run(async () => {
    let slip_file_id
    if (slip) { const fd = new FormData(); fd.append('file', slip); slip_file_id = (await api.post('/api/p/cash/deposits/slip', fd)).data.slip_file_id }
    await api.post('/api/p/cash/deposits', { payment_ids: sel, amount, reference: ref, date: d, slip_file_id })
    onClose()
  }, errMsg)
  return (
    <Modal title="Bank cash" onClose={onClose}>
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {receipts.map((r) => <label key={r._id} className="flex items-center justify-between gap-2 text-sm">
          <span><input type="checkbox" className="mr-2" checked={sel.includes(r._id)} onChange={() => setSel(sel.includes(r._id) ? sel.filter((x) => x !== r._id) : [...sel, r._id])} />{date(r.date)} · {r.holder?.name}</span><b>{money(r.amount)}</b></label>)}
      </div>
      <p className="my-2 text-sm">Selected receipts: <b>{money(covered)}</b></p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Deposited"><input type="number" step="0.01" className="input" value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></Field>
        <Field label="Date"><input type="date" className="input" value={d} onChange={(e) => setD(e.target.value)} /></Field>
        <Field label="Slip reference"><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label="Slip photo"><input type="file" accept="image/*" capture="environment" className="input" onChange={(e) => setSlip(e.target.files?.[0] || null)} /></Field>
      </div>
      {Math.abs(amount - covered) > 0.004 && <p className="mt-2 text-xs text-red-700">The deposit differs from the receipts by {money(amount - covered)}. It will be flagged.</p>}
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end"><button className="btn-primary" disabled={!sel.length || a.busy} onClick={save}>Save deposit</button></div>
    </Modal>
  )
}
