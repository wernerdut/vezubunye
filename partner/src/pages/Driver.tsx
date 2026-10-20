import { CheckCircle2, Navigation, Phone, Play, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, errMsg } from '../api'
import { ErrorBox, Field, Spinner } from '../components/ui'
import { money } from '../lib/format'
import QR from '../components/QR'

/** The driver's page: one delivery, no login. Opened from the WhatsApp message. */
export default function Driver() {
  const token = useParams()['*'] || window.location.pathname.split('/d/')[1]
  const [v, setV] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState<'' | 'collect' | 'deliver' | 'fail' | 'qr'>('')
  const [mode, setMode] = useState<'cash' | 'machine' | 'none'>('cash')
  const [cash, setCash] = useState('')
  const [slip, setSlip] = useState('')
  const [reason, setReason] = useState('')
  const [receiver, setReceiver] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const load = useCallback(() => api.get(`/api/d/${token}`).then((r) => { setV(r.data); setErr(null) }).catch((e) => setErr(errMsg(e))), [token])
  useEffect(() => { load() }, [load])
  useEffect(() => {   // live while the customer pays by card
    if (step !== 'qr') return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [step, load])
  useEffect(() => { if (v?.collection && step === 'qr') setStep('') }, [v, step])
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setErr(null)
    try { const r = await fn(); if (r?.data) setV(r.data); setStep('') } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }
  const gps = (): Promise<{ lat?: number; lng?: number }> => new Promise((res) => {
    if (!navigator.geolocation) return res({})
    navigator.geolocation.getCurrentPosition((p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }), () => res({}), { timeout: 8000 })
  })
  if (err && !v) return <div className="p-8 text-center text-gray-600">{err}</div>
  if (!v) return <Spinner />
  const done = v.status === 'delivered'
  const needCollect = v.collect_amount && !v.collection
  return (
    <div className="mx-auto min-h-screen max-w-md bg-white">
      <header className="gradient p-4 text-white"><div className="text-sm opacity-90">{v.business.name}</div><div className="text-xl font-bold">{v.dn_number}</div></header>
      <div className="space-y-4 p-4">
        <div>
          <div className="text-lg font-bold">{v.customer}</div>
          <div className="text-gray-700">{[v.site.label, v.site.address, v.site.town].filter(Boolean).join(', ')}</div>
          {v.site.directions && <div className="mt-1 rounded-lg bg-amber-50 p-2 text-sm">{v.site.directions}</div>}
        </div>
        <div className="grid grid-cols-2 gap-2">
          {v.navigate && <a className="btn-primary py-3" href={v.navigate} target="_blank" rel="noreferrer"><Navigation size={18} /> Navigate</a>}
          {v.site.contact_phone && <a className="btn-ghost py-3" href={`tel:${v.site.contact_phone}`}><Phone size={18} /> {v.site.contact_name || 'Call'}</a>}
        </div>
        <div className="rounded-xl border p-3"><div className="label">Load</div>{v.load.map((l: any, i: number) => <div key={i} className="text-lg font-semibold">{l.qty} × {l.what}</div>)}</div>
        {v.collect_amount != null && (
          <div className="rounded-xl bg-violet-50 p-3 text-violet-900">
            <div className="text-sm font-semibold">Collect on delivery</div><div className="text-2xl font-bold">{money(v.collect_amount)}</div>
            {v.collection && <div className="mt-1 text-sm">{v.collection.method === 'not_collected' ? `Not collected: ${v.collection.reason}` : `Collected ${money(v.collection.amount)}`}</div>}
          </div>
        )}
        <ErrorBox msg={err} />
        {done ? <div className="flex items-center gap-2 rounded-xl bg-emerald-50 p-4 font-semibold text-emerald-800"><CheckCircle2 /> Delivered. Thank you.</div> : <>
          {v.status === 'scheduled' && <button className="btn-dark w-full py-3" disabled={busy} onClick={() => act(() => api.post(`/api/d/${token}/start`))}><Play size={18} /> Start</button>}
          {needCollect && step === '' && (
            <div className="grid grid-cols-2 gap-2">
              {v.cod_method !== 'yoco' && <button className="btn-ghost py-3" onClick={() => { setMode('cash'); setStep('collect') }}>Cash received</button>}
              {v.cod_method !== 'cash' && v.yoco && <button className="btn-ghost py-3" onClick={async () => { try { setQr((await api.post(`/api/d/${token}/pay-link`)).data.url); setStep('qr') } catch (e) { setErr(errMsg(e)) } }}>Card: show QR</button>}
              {v.cod_method !== 'cash' && <button className="btn-ghost py-3" onClick={() => { setMode('machine'); setStep('collect') }}>Paid on card machine</button>}
              <button className="btn-ghost py-3 text-red-700" onClick={() => { setMode('none'); setStep('collect') }}>Not collected</button>
            </div>
          )}
          {step === 'qr' && qr && <div className="rounded-xl border p-4 text-center"><QR text={qr} /><p className="mt-2 text-sm text-gray-600">Customer scans to pay {money(v.collect_amount)}. This updates when the payment lands.</p><button className="btn-ghost mt-2" onClick={() => setStep('')}>Back</button></div>}
          {step === 'collect' && (
            <div className="space-y-3 rounded-xl border p-3">
              {mode === 'cash' && <>
                <Field label="Cash counted"><input type="number" inputMode="decimal" className="input" value={cash} onChange={(e) => setCash(e.target.value)} placeholder={String(v.collect_amount)} /></Field>
                <button className="btn-primary w-full py-3" disabled={busy || !cash} onClick={() => act(() => api.post(`/api/d/${token}/collection`, { method: 'cash', amount: Number(cash) }))}>Save cash</button>
              </>}
              {mode === 'machine' && <>
                <Field label="Card machine slip number"><input className="input" value={slip} onChange={(e) => setSlip(e.target.value)} /></Field>
                <button className="btn-primary w-full py-3" disabled={busy || !slip} onClick={() => act(() => api.post(`/api/d/${token}/collection`, { method: 'yoco_machine', amount: v.collect_amount, reference: slip }))}>Paid {money(v.collect_amount)} on card machine</button>
              </>}
              {mode === 'none' && <>
                <Field label="Why was nothing collected?"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
                <button className="btn-danger w-full py-3" disabled={busy || !reason} onClick={() => act(() => api.post(`/api/d/${token}/collection`, { method: 'not_collected', reason }))}>Not collected</button>
              </>}
              <button className="w-full text-sm text-gray-500" onClick={() => setStep('')}>Back</button>
            </div>
          )}
          {!needCollect && step === '' && <button className="btn-primary w-full py-3" onClick={() => setStep('deliver')}><CheckCircle2 size={18} /> Delivered</button>}
          {step === 'deliver' && (
            <div className="space-y-3 rounded-xl border p-3">
              <Field label="Received by"><input className="input" value={receiver} onChange={(e) => setReceiver(e.target.value)} /></Field>
              <Field label="Photo of the signed delivery note"><input type="file" accept="image/*" capture="environment" className="input" onChange={(e) => setPhoto(e.target.files?.[0] || null)} /></Field>
              <button className="btn-primary w-full py-3" disabled={busy} onClick={() => act(async () => {
                const fd = new FormData(); fd.append('receiver_name', receiver)
                const g = await gps(); if (g.lat) { fd.append('lat', String(g.lat)); fd.append('lng', String(g.lng)) }
                if (photo) fd.append('file', photo)
                return api.post(`/api/d/${token}/delivered`, fd)
              })}>Confirm delivered</button>
            </div>
          )}
          {step === '' && <button className="w-full py-2 text-sm font-semibold text-red-700" onClick={() => setStep('fail')}><XCircle size={14} className="inline" /> Couldn't deliver</button>}
          {step === 'fail' && (
            <div className="space-y-3 rounded-xl border p-3">
              <Field label="What happened?"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
              <button className="btn-danger w-full" disabled={busy || !reason} onClick={() => act(() => api.post(`/api/d/${token}/failed`, { reason }))}>Couldn't deliver</button>
            </div>
          )}
        </>}
        {v.business.phone && <p className="pt-4 text-center text-xs text-gray-400">Office {v.business.phone}</p>}
      </div>
    </div>
  )
}
