import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { api } from '../api'
import type { NodeConfig, NodeInfo, User } from '../types'
import Counts from './tabs/Counts'
import DailyCapture from './tabs/DailyCapture'
import Deliveries from './tabs/Deliveries'
import FinishedGoods from './tabs/FinishedGoods'
import FlagsTab from './tabs/FlagsTab'
import Invoices from './tabs/Invoices'
import Payments from './tabs/Payments'
import Powder from './tabs/Powder'
import Production from './tabs/Production'

const TABS = [
  { path: 'capture', label: 'Daily Capture' },
  { path: 'powder', label: 'Powder' },
  { path: 'production', label: 'Production' },
  { path: 'finished-goods', label: 'Finished Goods' },
  { path: 'deliveries', label: 'Deliveries' },
  { path: 'invoices', label: 'Invoices' },
  { path: 'payments', label: 'Payments' },
  { path: 'flags', label: 'Flags' },
  { path: 'counts', label: 'Counts' },
]

export interface TabProps {
  nodeId: string
  config: NodeConfig
  user: User
}

export default function NodePage({ user }: { user: User }) {
  const { nodeId = 'gogreen' } = useParams()
  const [node, setNode] = useState<NodeInfo | null>(null)
  const [config, setConfig] = useState<NodeConfig | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/api/nodes')
      .then((r) => setNode(r.data.find((n: NodeInfo) => n.node_id === nodeId) || null))
      .catch(() => setError('Could not load node'))
    api.get(`/api/nodes/${nodeId}/config`)
      .then((r) => setConfig(r.data))
      .catch(() => setError('Could not load node config'))
  }, [nodeId])

  if (error) return <p className="text-brand-red">{error}</p>
  if (!node || !config) return <p className="text-gray-400">Loading…</p>

  const props: TabProps = { nodeId, config, user }

  return (
    <div>
      <div className="flex items-baseline gap-3 mb-4">
        <h1 className="font-headline text-4xl text-brand-blue">{node.name}</h1>
        <span className="text-sm text-gray-500">{node.location}</span>
      </div>
      <nav className="flex flex-wrap gap-1 border-b border-gray-200 mb-5">
        {TABS.map((t) => (
          <NavLink
            key={t.path}
            to={t.path}
            className={({ isActive }) =>
              `px-3 py-2 text-sm font-semibold border-b-2 -mb-px ${
                isActive
                  ? 'border-brand-light text-brand-blue'
                  : 'border-transparent text-gray-500 hover:text-brand-blue'
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="capture" replace />} />
        <Route path="capture" element={<DailyCapture {...props} />} />
        <Route path="powder" element={<Powder {...props} />} />
        <Route path="production" element={<Production {...props} />} />
        <Route path="finished-goods" element={<FinishedGoods {...props} />} />
        <Route path="deliveries" element={<Deliveries {...props} />} />
        <Route path="invoices" element={<Invoices {...props} />} />
        <Route path="payments" element={<Payments {...props} />} />
        <Route path="flags" element={<FlagsTab {...props} />} />
        <Route path="counts" element={<Counts {...props} />} />
      </Routes>
    </div>
  )
}
