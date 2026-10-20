import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import type { NodeConfig, NodeInfo } from '../types'
import { SectionTitle } from './ui'

/** Admin > Nodes: each node's partner app: address, prefix, cutover, commercial model,
 * colour stock, company details, and its first partner admin. */
export default function PartnerApps({ nodes, onChange }: { nodes: NodeInfo[]; onChange: () => void }) {
  const [sel, setSel] = useState(nodes[0]?.node_id || '')
  const node = nodes.find((n) => n.node_id === sel)
  return (
    <div>
      <SectionTitle>Partner apps</SectionTitle>
      <div className="mb-3 flex flex-wrap gap-2">
        {nodes.map((n) => <button key={n.node_id} className={sel === n.node_id ? 'btn-primary text-sm' : 'btn-secondary text-sm'} onClick={() => setSel(n.node_id)}>{n.name}</button>)}
      </div>
      {node && <NodePartner key={node.node_id} node={node} onChange={onChange} />}
    </div>
  )
}

function NodePartner({ node, onChange }: { node: NodeInfo; onChange: () => void }) {
  const [n, setN] = useState<NodeInfo>({ ...node, company: { ...(node.company || {}) } })
  const [cfg, setCfg] = useState<NodeConfig | null>(null)
  const [users, setUsers] = useState<any[]>([])
  const [nu, setNu] = useState({ email: '', name: '', role: 'partner_admin', employer: 'partner', password: '' })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get(`/api/nodes/${node.node_id}/config`).then((r) => setCfg(r.data)).catch(() => setCfg(null))
    api.get(`/api/nodes/${node.node_id}/partner-users`).then((r) => setUsers(r.data))
  }, [node.node_id])
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setErr(''); setMsg('')
    try { await fn(); setMsg(ok); onChange() } catch (e) { setErr(errMsg(e)) }
  }
  const saveNode = () => run(() => api.put(`/api/nodes/${node.node_id}`, {
    node_id: n.node_id, name: n.name, location: n.location, status: n.status, slug: n.slug || undefined,
    prefix: n.prefix || undefined, partner_app_live: !!n.partner_app_live, company: n.company,
    branding: n.branding,
  }), 'Node saved')
  const saveCfg = () => cfg && run(() => api.put(`/api/nodes/${node.node_id}/config`, cfg), 'Config saved')
  const co = (k: string) => ({ value: (n.company || {})[k] || '', onChange: (e: any) => setN({ ...n, company: { ...n.company, [k]: e.target.value } }) })
  return (
    <div className="card space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <label className="text-sm">Address <div className="flex items-center gap-1"><input className="input" value={n.slug || ''} onChange={(e) => setN({ ...n, slug: e.target.value.toLowerCase() })} />.vezubunye.com</div></label>
        <label className="text-sm">Document prefix<input className="input" value={n.prefix || ''} onChange={(e) => setN({ ...n, prefix: e.target.value.toUpperCase() })} /></label>
        <label className="text-sm">Display name<input className="input" value={n.branding?.display_name || ''} onChange={(e) => setN({ ...n, branding: { ...(n.branding || {}), display_name: e.target.value } })} /></label>
        <label className="flex items-center gap-2 pt-5 text-sm font-semibold"><input type="checkbox" checked={!!n.partner_app_live} onChange={(e) => setN({ ...n, partner_app_live: e.target.checked })} /> Partner app live (internal capture read-only)</label>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        {['legal_name', 'reg_no', 'vat_number', 'address', 'phone', 'whatsapp', 'email', 'website'].map((k) => (
          <label key={k} className="text-sm">{k.replace('_', ' ')}<input className="input" {...co(k)} /></label>
        ))}
      </div>
      <p className="text-xs text-gray-500">DNS: add a CNAME for {n.slug || '<slug>'} at clusterdns pointing to the partner Vercel project, and add the domain to that project.</p>
      <button className="btn-primary" onClick={saveNode}>Save node</button>

      {cfg && (
        <div className="grid gap-3 border-t pt-4 md:grid-cols-3">
          <label className="text-sm">Commercial model
            <select className="input" value={cfg.commercial_model || 'fenix_owns_tanks'} onChange={(e) => setCfg({ ...cfg, commercial_model: e.target.value as NodeConfig['commercial_model'] })}>
              <option value="fenix_owns_tanks">Fenix owns the tanks (ex-works draw)</option>
              <option value="powder_sale">Fenix sells powder</option>
            </select></label>
          <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={!!cfg.track_colour_stock} onChange={(e) => setCfg({ ...cfg, track_colour_stock: e.target.checked })} /> Count finished goods by colour</label>
          <div className="pt-4"><button className="btn-secondary" onClick={saveCfg}>Save config</button></div>
          {cfg.track_colour_stock && <p className="text-xs text-brand-orange md:col-span-3">Switching colour stock on needs a physical finished-goods count by colour as the opening position.</p>}
        </div>
      )}

      <div className="border-t pt-4">
        <div className="mb-2 text-sm font-bold text-brand-blue">Partner app logins</div>
        {users.map((u) => <div key={u._id} className="text-sm">{u.name} · {u.email} · {u.role.replace('partner_', '')}{u.employer === 'fenix' ? ' · Fenix' : ''}{u.active ? '' : ' · disabled'}</div>)}
        {users.length === 0 && <p className="text-sm text-gray-500">None yet. Create the first partner admin; they add the rest in the app.</p>}
        <div className="mt-2 grid gap-2 md:grid-cols-6">
          <input className="input" placeholder="Email" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} />
          <input className="input" placeholder="Name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} />
          <select className="input" value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option value="partner_admin">Admin</option><option value="partner_staff">Staff</option></select>
          <select className="input" value={nu.employer} onChange={(e) => setNu({ ...nu, employer: e.target.value })}><option value="partner">Partner</option><option value="fenix">Fenix</option></select>
          <input className="input" type="password" placeholder="Password (8+)" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          <button className="btn-primary" onClick={() => run(async () => { await api.post(`/api/nodes/${node.node_id}/partner-users`, nu); setUsers((await api.get(`/api/nodes/${node.node_id}/partner-users`)).data); setNu({ email: '', name: '', role: 'partner_admin', employer: 'partner', password: '' }) }, 'Login created')}>Create login</button>
        </div>
      </div>
      {msg && <p className="text-sm font-semibold text-brand-green">{msg}</p>}
      {err && <p className="text-sm text-brand-red">{err}</p>}
    </div>
  )
}
