import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, openPdf } from '../api'
import { Empty, PageTitle, Spinner } from '../components/ui'
import { money } from '../lib/format'

export default function Debtors() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<any[] | null>(null)
  useEffect(() => { api.get('/api/p/debtors').then((r) => setRows(r.data)) }, [])
  if (!rows) return <Spinner />
  const sum = (k: string) => rows.reduce((s, r) => s + r[k], 0)
  return (
    <div>
      <PageTitle title="Debtors" sub={`${money(sum('balance'))} outstanding · ${money(sum('overdue'))} overdue`} />
      <div className="card overflow-x-auto">
        {rows.length === 0 ? <Empty text="Nobody owes anything." /> : (
          <table className="min-w-full">
            <thead className="border-b bg-gray-50"><tr>{['Customer', 'Current', '30', '60', '90+', 'Balance', 'Credit', 'COD not collected', ''].map((h) => <th key={h} className="th text-right first:text-left">{h}</th>)}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.customer_id} className={`border-b hover:bg-gray-50 ${r.overdue > 0 ? 'bg-red-50/40' : ''}`}>
                <td className="td"><button className="font-semibold text-brand-dark" onClick={() => navigate(`/customers/${r.customer_id}`)}>{r.name}</button></td>
                {['current', 'd30', 'd60', 'd90', 'balance', 'credit', 'cod_uncollected'].map((k) => <td key={k} className={`td text-right tabular-nums ${k === 'balance' ? 'font-bold' : ''} ${['d30', 'd60', 'd90'].includes(k) && r[k] > 0 ? 'text-red-700' : ''}`}>{r[k] ? money(r[k]) : ''}</td>)}
                <td className="td text-right"><button className="text-xs text-brand-primary underline" onClick={() => openPdf(`/api/p/customers/${r.customer_id}/statement.pdf`)}>Statement</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </div>
  )
}
