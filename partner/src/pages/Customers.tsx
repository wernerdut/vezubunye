import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CustomerForm, CustomerSearch } from '../components/Forms'
import { PageTitle } from '../components/ui'

export default function Customers() {
  const navigate = useNavigate()
  const [adding, setAdding] = useState(false)
  return (
    <div>
      <PageTitle title="Customers" actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> New customer</button>} />
      <div className="card p-3"><CustomerSearch onPick={(c) => navigate(`/customers/${c._id}`)} /></div>
      {adding && <CustomerForm onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); navigate(`/customers/${c._id}`) }} />}
    </div>
  )
}
