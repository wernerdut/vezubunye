import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { api, errMsg } from '../api'
import type { NetworkDashboardData, User } from '../types'

const kg = (n: number) => n.toLocaleString('en-ZA', { maximumFractionDigits: 0 })
const rand = (n: number) => `R ${n.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`

export default function NetworkDashboard({ user }: { user: User }) {
  const [data, setData] = useState<NetworkDashboardData | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/api/dashboard/network').then((r) => setData(r.data)).catch((e) => setError(errMsg(e)))
  }, [])

  if (error) return <p className="text-brand-red">{error}</p>
  if (!data) return <p className="text-gray-400">Loading…</p>
  const isAdmin = user.role === 'admin'

  return (
    <div>
      <h1 className="font-headline text-4xl text-brand-blue mb-1">Dashboard</h1>
      <p className="text-sm text-gray-500 mb-6">Network overview. Click a node to drill into its monthly report.</p>

      {/* Grand total band */}
      <div className="card bg-brand-blue text-white mb-6 flex flex-wrap items-center gap-x-12 gap-y-3">
        <div>
          <div className="font-headline text-4xl text-brand-yellow">{kg(data.grand_total.tanks)}</div>
          <div className="text-xs uppercase tracking-wide text-blue-200">Total tanks produced</div>
        </div>
        <div>
          <div className="font-headline text-4xl text-brand-yellow">{kg(data.grand_total.material_kg)} kg</div>
          <div className="text-xs uppercase tracking-wide text-blue-200">Total material through plant</div>
        </div>
        {isAdmin && data.grand_total.material_cost !== undefined && (
          <div>
            <div className="font-headline text-3xl">{rand(data.grand_total.material_cost)}</div>
            <div className="text-xs uppercase tracking-wide text-blue-200">Material cost (Fenix)</div>
          </div>
        )}
        <div className="ml-auto text-xs uppercase tracking-wide text-blue-200">{data.nodes.length} node{data.nodes.length === 1 ? '' : 's'}</div>
      </div>

      {/* Node cards */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.nodes.map((n) => (
          <Link key={n.node_id} to={`/node/${n.node_id}`} className="card hover:shadow-md transition-shadow group">
            <div className="flex items-start justify-between">
              <div>
                <div className="font-bold text-lg text-brand-blue">{n.name}</div>
                <div className="text-xs text-gray-500">{n.location}</div>
              </div>
              <ArrowRight size={18} className="text-gray-300 group-hover:text-brand-light" />
            </div>
            <div className="flex gap-8 mt-4">
              <div>
                <div className="font-headline text-3xl text-brand-blue">{kg(n.total_tanks)}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">tanks</div>
              </div>
              <div>
                <div className="font-headline text-3xl text-brand-blue">{kg(n.total_material_kg)}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">kg material</div>
              </div>
            </div>
            {n.tanks_by_type.length > 0 && (
              <div className="mt-3 text-xs text-gray-500 flex flex-wrap gap-x-3 gap-y-1">
                {n.tanks_by_type.map((t) => (
                  <span key={t.tank_type}><b className="text-gray-700">{t.qty}</b> {t.name}</span>
                ))}
              </div>
            )}
            {isAdmin && n.material_cost !== undefined && (
              <div className="mt-2 text-xs text-gray-400">Material cost {rand(n.material_cost)}</div>
            )}
          </Link>
        ))}
        {data.nodes.length === 0 && <p className="text-sm text-gray-400">No active nodes.</p>}
      </div>
    </div>
  )
}
