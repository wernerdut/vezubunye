import { Camera, FileDown, MapPin, Navigation } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, errMsg, openPdf } from '../api'
import { sendToDriver, ShareModal } from '../components/Actions'
import { ErrorBox, Field, Modal, PageTitle, Spinner, Status, useAction } from '../components/ui'
import { lineLabel, useApp } from '../lib/ctx'
import { date, money, today } from '../lib/format'
import { Reason } from './OrderDetail'

export default function DeliveryDetail() {
  const { id } = useParams()
  const { cfg, isAdmin } = useApp()
  const navigate = useNavigate()
  const [d, setD] = useState<any>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const a = useAction()
  const load = useCallback(() => api.get(`/api/p/deliveries/${id}`).then((r) => setD(r.data)), [id])
  useEffect(() => { load() }, [load])
  if (!d) return <Spinner />
  const close = () => { setModal(null); load() }
  const status = (s: string, extra: any = {}) => a.run(async () => { await api.post(`/api/p/deliveries/${id}/status`, { status: s, ...extra }); load() }, errMsg)
  const site = d.site || {}
  const live = !d.void && d.delivery_status !== 'delivered'
  return (
    <div>
      <PageTitle title={d.dn_number} sub={<span className="flex flex-wrap items-center gap-2">
        {d.client_name} <Status s={d.void ? 'void' : d.delivery_status} />
        {d.order_id && <button className="text-brand-primary" onClick={() => navigate(`/orders/${d.order_id}`)}>Order {d.order_number}</button>}</span>}
        actions={<>
          <button className="btn-ghost" onClick={() => openPdf(`/api/p/deliveries/${id}/pdf`)}><FileDown size={16} /> PDF</button>
          {live && <button className="btn-primary" onClick={() => a.run(async () => setModal(<ShareModal title="Send to driver" data={await sendToDriver(d._id)} onClose={close} />), errMsg)}>Send to driver</button>}
        </>} />
      <ErrorBox msg={a.error} />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <div className="label">Load</div>
          {d.lines.map((l: any, i: number) => <div key={i} className="text-sm">{l.quantity} × {lineLabel(cfg, { ...l, kind: 'tank' })}</div>)}
          {d.collect_amount != null && <div className="mt-3 rounded-lg bg-violet-50 p-2 text-sm font-semibold text-violet-800">Collect {money(d.collect_amount)} on delivery
            {d.collection && <span className="block font-normal">{d.collection.method === 'not_collected' ? `Not collected: ${d.collection.reason}` : `Collected ${money(d.collection.amount)} (${d.collection.method.replace('_', ' ')})`}</span>}</div>}
          <div className="mt-3 text-sm text-gray-600">Planned {date(d.planned_date)}{d.driver?.name ? ` · ${d.driver.name} ${d.driver.phone}` : ''}{d.vehicle_reg ? ` · ${d.vehicle_reg}` : ''}</div>
          {d.failed_reason && <div className="mt-2 text-sm text-red-700">Failed: {d.failed_reason}</div>}
          {d.release && <div className="mt-2 text-sm text-amber-700">Released unpaid by {d.release.by}: {d.release.reason}</div>}
        </div>
        <div className="card p-4">
          <div className="label">Site</div>
          <div className="text-sm font-semibold">{site.label}</div>
          <div className="text-sm text-gray-600">{[site.address, site.town].filter(Boolean).join(', ')}</div>
          {(site.contact_name || site.contact_phone) && <div className="mt-1 text-sm">Contact: {site.contact_name} {site.contact_phone && <a className="text-brand-primary" href={`tel:${site.contact_phone}`}>{site.contact_phone}</a>}</div>}
          {site.directions && <div className="mt-1 text-sm text-gray-600">{site.directions}</div>}
          <div className="mt-2 flex gap-2">
            {d.nav_link && <a className="btn-ghost btn-sm" href={d.nav_link} target="_blank" rel="noreferrer"><Navigation size={14} /> Navigate</a>}
            {!site.lat && <span className="text-xs text-amber-700"><MapPin size={12} className="inline" /> Location not pinned</span>}
          </div>
        </div>
      </div>
      {d.pod && (
        <div className="card mt-4 p-4 text-sm">
          <div className="label">Proof of delivery</div>
          Received by {d.pod.receiver_name || '—'} · {new Date(d.pod.at).toLocaleString('en-ZA')}{d.pod.lat ? ` · GPS ${d.pod.lat}, ${d.pod.lng}` : ''}
          {d.pod.photo_file_id && <button className="ml-2 text-brand-primary underline" onClick={() => openPdf(`/api/p/deliveries/${id}/pod/photo`)}>Photo</button>}
        </div>
      )}
      {live && (
        <div className="mt-4 flex flex-wrap gap-2">
          {d.delivery_status === 'scheduled' && <button className="btn-ghost" onClick={() => status('out_for_delivery')}>Mark out for delivery</button>}
          {d.collect_amount != null && !d.collection && <button className="btn-ghost" onClick={() => setModal(<Collect dnId={d._id} amount={d.collect_amount} onClose={close} />)}>Record collection</button>}
          {d.delivery_status !== 'failed' && <button className="btn-primary" onClick={() => setModal(<Pod dnId={d._id} onClose={close} />)}><Camera size={16} /> Proof of delivery</button>}
          {d.delivery_status !== 'failed' && <button className="btn-danger" onClick={() => setModal(<Reason title="Couldn't deliver" onClose={() => setModal(null)} cta="Mark failed" onSave={(reason) => api.post(`/api/p/deliveries/${id}/status`, { status: 'failed', reason }).then(close)} />)}>Couldn't deliver</button>}
          {d.delivery_status === 'failed' && <button className="btn-primary" onClick={() => setModal(<Reschedule dnId={d._id} onClose={close} />)}>Reschedule</button>}
          {(d.delivery_status === 'scheduled' || d.delivery_status === 'failed' || isAdmin) && !d.legacy &&
            <button className="btn-danger" onClick={() => setModal(<Reason title="Void delivery (tanks return to stock)" onClose={() => setModal(null)} cta="Void" onSave={(reason) => api.post(`/api/p/deliveries/${id}/void`, { reason }).then(close)} />)}>Void</button>}
        </div>
      )}
      {d.delivery_status === 'delivered' && !d.pod && <button className="btn-ghost mt-4" onClick={() => setModal(<Pod dnId={d._id} onClose={close} />)}>Upload POD</button>}
      {modal}
    </div>
  )
}

function Pod({ dnId, onClose }: { dnId: string; onClose: () => void }) {
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const a = useAction()
  const save = () => a.run(async () => {
    const fd = new FormData()
    fd.append('receiver_name', name)
    if (file) fd.append('file', file)
    await api.post(`/api/p/deliveries/${dnId}/pod`, fd)
    onClose()
  }, errMsg)
  return (
    <Modal title="Proof of delivery" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Received by"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Photo of the signed delivery note"><input type="file" accept="image/*" capture="environment" className="input" onChange={(e) => setFile(e.target.files?.[0] || null)} /></Field>
        <ErrorBox msg={a.error} />
        <div className="flex justify-end"><button className="btn-primary" disabled={a.busy} onClick={save}>Mark delivered</button></div>
      </div>
    </Modal>
  )
}

function Collect({ dnId, amount, onClose }: { dnId: string; amount: number; onClose: () => void }) {
  const [method, setMethod] = useState('cash')
  const [amt, setAmt] = useState(amount)
  const [ref, setRef] = useState('')
  const [reason, setReason] = useState('')
  const a = useAction()
  return (
    <Modal title="Collection on delivery" onClose={onClose}>
      <div className="space-y-3">
        <div className="flex gap-2">{[['cash', 'Cash'], ['yoco_machine', 'Card machine'], ['not_collected', 'Not collected']].map(([k, l]) =>
          <button key={k} className={method === k ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setMethod(k)}>{l}</button>)}</div>
        {method !== 'not_collected' ? <>
          <Field label="Amount"><input type="number" className="input" value={amt} onChange={(e) => setAmt(Number(e.target.value))} /></Field>
          {method === 'yoco_machine' && <Field label="Slip reference"><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>}
        </> : <Field label="Why"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
        <ErrorBox msg={a.error} />
        <div className="flex justify-end"><button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => {
          await api.post(`/api/p/deliveries/${dnId}/collection`, { method, amount: amt, reference: ref, reason }); onClose()
        }, errMsg)}>Save</button></div>
      </div>
    </Modal>
  )
}

function Reschedule({ dnId, onClose }: { dnId: string; onClose: () => void }) {
  const [d, setD] = useState(today())
  const a = useAction()
  return (
    <Modal title="Reschedule" onClose={onClose}>
      <Field label="New date"><input type="date" className="input" value={d} onChange={(e) => setD(e.target.value)} /></Field>
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end"><button className="btn-primary" onClick={() => a.run(async () => { await api.post(`/api/p/deliveries/${dnId}/status`, { status: 'scheduled', planned_date: d }); onClose() }, errMsg)}>Save</button></div>
    </Modal>
  )
}
