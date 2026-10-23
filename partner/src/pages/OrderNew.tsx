import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errMsg } from '../api'
import { ShareBar } from '../components/Actions'
import { cleanLines, CustomerPicker, DeliverTo, Line, LineEditor, siteIdFor } from '../components/Forms'
import { ErrorBox, Field, PageTitle, useAction } from '../components/ui'
import { useApp } from '../lib/ctx'

/** New order, or Quick sale: one screen makes the order, the invoice, the payment and the DN. */
export default function OrderNew({ quick }: { quick?: boolean }) {
  const navigate = useNavigate()
  const { me } = useApp()
  const [customer, setCustomer] = useState<any>(null)
  const [lines, setLines] = useState<Line[]>([{ kind: 'tank', qty: 1, grade: 'A' }])
  const [siteId, setSiteId] = useState('')
  const [newSite, setNewSite] = useState<any>(null)
  const [requested, setRequested] = useState('')
  const [notes, setNotes] = useState('')
  const [cod, setCod] = useState(false)
  const [codMethod, setCodMethod] = useState('either')
  const [pay, setPay] = useState<{ method: string; reference: string }>({ method: 'cash', reference: '' })
  const [link, setLink] = useState<string | null>(null)
  const a = useAction()
  const upfront = customer && customer.payment_basis !== 'terms'

  const save = () => a.run(async () => {
    if (quick) {
      const r = await api.post('/api/p/quick-sale', { customer_id: customer._id, lines: cleanLines(lines), notes, payment: pay })
      if (r.data.pay_link) setLink(r.data.pay_link)
      else navigate(`/orders/${r.data.order._id}`)
      return
    }
    const site_id = await siteIdFor(customer, siteId, newSite, (st) => { setCustomer({ ...customer, sites: [...(customer.sites || []), st] }); setSiteId(st.site_id); setNewSite(null) })
    const r = await api.post('/api/p/orders', {
      customer_id: customer._id, lines: cleanLines(lines), site_id, requested_date: requested || undefined,
      notes, pay_on_delivery: upfront && cod, cod_method: codMethod,
    })
    navigate(`/orders/${r.data._id}`)
  }, errMsg)

  if (link) {
    return (
      <div className="card mx-auto max-w-lg p-6 text-center">
        <h2 className="mb-2 text-lg font-bold">Card payment link</h2>
        <p className="mb-4 break-all text-sm text-gray-600">{link}</p>
        <div className="flex justify-center"><ShareBar message={`Pay by card: ${link}`} url={link} /></div>
        <p className="mt-4 text-xs text-gray-500">Release the tanks once the payment shows on the order.</p>
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-3xl">
      <PageTitle title={quick ? 'Quick sale' : 'New order'} sub={quick ? 'Walk-in at the factory: order, invoice, payment and delivery note in one go.' : undefined} />
      <div className="card space-y-4 p-4">
        <CustomerPicker value={customer} onChange={(c) => { setCustomer(c); setSiteId(''); setNewSite(null) }} />
        {customer && <>
          <LineEditor lines={lines} setLines={setLines} tier={customer.price_tier || 'retail'} />
          {!quick && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DeliverTo customer={customer} siteId={siteId} setSiteId={setSiteId} site={newSite} setSite={setNewSite} />
              <Field label="Requested date"><input type="date" className="input" value={requested} onChange={(e) => setRequested(e.target.value)} /></Field>
            </div>
          )}
          {!quick && upfront && (
            <div className="rounded-lg bg-gray-50 p-3">
              <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={cod} onChange={(e) => setCod(e.target.checked)} /> Pay on delivery</label>
              {cod && (
                <div className="mt-2 flex gap-2">
                  {['cash', 'yoco', 'either'].map((m) => <button key={m} className={codMethod === m ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setCodMethod(m)}>{m === 'yoco' ? 'Card' : m === 'either' ? 'Cash or card' : 'Cash'}</button>)}
                </div>
              )}
              {!cod && <p className="mt-1 text-xs text-gray-500">The invoice goes out now; tanks release up to what is paid.</p>}
            </div>
          )}
          {!quick && customer.payment_basis === 'terms' && <p className="text-sm text-sky-700">On terms: deliver first, one invoice per delivery.</p>}
          {quick && (
            <div className="rounded-lg bg-gray-50 p-3">
              <div className="label">Payment</div>
              <div className="flex flex-wrap gap-2">
                {['cash', 'yoco_machine', ...(me.features.yoco ? ['yoco_link'] : [])].map((m) => (
                  <button key={m} className={pay.method === m ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setPay({ ...pay, method: m })}>
                    {m === 'cash' ? 'Cash' : m === 'yoco_machine' ? 'Card machine' : 'Pay link on phone'}
                  </button>
                ))}
              </div>
              {pay.method === 'yoco_machine' && <input className="input mt-2" placeholder="Slip reference" value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} />}
            </div>
          )}
          <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        </>}
        <ErrorBox msg={a.error} />
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => navigate(-1)}>Cancel</button>
          <button className="btn-primary" disabled={!customer || a.busy} onClick={save}>{quick ? 'Complete sale' : 'Create order'}</button>
        </div>
      </div>
      <p className="mt-3 text-center text-xs text-gray-400">Prices lock when the order is created.</p>
    </div>
  )
}
