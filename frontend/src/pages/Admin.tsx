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
  const [newUser, setNewUser] = useState({ email: '', name: '', password: '', role: 'operations' })
  const [pw, setPw] = useState<Record<string, string>>({})
  const [userMsg, setUserMsg] = useState('')

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
    // drop blank rows the user added but didn't fill in
    const cleaned = {
      ...config,
      tank_types: config.tank_types.filter((t) => t.code.trim()),
      powder_products: config.powder_products.filter((p) => p.code.trim()),
      fitting_types: config.fitting_types.filter((f) => f.code.trim()),
    }
    try {
      await api.put('/api/nodes/gogreen/config', cleaned)
      setConfig(cleaned)
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

  const patch = (p: Partial<NodeConfig>) => config && setConfig({ ...config, ...p })

  const addUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await api.post('/api/users', { ...newUser, node_access: newUser.role === 'admin' ? 'all' : ['gogreen'] })
      setNewUser({ email: '', name: '', password: '', role: 'operations' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  const resetPassword = async (u: User) => {
    const newPw = pw[u.email] || ''
    if (newPw.length < 6) { setError('Password must be at least 6 characters'); return }
    setError(''); setUserMsg('')
    try {
      await api.put(`/api/users/${u.email}`, { email: u.email, name: u.name, role: u.role, node_access: u.node_access, password: newPw })
      setPw({ ...pw, [u.email]: '' })
      setUserMsg(`Password updated for ${u.email}.`)
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
                <tr><th className="th">Code</th><th className="th">Name</th><th className="th">Ex-works (R, ex VAT)</th><th className="th">Body weight (kg)</th><th className="th">Lid weight (kg)</th></tr>
              </thead>
              <tbody>
                {config.tank_types.map((t, i) => (
                  <tr key={i}>
                    <td className="td"><input className="input w-24" value={t.code} onChange={(e) => setTank(i, 'code', e.target.value)} /></td>
                    <td className="td"><input className="input" value={t.name} onChange={(e) => setTank(i, 'name', e.target.value)} /></td>
                    <td className="td"><input className="input w-28" type="number" step="0.01" value={t.ex_works_price || ''} onChange={(e) => setTank(i, 'ex_works_price', e.target.value)} /></td>
                    <td className="td"><input className="input w-24" type="number" step="0.1" value={t.weight_kg || ''} onChange={(e) => setTank(i, 'weight_kg', e.target.value)} /></td>
                    <td className="td"><input className="input w-24" type="number" step="0.1" value={t.lid_weight_kg || ''} onChange={(e) => setTank(i, 'lid_weight_kg', e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary" onClick={() => patch({ tank_types: [...config.tank_types, { code: '', name: '', ex_works_price: 0, weight_kg: 0, lid_weight_kg: 1 }] })}>
              Add tank type
            </button>

            <div className="text-xs text-gray-500">Each tank body is 50% colour + 50% black powder; the lid is black. Set the black powder below.</div>

            {/* Powder products */}
            <div>
              <div className="text-sm font-bold text-brand-blue mb-1">Powder products</div>
              <p className="text-xs text-gray-500 mb-2">
                List every powder grade you stock. Tick <b>“Is black powder”</b> on the one black grade — every tank
                draws it for half its body plus the lid, so the recipe needs to know which grade is the black. All the
                others are colours. Only one grade can be the black.
              </p>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Code</th><th className="th">Colour / grade</th><th className="th">Description</th><th className="th">Is black powder</th><th className="th"></th></tr></thead>
                <tbody>
                  {config.powder_products.map((p, i) => (
                    <tr key={i}>
                      <td className="td"><input className="input w-24" value={p.code} onChange={(e) => patch({ powder_products: config.powder_products.map((x, j) => j === i ? { ...x, code: e.target.value } : x) })} /></td>
                      <td className="td"><input className="input w-28" value={p.colour} onChange={(e) => patch({ powder_products: config.powder_products.map((x, j) => j === i ? { ...x, colour: e.target.value } : x) })} /></td>
                      <td className="td"><input className="input" value={p.description || ''} onChange={(e) => patch({ powder_products: config.powder_products.map((x, j) => j === i ? { ...x, description: e.target.value } : x) })} /></td>
                      <td className="td text-center"><input type="checkbox" checked={p.is_black} onChange={(e) => patch({ powder_products: config.powder_products.map((x, j) => ({ ...x, is_black: j === i ? e.target.checked : false })) })} /></td>
                      <td className="td"><button className="text-xs text-brand-red" onClick={() => patch({ powder_products: config.powder_products.filter((_, j) => j !== i) })}>remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="btn-secondary mt-2" onClick={() => patch({ powder_products: [...config.powder_products, { code: '', colour: '', description: '', is_black: false }] })}>Add powder product</button>
            </div>

            {/* Fitting types */}
            <div>
              <div className="text-sm font-bold text-brand-blue mb-1">Fitting types</div>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Code</th><th className="th">Name</th><th className="th"></th></tr></thead>
                <tbody>
                  {config.fitting_types.map((f, i) => (
                    <tr key={i}>
                      <td className="td"><input className="input w-28" value={f.code} onChange={(e) => patch({ fitting_types: config.fitting_types.map((x, j) => j === i ? { ...x, code: e.target.value } : x) })} /></td>
                      <td className="td"><input className="input" value={f.name} onChange={(e) => patch({ fitting_types: config.fitting_types.map((x, j) => j === i ? { ...x, name: e.target.value } : x) })} /></td>
                      <td className="td"><button className="text-xs text-brand-red" onClick={() => patch({ fitting_types: config.fitting_types.filter((_, j) => j !== i) })}>remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="btn-secondary mt-2" onClick={() => patch({ fitting_types: [...config.fitting_types, { code: '', name: '' }] })}>Add fitting type</button>
            </div>

            {/* Fittings per tank matrix */}
            {config.fitting_types.length > 0 && (
              <div>
                <div className="text-sm font-bold text-brand-blue mb-1">Fittings per tank</div>
                <table className="w-full text-sm">
                  <thead><tr><th className="th">Tank</th>{config.fitting_types.map((f) => <th className="th" key={f.code}>{f.name || f.code}</th>)}</tr></thead>
                  <tbody>
                    {config.tank_types.map((t) => (
                      <tr key={t.code}>
                        <td className="td font-semibold">{t.name}</td>
                        {config.fitting_types.map((f) => (
                          <td className="td" key={f.code}>
                            <input className="input w-16" type="number" min="0"
                                   value={config.fittings_per_tank?.[t.code]?.[f.code] ?? ''}
                                   onChange={(e) => {
                                     const fpt = { ...(config.fittings_per_tank || {}) }
                                     fpt[t.code] = { ...(fpt[t.code] || {}), [f.code]: parseInt(e.target.value) || 0 }
                                     patch({ fittings_per_tank: fpt })
                                   }} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Material cost (R/kg, admin-only)</label>
                <input className="input" type="number" step="0.01" value={config.material_cost_per_kg || ''}
                       onChange={(e) => setConfig({ ...config, material_cost_per_kg: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">B-grade ex-works %</label>
                <input className="input" type="number" step="1" value={config.b_grade_exworks_pct || ''}
                       onChange={(e) => setConfig({ ...config, b_grade_exworks_pct: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">VAT %</label>
                <input className="input" type="number" step="0.5" value={config.vat_rate || ''}
                       onChange={(e) => setConfig({ ...config, vat_rate: parseFloat(e.target.value) || 0 })} />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Payment terms (days)</label>
                <input className="input" type="number" value={config.payment_terms_days || ''}
                       onChange={(e) => setConfig({ ...config, payment_terms_days: parseInt(e.target.value) || 30 })} />
              </div>
            </div>
            <div>
              <div className="text-sm font-bold text-brand-blue mb-1">Variance tolerances (0 = exact)</div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Powder (kg)</label>
                  <input className="input" type="number" step="0.1" value={config.tolerances?.powder_kg || ''}
                         onChange={(e) => patch({ tolerances: { ...config.tolerances, powder_kg: parseFloat(e.target.value) || 0 } })} />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Tanks (qty)</label>
                  <input className="input" type="number" value={config.tolerances?.tank_qty || ''}
                         onChange={(e) => patch({ tolerances: { ...config.tolerances, tank_qty: parseInt(e.target.value) || 0 } })} />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Fittings (qty)</label>
                  <input className="input" type="number" value={config.tolerances?.fittings_qty || ''}
                         onChange={(e) => patch({ tolerances: { ...config.tolerances, fittings_qty: parseInt(e.target.value) || 0 } })} />
                </div>
              </div>
            </div>
            <div className="sticky bottom-0 -mx-4 -mb-4 px-4 py-3 bg-white border-t border-gray-200 rounded-b-lg flex items-center gap-3">
              <button className="btn-primary" onClick={saveConfig}>Save config</button>
              <span className="text-xs text-gray-500">Changes only take effect after you save.</span>
              {msg && <span className="text-sm text-brand-green font-semibold ml-auto">{msg}</span>}
              {error && <span className="text-sm text-brand-red ml-auto">{error}</span>}
            </div>
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
        {userMsg && <p className="text-sm text-brand-green font-semibold mb-2">{userMsg}</p>}
        <div className="card p-0 overflow-hidden mb-3">
          <table className="w-full">
            <thead><tr><th className="th">Email</th><th className="th">Name</th><th className="th">Role</th><th className="th">Nodes</th><th className="th">Set new password</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.email}>
                  <td className="td">{u.email}</td>
                  <td className="td font-semibold">{u.name}</td>
                  <td className="td capitalize">{u.role}</td>
                  <td className="td text-gray-500">{u.node_access === 'all' ? 'all' : (u.node_access as string[]).join(', ')}</td>
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <input className="input py-1 w-40" type="text" autoComplete="new-password" placeholder="new password"
                             value={pw[u.email] || ''} onChange={(e) => setPw({ ...pw, [u.email]: e.target.value })} />
                      <button type="button" className="btn-secondary py-1" disabled={(pw[u.email] || '').length < 6} onClick={() => resetPassword(u)}>Update</button>
                    </div>
                  </td>
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
              <option value="operations">operations</option>
              <option value="audit">audit</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <button className="btn-primary">Add user</button>
        </form>
      </div>
    </div>
  )
}
