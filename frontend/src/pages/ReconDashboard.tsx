import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { api, errMsg } from '../api'
import { SectionTitle, StatusBadge } from '../components/ui'
import type { ReconData, User } from '../types'

const NODE_ID = 'gogreen'

export default function ReconDashboard({ user }: { user: User }) {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7))
  const [data, setData] = useState<ReconData | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  const canAct = user.role === 'audit' || user.role === 'admin'

  const load = useCallback(() => {
    api.get(`/api/nodes/${NODE_ID}/recon?month=${month}`).then((r) => setData(r.data)).catch((e) => setError(errMsg(e)))
  }, [month])
  useEffect(load, [load])

  const sweep = async () => {
    await api.post(`/api/nodes/${NODE_ID}/recon/sweep`)
    load()
  }

  const resolve = async (id: string) => {
    setError('')
    try {
      await api.post(`/api/flags/${id}/resolve`, { resolution_note: notes[id] || '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  if (!data) return <p className="text-gray-400">{error || 'Loading…'}</p>

  const dayColor: Record<string, string> = {
    clear: 'bg-brand-green',
    flagged: 'bg-brand-red',
    captured: 'bg-brand-orange',
    no_capture: 'bg-gray-200',
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <h1 className="font-headline text-4xl text-brand-blue">Reconciliation</h1>
        <input className="input w-40" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        {canAct && (
          <button className="btn-secondary flex items-center gap-2" onClick={sweep} title="Check delivery notes without invoices, unpaid invoices past terms, unmatched payments">
            <RefreshCw size={14} /> Run checks
          </button>
        )}
      </div>

      <div className="card mb-6">
        <div className="text-sm font-semibold text-gray-600 mb-2">GoGreen — day by day</div>
        <div className="flex flex-wrap gap-1.5">
          {data.days.map((d) => (
            <div key={d.date} className="text-center" title={`${d.date}: ${d.status.replace('_', ' ')}`}>
              <div className={`w-9 h-9 rounded flex items-center justify-center text-xs font-bold text-white ${dayColor[d.status]}`}>
                {parseInt(d.date.slice(8))}
              </div>
            </div>
          ))}
        </div>
        <div className="flex gap-4 mt-3 text-xs text-gray-500">
          <span><span className="inline-block w-3 h-3 rounded bg-brand-green mr-1 align-middle" />clear</span>
          <span><span className="inline-block w-3 h-3 rounded bg-brand-red mr-1 align-middle" />flagged</span>
          <span><span className="inline-block w-3 h-3 rounded bg-brand-orange mr-1 align-middle" />captured, unresolved</span>
          <span><span className="inline-block w-3 h-3 rounded bg-gray-200 mr-1 align-middle" />no capture</span>
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <div>
          <SectionTitle>Open Flags ({data.open_flags.length})</SectionTitle>
          {error && <p className="text-sm text-brand-red mb-2">{error}</p>}
          <div className="space-y-3">
            {data.open_flags.length === 0 && (
              <div className="card border-brand-green text-brand-green font-semibold text-sm">Nothing open. The chain is clean.</div>
            )}
            {data.open_flags.map((f) => (
              <div key={f._id} className="card border-brand-red">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-brand-red">{f.type.replace(/_/g, ' ')}</span>
                  <span className="text-xs text-gray-400 ml-auto">{f.date_raised}</span>
                </div>
                <p className="text-sm mb-2">{f.description}</p>
                {canAct && (
                  <div className="flex gap-2">
                    <input className="input flex-1" placeholder="Resolution note (required)" value={notes[f._id] || ''}
                           onChange={(e) => setNotes({ ...notes, [f._id]: e.target.value })} />
                    <button className="btn-primary" disabled={!(notes[f._id] || '').trim()} onClick={() => resolve(f._id)}>Resolve</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="space-y-6">
          <div>
            <SectionTitle>Unmatched Payments ({data.unmatched_payments.length})</SectionTitle>
            <div className="card p-0 overflow-hidden">
              {data.unmatched_payments.length === 0 ? (
                <p className="text-sm text-gray-400 p-4">All payments matched.</p>
              ) : (
                <table className="w-full">
                  <thead><tr><th className="th">Date</th><th className="th text-right">Amount</th><th className="th">Reference</th></tr></thead>
                  <tbody>
                    {data.unmatched_payments.map((p) => (
                      <tr key={p._id}>
                        <td className="td">{p.date}</td>
                        <td className="td text-right font-semibold">R {p.amount.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</td>
                        <td className="td text-gray-500">{p.bank_reference}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <Link to={`/node/${NODE_ID}/payments`} className="text-sm text-brand-light font-semibold">Go match payments →</Link>
          </div>
          <div>
            <SectionTitle>Unpaid Invoices ({data.unpaid_invoices.length})</SectionTitle>
            <div className="card p-0 overflow-hidden">
              {data.unpaid_invoices.length === 0 ? (
                <p className="text-sm text-gray-400 p-4">Every invoice is paid.</p>
              ) : (
                <table className="w-full">
                  <thead><tr><th className="th">Number</th><th className="th">Client</th><th className="th text-right">Total</th><th className="th">Status</th></tr></thead>
                  <tbody>
                    {data.unpaid_invoices.map((i) => (
                      <tr key={i._id}>
                        <td className="td font-semibold">{i.invoice_number}</td>
                        <td className="td">{i.client_name}</td>
                        <td className="td text-right">R {i.total.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}</td>
                        <td className="td"><StatusBadge status={i.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
