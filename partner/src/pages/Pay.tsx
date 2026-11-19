import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { Spinner } from '../components/ui'
import { money } from '../lib/format'

/** The customer's pay link: shows the amount due now and hands over to the card payment. On a
 * proforma the customer can instead accept the order and pay by card on delivery (19 Nov 2026). */
export default function Pay() {
  const token = window.location.pathname.split('/pay/')[1]
  const done = new URLSearchParams(window.location.search).get('done')
  const [p, setP] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!done) api.get(`/api/p/pay/${token}`).then((r) => setP(r.data)).catch((e) => setErr(errMsg(e))) }, [token, done])
  if (done) return <Box><h1 className="text-xl font-bold">Thank you</h1><p className="mt-2 text-gray-600">Your payment was received.</p></Box>
  if (err) return <Box><p className="text-gray-700">{err}</p></Box>
  if (!p) return <Spinner />
  const when = p.collect ? 'on collection' : 'on delivery'
  const accept = async () => {
    setBusy(true)
    try { setP((await api.post(`/api/p/pay/${token}/accept`)).data); setConfirming(false) } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }
  const acceptButton = p.accept_cod && <button className="btn-ghost mt-3 w-full py-3" onClick={() => setConfirming(true)}>Accept, pay by card {when}</button>
  if (p.state === 'accepted') {
    return (
      <Box>
        <div className="text-sm text-gray-500">{p.business}</div>
        <h1 className="mt-1 text-xl font-bold">Thank you, your order is confirmed</h1>
        <p className="mt-3 text-gray-700">Order {p.order}</p>
        {p.amount > 0 && <div className="my-3 text-3xl font-bold">{money(p.amount)}</div>}
        <p className="text-sm text-gray-600">Nothing to pay now. You pay by card {when}. We will contact you to arrange {p.collect ? 'the collection' : 'the delivery'}.</p>
      </Box>
    )
  }
  if (confirming) {
    return (
      <Box>
        <div className="text-sm text-gray-500">{p.business}</div>
        <div className="mt-1 text-lg font-semibold">Confirm your order</div>
        <div className="my-4 text-3xl font-bold">{money(p.amount)}</div>
        <p className="text-sm text-gray-600">You accept proforma {p.invoice} and pay {money(p.amount)} by card {when}. Nothing is charged now.</p>
        <button className="btn-primary mt-4 w-full py-3" disabled={busy} onClick={accept}>{busy ? 'One moment…' : 'Confirm order'}</button>
        <button className="btn-ghost mt-2 w-full py-3" disabled={busy} onClick={() => setConfirming(false)}>Back</button>
      </Box>
    )
  }
  return (
    <Box>
      <div className="text-sm text-gray-500">{p.business}</div>
      <div className="mt-1 text-lg font-semibold">{p.kind === 'proforma' ? 'Proforma' : 'Invoice'} {p.invoice}</div>
      {p.state === 'open' ? <>
        <div className="my-4 text-3xl font-bold">{money(p.amount)}</div>
        <a className="btn-primary w-full py-3" href={p.redirect_url}>{p.accept_cod ? 'Pay by card now' : 'Pay by card'}</a>
        {acceptButton}
      </> : p.state === 'eft' ? <>
        <div className="my-4 text-3xl font-bold">{money(p.amount)}</div>
        <p className="text-sm text-gray-600">Card payments aren't available right now. Please pay by EFT{p.bank?.length ? ' to:' : '.'}</p>
        {p.bank?.length > 0 && <div className="mt-3 rounded-lg bg-gray-50 p-3 text-left text-sm">{p.bank.map((l: string, i: number) => <div key={i}>{l}</div>)}</div>}
        <p className="mt-3 text-sm font-semibold">Use {p.reference} as your payment reference.</p>
        {acceptButton}
      </> : <p className="mt-4 text-gray-600">{p.state === 'paid' ? 'This invoice is paid. Thank you.' : 'This link is no longer active.'}</p>}
    </Box>
  )
}

function Box({ children }: { children: React.ReactNode }) {
  return <div className="gradient flex min-h-screen items-center justify-center p-4"><div className="w-full max-w-sm rounded-2xl bg-white p-6 text-center shadow-xl">{children}</div></div>
}
