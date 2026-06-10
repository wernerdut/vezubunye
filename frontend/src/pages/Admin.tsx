import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { SectionTitle, StatusBadge } from '../components/ui'
import type { MonthlyReport, NodeConfig, NodeInfo, TankType, User } from '../types'

export default function Admin() {
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [config, setConfig] = useState<NodeConfig | null>(null)
  const [report, setReport] = useState<MonthlyReport | null>(null)
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7))
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const [newUser, setNewUser] = useState({ email: '', name: '', password: '', role: 'capturer' })

  const load = useCallback(() => {
    api.get('/api/nodes').then((r) => setNodes(r.data))
    api.get('/api/users').then((r) => setUsers(r.data))
    api.get('/api/nodes/gogreen/config').then((r) => setConfig(r.data))
  }, [])
  useEffect(load, [load])

  useEffect(() => {
    api.get(`/api/nodes/gogreen/reports/monthly?month=${month}`).then((r) => setReport(r.data)).catch(() => setReport(null))
  }, [month])

  const saveConfig = async () => {
    if (!config) return
    setMsg(''); setError('')
    try {
      await api.put('/api/nodes/gogreen/config', config)
      setMsg('Config saved.')
    } catch (e) {
      setError(errMsg(e))
    }
  }

  const setTank = (i: number, field: keyof TankType, value: string) => {
    if (!config) return
    const tanks = config.tank_types.map((t, j) =>
      j === i ? { ...t, [field]: field === 'code' || field === 'name' ? value : parseFloat(value) || 0 } : t)
    setConfig({ ...config, tank_types: tanks })
  }

  const addUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post('/api/users', { ...newUser, node_access: newUser.role === 'admin' ? 'all' : ['gogreen'] })
      setNewUser({ email: '', name: '', password: '', role: 'capturer' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="font-headline text-4xl text-brand-blue">Admin</h1>
      {error && <p className="text-sm text-brand-red">{error}</p>}
      {msg && <p className="text-sm text-brand-green font-semibold">{msg}</p>}

      <div>
        <SectionTitle>Nodes</SectionTitle>
        <div className="card p-0 overflow-hidden">
          <table className="w-full">
            <thead><tr><th className="th">ID</th><th className="th">Name</th><th className="th">Location</th><th className="th">Status</th></tr></thead>
            <tbody>
              {nodes.map((n) => (
                <tr key={n.node_id}>
                  <td className="td font-mono text-xs">{n.node_id}</td>
                  <td className="td font-semibold">{n.name}</td>
                  <td className="td">{n.location}</td>
                  <td className="td"><StatusBadge status={n.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {config && (
        <div>
          <SectionTitle>GoGreen Config</SectionTitle>
          <div className="card space-y-4">
            <table className="w-full">
              <thead>
                <tr><th className="th">Code</th><th className="th">Name</th><th className="th">Ex-works (R, ex VAT)</th><th className="th">Weight (kg)</th></tr>
              </thead>
              <tbody>
                {config.tank_types.map((t, i) => (
                  <tr key={i}>
                    <td className="td"><input className="input w-24" value={t.code} onChange={(e) => setTank(i, 'code', e.target.value)} /></td>
                    <td className="td"><input className="input" value={t.name} onChange={(e) => setTank(i, 'name', e.target.value)} /></td>
                    <td className="td"><input className="input w-28" type="number" step="0.01" value={t.ex_works_price} onChange={(e) => setTank(i, 'ex_works_price', e.target.value)} /></td>
                    <td className="td"><input className="input w-24" type="number" step="0.1" value={t.weight_kg} onChange={(e) => setTank(i, 'weight_kg', e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary" onClick={() => setConfig({ ...config, tank_types: [...config.tank_types, { code: '', name: '', ex_works_price: 0, weight_kg: 0 }] })}>
              Add tank type
            </button>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Material cost (R/kg, admin-only)</label>
                <input className="input" type="number" step="0.01" value={config.material_cost_per_kg ?? 0}
                       onChange={(e) => setConfig({ ...config, material_cost_per_kg: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">B-grade ex-works %</label>
                <input className="input" type="number" step="1" value={config.b_grade_exworks_pct}
                       onChange={(e) => setConfig({ ...config, b_grade_exworks_pct: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">VAT %</label>
                <input className="input" type="number" step="0.5" value={config.vat_rate}
                       onChange={(e) => setConfig({ ...config, vat_rate: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Payment terms (days)</label>
                <input className="input" type="number" value={config.payment_terms_days}
                       onChange={(e) => setConfig({ ...config, payment_terms_days: parseInt(e.target.value) || 30 })} />
              </div>
            </div>
            <button className="btn-primary" onClick={saveConfig}>Save config</button>
          </div>
        </div>
      )}

      <div>
        <SectionTitle>Monthly Report — GoGreen</SectionTitle>
        <input className="input w-40 mb-3" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        {report ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              ['kg through plant', `${report.kg_through_plant.toLocaleString('en-ZA')} kg`],
              ['Ex-works value (Fenix)', `R ${report.ex_works_value_invoiced.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`],
              ['Invoiced', `R ${report.invoiced_value.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`],
              ['Cash received', `R ${report.cash_received.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`],
              ['Outstanding', `R ${report.outstanding.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`],
              ['Scrap', `${report.scrap_kg.toLocaleString('en-ZA')} kg`],
              ['Scrap material cost', report.scrap_material_cost !== undefined ? `R ${report.scrap_material_cost.toLocaleString('en-ZA', { minimumFractionDigits: 2 })}` : '—'],
            ].map(([label, value]) => (
              <div className="card" key={label as string}>
                <div className="font-headline text-2xl text-brand-blue">{value}</div>
                <div className="text-xs text-gray-500 uppercase tracking-wide">{label}</div>
              </div>
            ))}
            <div className="card col-span-2">
              <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">Tanks by type</div>
              {report.tanks_by_type.length === 0 ? (
                <p className="text-sm text-gray-400">No production this month.</p>
              ) : (
                report.tanks_by_type.map((t) => (
                  <p className="text-sm" key={t.tank_type}>
                    <b>{t.tank_type}</b>: {t.a} A / {t.b} B / {t.reject} reject
                  </p>
                ))
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No data for {month}.</p>
        )}
      </div>

      <div>
        <SectionTitle>Users</SectionTitle>
        <div className="card p-0 overflow-hidden mb-3">
          <table className="w-full">
            <thead><tr><th className="th">Email</th><th className="th">Name</th><th className="th">Role</th><th className="th">Nodes</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.email}>
                  <td className="td">{u.email}</td>
                  <td className="td font-semibold">{u.name}</td>
                  <td className="td capitalize">{u.role}</td>
                  <td className="td text-gray-500">{u.node_access === 'all' ? 'all' : (u.node_access as string[]).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form onSubmit={addUser} className="card flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Email</label>
            <input className="input" type="email" value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Name</label>
            <input className="input" value={newUser.name} onChange={(e) => setNewUser({ ...newUser, name: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Password</label>
            <input className="input" type="password" value={newUser.password} onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} required />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Role</label>
            <select className="input" value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
              <option value="capturer">capturer</option>
              <option value="controller">controller</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <button className="btn-primary">Add user</button>
        </form>
      </div>
    </div>
  )
}
