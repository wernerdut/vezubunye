import { FileDown, MapPin, Pencil, Plus } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, openPdf } from '../api'
import { RecordPayment } from '../components/Actions'
import { CustomerForm, MapPreview, SiteModal } from '../components/Forms'
import { Chip, PageTitle, Spinner, Status } from '../components/ui'
import { date, label, money } from '../lib/format'

export default function CustomerDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [c, setC] = useState<any>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const load = useCallback(() => api.get(`/api/p/customers/${id}`).then((r) => setC(r.data)), [id])
  useEffect(() => { load() }, [load])
  if (!c) return <Spinner />
  const close = () => { setModal(null); load() }
  const a = c.aging || { current: 0, d30: 0, d60: 0, d90: 0, balance: 0, credit: 0 }
  return (
    <div>
      <PageTitle title={c.name} sub={<span className="flex flex-wrap gap-2">{c.ref && <Chip>{c.ref}</Chip>}{c.type_check && <Chip tone="amber">{c.type_check}</Chip>}<Chip tone={c.payment_basis === 'terms' ? 'blue' : 'grey'}>{label(c.payment_basis)}{c.payment_basis === 'terms' ? ` ${c.payment_terms_days}d` : ''}</Chip><Chip>{label(c.price_tier)}</Chip>{c.phone} {c.email}</span>}
        actions={<>
          <button className="btn-ghost" onClick={() => setModal(<CustomerForm initial={c} onClose={() => setModal(null)} onSaved={close} />)}><Pencil size={16} /> Edit</button>
          <button className="btn-ghost" onClick={() => openPdf(`/api/p/customers/${id}/statement.pdf`)}><FileDown size={16} /> Statement</button>
          <button className="btn-primary" onClick={() => navigate('/orders/new')}>New order</button>
        </>} />
      <div className="mb-4 grid gap-3 sm:grid-cols-6">
        {[['Balance', a.balance], ['Current', a.current], ['30', a.d30], ['60', a.d60], ['90+', a.d90], ['Credit', a.credit]].map(([l, v]) => (
          <div key={l as string} className="card p-3"><div className="label">{l}</div><div className="font-bold tabular-nums">{money(v as number)}</div></div>
        ))}
      </div>
      {a.balance > 0 && <button className="btn-ghost mb-4" onClick={() => setModal(<RecordPayment customerId={c._id} onClose={() => setModal(null)} onDone={close} />)}>Record payment</button>}

      <div className="card mb-4">
        <div className="flex items-center justify-between border-b p-3"><h2 className="font-bold">Delivery sites</h2><button className="btn-ghost btn-sm" onClick={() => setModal(<SiteModal customerId={c._id} onClose={() => setModal(null)} onSaved={close} />)}><Plus size={14} /> Add site</button></div>
        <div className="grid gap-3 p-3 md:grid-cols-2">
          {(c.sites || []).map((s: any) => (
            <div key={s.site_id} className="rounded-lg border p-3">
              <div className="flex items-start justify-between"><div><div className="font-semibold">{s.label}</div><div className="text-sm text-gray-600">{[s.address, s.town].filter(Boolean).join(', ')}</div>
                {s.contact_name && <div className="text-sm">{s.contact_name} {s.contact_phone}</div>}{s.directions && <div className="text-xs text-gray-500">{s.directions}</div>}</div>
                <button className="p-1 text-gray-400" onClick={() => setModal(<SiteModal customerId={c._id} initial={s} onClose={() => setModal(null)} onSaved={close} />)}><Pencil size={14} /></button></div>
              {s.lat != null ? <div className="mt-2"><MapPreview lat={s.lat} lng={s.lng} /></div> : <div className="mt-2 text-xs text-amber-700"><MapPin size={12} className="inline" /> Location not pinned</div>}
            </div>
          ))}
          {(c.sites || []).length === 0 && <p className="text-sm text-gray-400">No sites yet.</p>}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <List title="Orders" rows={c.orders} render={(o: any) => <button className="flex w-full justify-between text-left" onClick={() => navigate(`/orders/${o._id}`)}><span>{o.order_number} · {date(o.date)} · {money(o.value)}</span><span className="flex gap-1"><Status s={o.payment_status} /><Status s={o.delivery_status} /></span></button>} />
        <List title="Invoices" rows={c.invoices} render={(i: any) => <button className="flex w-full justify-between text-left" onClick={() => navigate(`/invoices/${i._id}`)}><span>{i.number} · {date(i.date)} · {money(i.total)}</span><Status s={i.status} /></button>} />
        <List title="Quotes" rows={c.quotes} render={(q: any) => <button className="flex w-full justify-between text-left" onClick={() => navigate(`/quotes/${q._id}`)}><span>{q.quote_number} · {money(q.total)}</span><Status s={q.status} /></button>} />
        <List title="Payments" rows={c.payments} render={(p: any) => <div className="flex justify-between"><span>{date(p.date)} · {label(p.method)}</span><b>{money(p.amount)}</b></div>} />
        <List title="Deliveries" rows={c.deliveries} render={(d: any) => <button className="flex w-full justify-between text-left" onClick={() => navigate(`/deliveries/${d._id}`)}><span>{d.dn_number} · {date(d.planned_date)}</span><Status s={d.delivery_status} /></button>} />
      </div>
      {modal}
    </div>
  )
}

function List({ title, rows, render }: { title: string; rows: any[]; render: (r: any) => ReactNode }) {
  return (
    <div className="card">
      <h2 className="border-b p-3 font-bold">{title}</h2>
      {rows.length === 0 ? <p className="p-3 text-sm text-gray-400">None.</p> : rows.slice(0, 20).map((r, i) => <div key={i} className="border-b px-3 py-2 text-sm">{render(r)}</div>)}
    </div>
  )
}
