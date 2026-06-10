import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { PowderEntry } from '../../types'
import type { TabProps } from '../NodePage'

export default function Powder({ nodeId, user }: TabProps) {
  const [entries, setEntries] = useState<PowderEntry[]>([])
  const [balance, setBalance] = useState(0)
  const [adjDate, setAdjDate] = useState(new Date().toISOString().slice(0, 10))
  const [adjKg, setAdjKg] = useState('')
  const [adjNotes, setAdjNotes] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/powder`).then((r) => {
      setEntries([...r.data.entries].reverse())
      setBalance(r.data.balance)
    })
  }, [nodeId])
  useEffect(load, [load])

  const adjust = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post(`/api/nodes/${nodeId}/powder/adjustment`, {
        date: adjDate, kg: parseFloat(adjKg), notes: adjNotes,
      })
      setAdjKg(''); setAdjNotes('')
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <SectionTitle>Powder Ledger</SectionTitle>
        <div className="text-right">
          <div className="font-headline text-3xl text-brand-blue">{balance.toLocaleString('en-ZA')} kg</div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">on hand</div>
        </div>
      </div>
      {(user.role === 'audit' || user.role === 'admin') && (
        <form onSubmit={adjust} className="card mb-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Adjustment date</label>
            <input className="input" type="date" value={adjDate} onChange={(e) => setAdjDate(e.target.value)} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">kg (+/-)</label>
            <input className="input w-28" type="number" step="0.1" value={adjKg} onChange={(e) => setAdjKg(e.target.value)} required />
          </div>
          <div className="flex-1 min-w-48">
            <label className="block text-xs font-semibold text-gray-600 mb-1">Reason</label>
            <input className="input" value={adjNotes} onChange={(e) => setAdjNotes(e.target.value)} required />
          </div>
          <button className="btn-secondary">Post adjustment</button>
          {error && <p className="text-sm text-brand-red w-full">{error}</p>}
        </form>
      )}
      <div className="card p-0 overflow-hidden">
        {entries.length === 0 ? (
          <Empty text="No powder movements yet" />
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Date</th>
                <th className="th">Type</th>
                <th className="th text-right">kg</th>
                <th className="th text-right">Running balance</th>
                <th className="th">Notes</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e._id}>
                  <td className="td">{e.date}</td>
                  <td className="td">
                    <span className={`font-semibold ${e.type === 'in' ? 'text-brand-green' : e.type === 'drawn' ? 'text-brand-blue' : 'text-brand-orange'}`}>
                      {e.type === 'in' ? 'received' : e.type === 'drawn' ? 'drawn' : 'adjustment'}
                    </span>
                  </td>
                  <td className="td text-right">{e.type === 'drawn' ? '−' : '+'}{Math.abs(e.kg).toLocaleString('en-ZA')}</td>
                  <td className="td text-right font-semibold">{e.running_balance.toLocaleString('en-ZA')}</td>
                  <td className="td text-gray-500">{e.notes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
