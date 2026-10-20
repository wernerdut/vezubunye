import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { CustomerForm } from '../components/Forms'
import { Chip, Empty, PageTitle, Spinner } from '../components/ui'
import { label, money } from '../lib/format'

export default function Customers() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<any[] | null>(null)
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  useEffect(() => { api.get('/api/p/customers').then((r) => setRows(r.data)) }, [adding])
  const shown = (rows || []).filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase()) || (c.phone || '').includes(q))
  return (
    <div>
      <PageTitle title="Customers" actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> New customer</button>} />
      <input className="input mb-3" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="card divide-y">
        {rows === null ? <Spinner /> : shown.length === 0 ? <Empty text="No customers." /> : shown.map((c) => (
          <button key={c._id} onClick={() => navigate(`/customers/${c._id}`)} className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-gray-50">
            <div className="min-w-0"><div className="font-semibold">{c.name}</div>
              <div className="text-xs text-gray-500">{c.phone}{c.sites?.length ? ` · ${c.sites.length} site${c.sites.length > 1 ? 's' : ''}` : ''}</div></div>
            <div className="flex items-center gap-2">
              {c.payment_basis === 'terms' && <Chip tone="blue">Terms</Chip>}{c.price_tier !== 'retail' && <Chip>{label(c.price_tier)}</Chip>}
              {c.balance > 0 && <span className="text-sm font-semibold tabular-nums">{money(c.balance)}</span>}
            </div>
          </button>
        ))}
      </div>
      {adding && <CustomerForm onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); navigate(`/customers/${c._id}`) }} />}
    </div>
  )
}
