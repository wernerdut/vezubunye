import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { Spinner } from '../components/ui'
import { money } from '../lib/format'

/** The customer's pay link: shows the amount due now and hands over to the card payment. */
export default function Pay() {
  const token = window.location.pathname.split('/pay/')[1]
  const done = new URLSearchParams(window.location.search).get('done')
  const [p, setP] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (!done) api.get(`/api/p/pay/${token}`).then((r) => setP(r.data)).catch((e) => setErr(errMsg(e))) }, [token, done])
  if (done) return <Box><h1 className="text-xl font-bold">Thank you</h1><p className="mt-2 text-gray-600">Your payment was received.</p></Box>
  if (err) return <Box><p className="text-gray-700">{err}</p></Box>
  if (!p) return <Spinner />
  return (
    <Box>
      <div className="text-sm text-gray-500">{p.business}</div>
      <div className="mt-1 text-lg font-semibold">Invoice {p.invoice}</div>
      {p.state === 'open' ? <>
        <div className="my-4 text-3xl font-bold">{money(p.amount)}</div>
        <a className="btn-primary w-full py-3" href={p.redirect_url}>Pay by card</a>
      </> : <p className="mt-4 text-gray-600">{p.state === 'paid' ? 'This invoice is paid. Thank you.' : 'This link is no longer active.'}</p>}
    </Box>
  )
}

function Box({ children }: { children: React.ReactNode }) {
  return <div className="gradient flex min-h-screen items-center justify-center p-4"><div className="w-full max-w-sm rounded-2xl bg-white p-6 text-center shadow-xl">{children}</div></div>
}
