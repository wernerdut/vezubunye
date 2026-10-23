import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, errMsg } from '../api'
import { Chip, ErrorBox, Field, PageTitle, Tabs, useAction } from '../components/ui'
import { Colour, Config, useApp } from '../lib/ctx'
import { date, label, today } from '../lib/format'

type Tab = 'company' | 'branding' | 'products' | 'colours' | 'prices' | 'charges' | 'drivers' | 'users' | 'xero' | 'yoco' | 'import'
const TABS: { key: Tab; label: string }[] = [
  { key: 'company', label: 'Company' }, { key: 'branding', label: 'Branding' }, { key: 'products', label: 'Products' },
  { key: 'colours', label: 'Colours' }, { key: 'prices', label: 'Prices' }, { key: 'charges', label: 'Charges & general' },
  { key: 'drivers', label: 'Drivers' }, { key: 'users', label: 'Users' }, { key: 'xero', label: 'Xero' }, { key: 'yoco', label: 'Yoco' },
  { key: 'import', label: 'Import' },
]

export default function Settings() {
  const loc = useLocation()
  const navigate = useNavigate()
  const tab = (loc.pathname.split('/')[2] as Tab) || 'company'
  return (
    <div>
      <PageTitle title="Settings" />
      <Tabs tabs={TABS} value={tab} onChange={(k) => navigate(`/settings/${k}`)} />
      {tab === 'company' && <Company />}
      {tab === 'branding' && <BrandingTab />}
      {tab === 'products' && <Products />}
      {tab === 'colours' && <Colours />}
      {tab === 'prices' && <Prices />}
      {tab === 'charges' && <Charges />}
      {tab === 'drivers' && <Drivers />}
      {tab === 'users' && <Users />}
      {tab === 'xero' && <Xero />}
      {tab === 'yoco' && <Yoco />}
      {tab === 'import' && <ImportCin7 />}
    </div>
  )
}

function SaveBar({ busy, error, onSave, saved }: { busy: boolean; error: string | null; onSave: () => void; saved?: boolean }) {
  return <div className="mt-4 flex items-center justify-end gap-3"><ErrorBox msg={error} />{saved && <span className="text-sm text-emerald-700">Saved</span>}<button className="btn-primary" disabled={busy} onClick={onSave}>Save</button></div>
}

function useConfigSave() {
  const { reload } = useApp()
  const a = useAction()
  const [saved, setSaved] = useState(false)
  const save = (body: Partial<Config> | Record<string, unknown>, path = '/api/p/settings/config') => a.run(async () => { await api.put(path, body); await reload(); setSaved(true); setTimeout(() => setSaved(false), 2000) }, errMsg)
  return { ...a, saved, save }
}

function Company() {
  const { cfg } = useApp()
  const [c, setC] = useState<any>({ ...cfg.company, bank: { ...(cfg.company.bank || {}) } })
  const s = useConfigSave()
  const f = (k: string) => ({ value: c[k] || '', onChange: (e: any) => setC({ ...c, [k]: e.target.value }) })
  const b = (k: string) => ({ value: c.bank[k] || '', onChange: (e: any) => setC({ ...c, bank: { ...c.bank, [k]: e.target.value } }) })
  return (
    <div className="card p-4">
      <p className="mb-3 text-sm text-gray-500">Printed on quotes, invoices, delivery notes and statements.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Registered name"><input className="input" {...f('legal_name')} /></Field>
        <Field label="Reg. No."><input className="input" {...f('reg_no')} /></Field>
        <Field label="VAT number"><input className="input" {...f('vat_number')} /></Field>
        <Field label="Address"><input className="input" {...f('address')} /></Field>
        <Field label="WhatsApp"><input className="input" {...f('whatsapp')} /></Field>
        <Field label="Landline"><input className="input" {...f('phone')} /></Field>
        <Field label="Email"><input className="input" {...f('email')} /></Field>
        <Field label="Website"><input className="input" {...f('website')} /></Field>
        <Field label="Hours"><input className="input" {...f('hours')} /></Field>
      </div>
      <h3 className="mb-2 mt-5 font-bold">Bank details</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Bank"><input className="input" {...b('bank')} /></Field>
        <Field label="Account name"><input className="input" {...b('account_name')} /></Field>
        <Field label="Account number"><input className="input" {...b('account_number')} /></Field>
        <Field label="Branch code"><input className="input" {...b('branch_code')} /></Field>
        <Field label="SWIFT"><input className="input" {...b('swift')} /></Field>
      </div>
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save(c, '/api/p/settings/company')} />
    </div>
  )
}

function BrandingTab() {
  const { cfg, brand } = useApp()
  const [b, setB] = useState<any>({ display_name: brand.display_name, colors: { ...brand.colors } })
  const s = useConfigSave()
  const upload = async (kind: string, file?: File | null) => {
    if (!file) return
    const fd = new FormData(); fd.append('file', file)
    await s.run(() => api.post(`/api/p/settings/branding/asset/${kind}`, fd), errMsg)
  }
  return (
    <div className="card space-y-4 p-4">
      <Field label="Business name"><input className="input" value={b.display_name} onChange={(e) => setB({ ...b, display_name: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-3">
        {['primary', 'accent', 'dark'].map((k) => (
          <Field key={k} label={`${k} colour`}><div className="flex gap-2"><input type="color" value={b.colors[k] || '#000000'} onChange={(e) => setB({ ...b, colors: { ...b.colors, [k]: e.target.value } })} /><input className="input" value={b.colors[k] || ''} onChange={(e) => setB({ ...b, colors: { ...b.colors, [k]: e.target.value } })} /></div></Field>
        ))}
      </div>
      <Field label="Gradient (comma-separated colours)"><input className="input" value={(b.colors.gradient || []).join(', ')} onChange={(e) => setB({ ...b, colors: { ...b.colors, gradient: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) } })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        {[['logo_light', 'Logo for light backgrounds (SVG or PNG)'], ['logo_dark', 'Logo for dark headers'], ['logo_light_png', 'Logo PNG for PDFs'], ['favicon', 'Favicon'], ['app_icon', 'App icon (PNG)']].map(([k, l]) => (
          <Field key={k} label={l}><div className="flex items-center gap-2">{brand.assets[k] && <img src={brand.assets[k]} className="h-10 rounded bg-gray-100 p-1" />}<input type="file" accept="image/*" className="input" onChange={(e) => upload(k, e.target.files?.[0])} /></div></Field>
        ))}
      </div>
      <p className="text-xs text-gray-500">Address: {cfg.slug}.vezubunye.com</p>
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save(b, '/api/p/settings/branding')} />
    </div>
  )
}

function Products() {
  const { cfg } = useApp()
  const [tanks, setTanks] = useState(cfg.tank_types.map((t) => ({ ...t })))
  const s = useConfigSave()
  return (
    <div className="card p-4">
      <p className="mb-3 text-sm text-gray-500">Weights and the recipe are set by Fenix. New tank types are added by Fenix.</p>
      {tanks.map((t, i) => (
        <div key={t.code} className="mb-3 rounded-lg border p-3">
          <div className="mb-2 flex items-center gap-2"><b>{t.code}</b><Chip>{t.kind}</Chip><label className="ml-auto flex items-center gap-1 text-sm"><input type="checkbox" checked={t.active !== false} onChange={(e) => setTanks(tanks.map((x, j) => j === i ? { ...x, active: e.target.checked } : x))} /> Active</label></div>
          <div className="grid gap-2 sm:grid-cols-4">
            <Field label="Name"><input className="input" value={t.name} onChange={(e) => setTanks(tanks.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} /></Field>
            {(['capacity_l', 'diameter_mm', 'height_mm'] as const).map((k) => <Field key={k} label={k.replace('_', ' ')}><input type="number" className="input" value={t[k] ?? ''} onChange={(e) => setTanks(tanks.map((x, j) => j === i ? { ...x, [k]: Number(e.target.value) } : x))} /></Field>)}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {cfg.colours.map((c) => {
              const on = !t.allowed_colours?.length || t.allowed_colours.includes(c.code)
              return <button key={c.code} onClick={() => {
                const cur = t.allowed_colours?.length ? t.allowed_colours : cfg.colours.map((x) => x.code)
                const next = on ? cur.filter((x) => x !== c.code) : [...cur, c.code]
                setTanks(tanks.map((x, j) => j === i ? { ...x, allowed_colours: next } : x))
              }} className={`rounded-full px-2 py-0.5 text-xs ring-1 ${on ? 'bg-brand-primary/10 ring-brand-primary' : 'opacity-50 ring-gray-300'}`}>{c.name}</button>
            })}
          </div>
        </div>
      ))}
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save({ tank_types: tanks })} />
    </div>
  )
}

function Colours() {
  const { cfg } = useApp()
  const [cols, setCols] = useState<Colour[]>(cfg.colours.map((c) => ({ ...c })))
  const s = useConfigSave()
  const colourProducts = cfg.powder_products
  const upd = (i: number, p: Partial<Colour>) => setCols(cols.map((x, j) => j === i ? { ...x, ...p } : x))
  return (
    <div className="card p-4">
      <p className="mb-3 text-sm text-gray-500">Every colour is moulded colour outside, black inside. A colour without a powder product can be quoted but not captured.</p>
      {cols.map((c, i) => (
        <div key={i} className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-12">
          <input type="color" className="h-10 w-full sm:col-span-1" value={c.swatch_hex} onChange={(e) => upd(i, { swatch_hex: e.target.value })} />
          <input className="input sm:col-span-2" placeholder="Code" value={c.code} onChange={(e) => upd(i, { code: e.target.value.toUpperCase() })} />
          <input className="input sm:col-span-3" placeholder="Name" value={c.name} onChange={(e) => upd(i, { name: e.target.value })} />
          <select className="input sm:col-span-2" value={c.availability} onChange={(e) => upd(i, { availability: e.target.value as Colour['availability'] })}><option value="standard">Standard</option><option value="on_request">On request</option></select>
          <select className="input sm:col-span-3" value={c.powder_product} onChange={(e) => upd(i, { powder_product: e.target.value })}><option value="">No powder product</option>{colourProducts.map((p) => <option key={p.code} value={p.code}>{p.code}</option>)}</select>
          <button className="text-gray-400 sm:col-span-1" onClick={() => setCols(cols.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={() => setCols([...cols, { code: '', name: '', swatch_hex: '#888888', availability: 'on_request', powder_product: '' }])}><Plus size={14} /> Colour</button>
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save({ colours: cols })} />
    </div>
  )
}

function Prices() {
  const { cfg, reload } = useApp()
  const [tiers, setTiers] = useState(cfg.price_tiers.map((t) => ({ ...t })))
  const [tier, setTier] = useState(cfg.price_tiers[0]?.code || 'retail')
  const [versions, setVersions] = useState<any[]>([])
  const cur = cfg.tiers_versions?.[tier]
  const [prices, setPrices] = useState<Record<string, { A?: number; B?: number }>>(cur?.prices || {})
  const [eff, setEff] = useState(today())
  const a = useAction()
  const s = useConfigSave()
  useEffect(() => { setPrices(cfg.tiers_versions?.[tier]?.prices || {}); api.get(`/api/p/settings/prices/${tier}/versions`).then((r) => setVersions(r.data)) }, [tier, cfg])
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="mb-2 font-bold">Tiers</h3>
        {tiers.map((t, i) => (
          <div key={i} className="mb-2 grid grid-cols-3 gap-2">
            <input className="input" value={t.code} disabled={i === 0} onChange={(e) => setTiers(tiers.map((x, j) => j === i ? { ...x, code: e.target.value.toLowerCase() } : x))} />
            <input className="input" value={t.name} onChange={(e) => setTiers(tiers.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={t.prices_include_vat} onChange={(e) => setTiers(tiers.map((x, j) => j === i ? { ...x, prices_include_vat: e.target.checked } : x))} /> Prices include VAT</label>
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setTiers([...tiers, { code: '', name: '', prices_include_vat: true }])}><Plus size={14} /> Tier</button>
        <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save({ price_tiers: tiers })} />
      </div>
      <div className="card p-4">
        <div className="mb-3 flex flex-wrap gap-2">{cfg.price_tiers.map((t) => <button key={t.code} className={tier === t.code ? 'btn-dark btn-sm' : 'btn-ghost btn-sm'} onClick={() => setTier(t.code)}>{t.name}</button>)}</div>
        <p className="mb-3 text-sm text-gray-500">{cur ? `Version ${cur.version}, effective ${date(cur.effective_from)}.` : 'No prices yet.'} Saving makes a new version; open quotes and orders keep theirs. No B price means B stock is priced by hand.</p>
        {cfg.tank_types.map((t) => (
          <div key={t.code} className="mb-2 grid grid-cols-3 items-center gap-2">
            <span className="text-sm font-semibold">{t.name}</span>
            <input type="number" className="input" placeholder="A price" value={prices[t.code]?.A ?? ''} onChange={(e) => setPrices({ ...prices, [t.code]: { ...prices[t.code], A: e.target.value === '' ? undefined : Number(e.target.value) } })} />
            <input type="number" className="input" placeholder="B price (optional)" value={prices[t.code]?.B ?? ''} onChange={(e) => setPrices({ ...prices, [t.code]: { ...prices[t.code], B: e.target.value === '' ? undefined : Number(e.target.value) } })} />
          </div>
        ))}
        <div className="mt-3 flex flex-wrap items-end justify-end gap-3">
          <Field label="Effective from"><input type="date" className="input" value={eff} onChange={(e) => setEff(e.target.value)} /></Field>
          <ErrorBox msg={a.error} />
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { await api.post(`/api/p/settings/prices/${tier}`, { effective_from: eff, prices: Object.fromEntries(Object.entries(prices).filter(([, v]) => v?.A != null)) }); await reload() }, errMsg)}>Save new version</button>
        </div>
        {versions.length > 1 && <p className="mt-3 text-xs text-gray-500">History: {versions.map((v) => `v${v.version} from ${v.effective_from}`).join(' · ')}</p>}
      </div>
    </div>
  )
}

function Charges() {
  const { cfg } = useApp()
  const [charges, setCharges] = useState(cfg.charges.map((c) => ({ ...c })))
  const [g, setG] = useState({ quote_validity_days: cfg.quote_validity_days, cash_banking_days: cfg.cash_banking_days, payment_terms_days: cfg.payment_terms_days })
  const s = useConfigSave()
  return (
    <div className="card p-4">
      <h3 className="mb-2 font-bold">Charge lines</h3>
      <p className="mb-3 text-sm text-gray-500">Added by hand to quotes, orders and invoices. Leave the amount empty for "quoted per job".</p>
      {charges.map((c, i) => (
        <div key={i} className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-12">
          <input className="input sm:col-span-2" placeholder="Code" value={c.code} onChange={(e) => setCharges(charges.map((x, j) => j === i ? { ...x, code: e.target.value.toUpperCase() } : x))} />
          <input className="input sm:col-span-4" placeholder="Name" value={c.name} onChange={(e) => setCharges(charges.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
          <input type="number" className="input sm:col-span-2" placeholder="Quoted" value={c.amount ?? ''} onChange={(e) => setCharges(charges.map((x, j) => j === i ? { ...x, amount: e.target.value === '' ? null : Number(e.target.value) } : x))} />
          <label className="flex items-center gap-1 text-sm sm:col-span-2"><input type="checkbox" checked={c.includes_vat} onChange={(e) => setCharges(charges.map((x, j) => j === i ? { ...x, includes_vat: e.target.checked } : x))} /> incl VAT</label>
          <button className="text-gray-400" onClick={() => setCharges(charges.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={() => setCharges([...charges, { code: '', name: '', amount: null, includes_vat: true }])}><Plus size={14} /> Charge</button>
      <h3 className="mb-2 mt-6 font-bold">General</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Quotes valid (days)"><input type="number" className="input" value={g.quote_validity_days} onChange={(e) => setG({ ...g, quote_validity_days: Number(e.target.value) })} /></Field>
        <Field label="Bank cash within (days)"><input type="number" className="input" value={g.cash_banking_days} onChange={(e) => setG({ ...g, cash_banking_days: Number(e.target.value) })} /></Field>
        <Field label="Default terms (days)"><input type="number" className="input" value={g.payment_terms_days} onChange={(e) => setG({ ...g, payment_terms_days: Number(e.target.value) })} /></Field>
      </div>
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save({ charges, ...g })} />
    </div>
  )
}

function Drivers() {
  const { cfg } = useApp()
  const [d, setD] = useState(cfg.drivers.map((x) => ({ ...x })))
  const s = useConfigSave()
  return (
    <div className="card p-4">
      <p className="mb-3 text-sm text-gray-500">Drivers are not users. They get a link per delivery on WhatsApp.</p>
      {d.map((x, i) => (
        <div key={i} className="mb-2 grid grid-cols-3 gap-2 sm:grid-cols-12">
          <input className="input sm:col-span-4" placeholder="Name" value={x.name} onChange={(e) => setD(d.map((y, j) => j === i ? { ...y, name: e.target.value } : y))} />
          <input className="input sm:col-span-4" placeholder="Phone" value={x.phone} onChange={(e) => setD(d.map((y, j) => j === i ? { ...y, phone: e.target.value } : y))} />
          <input className="input sm:col-span-3" placeholder="Vehicle reg" value={x.vehicle_reg || ''} onChange={(e) => setD(d.map((y, j) => j === i ? { ...y, vehicle_reg: e.target.value } : y))} />
          <button className="text-gray-400" onClick={() => setD(d.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={() => setD([...d, { name: '', phone: '', vehicle_reg: '' }])}><Plus size={14} /> Driver</button>
      <SaveBar busy={s.busy} error={s.error} saved={s.saved} onSave={() => s.save({ drivers: d })} />
    </div>
  )
}

function Users() {
  const { me } = useApp()
  const [rows, setRows] = useState<any[]>([])
  const [n, setN] = useState({ email: '', name: '', role: 'partner_staff', employer: 'partner', password: '' })
  const a = useAction()
  const load = () => api.get('/api/p/users').then((r) => setRows(r.data))
  useEffect(() => { load() }, [])
  return (
    <div className="card p-4">
      {rows.map((u) => (
        <div key={u._id} className="flex flex-wrap items-center justify-between gap-2 border-b py-2 text-sm">
          <span><b>{u.name || u.email}</b> {u.email} <Chip>{label(u.role.replace('partner_', ''))}</Chip>{u.employer === 'fenix' && <Chip tone="blue">Fenix</Chip>}{!u.active && <Chip tone="red">Disabled</Chip>}</span>
          {u.email !== me.email && <span className="flex gap-2">
            <button className="btn-ghost btn-sm" onClick={async () => { await api.put(`/api/p/users/${u._id}`, { role: u.role === 'partner_admin' ? 'partner_staff' : 'partner_admin' }); load() }}>{u.role === 'partner_admin' ? 'Make staff' : 'Make admin'}</button>
            <button className="btn-ghost btn-sm" onClick={async () => { await api.put(`/api/p/users/${u._id}`, { active: !u.active }); load() }}>{u.active ? 'Disable' : 'Enable'}</button>
            <button className="btn-ghost btn-sm" onClick={async () => { const p = prompt('New password (8+ characters)'); if (p) { await api.put(`/api/p/users/${u._id}`, { password: p }); load() } }}>Reset password</button>
          </span>}
        </div>
      ))}
      <h3 className="mb-2 mt-5 font-bold">Add user</h3>
      <div className="grid gap-2 sm:grid-cols-5">
        <input className="input" placeholder="Email" value={n.email} onChange={(e) => setN({ ...n, email: e.target.value })} />
        <input className="input" placeholder="Name" value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} />
        <select className="input" value={n.role} onChange={(e) => setN({ ...n, role: e.target.value })}><option value="partner_staff">Staff</option><option value="partner_admin">Admin</option></select>
        <select className="input" value={n.employer} onChange={(e) => setN({ ...n, employer: e.target.value })}><option value="partner">Works for us</option><option value="fenix">Works for Fenix</option></select>
        <input className="input" type="password" placeholder="Password" value={n.password} onChange={(e) => setN({ ...n, password: e.target.value })} />
      </div>
      <ErrorBox msg={a.error} />
      <div className="mt-3 flex justify-end"><button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { await api.post('/api/p/users', n); setN({ email: '', name: '', role: 'partner_staff', employer: 'partner', password: '' }); load() }, errMsg)}>Add user</button></div>
    </div>
  )
}

function Xero() {
  const { reload } = useApp()
  const [x, setX] = useState<any>(null)
  const [st, setSt] = useState<any>(null)
  const a = useAction()
  const load = () => api.get('/api/p/xero').then((r) => { setX(r.data); setSt(r.data.settings) })
  useEffect(() => { load() }, [])
  if (!x) return null
  const accounts = x.reference?.accounts || []
  const taxes = x.reference?.tax_rates || []
  const tracking = x.reference?.tracking || []
  const acct = (k: string, v: string, onChange: (v: string) => void, bank = false) => (
    <Field label={k}><select className="input" value={v || ''} onChange={(e) => onChange(e.target.value)}><option value="">Choose…</option>
      {accounts.filter((a: any) => !bank || a.type === 'BANK' || a.enable_payments).map((a: any) => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}</select></Field>
  )
  return (
    <div className="card space-y-4 p-4">
      {!x.connected ? <>
        <p className="text-sm text-gray-600">{x.status === 'broken' ? `Disconnected: ${x.last_error}` : 'Connect your Xero organisation to post invoices, payments and bills, and pull EFT you reconcile in Xero.'}</p>
        <ErrorBox msg={a.error} />
        <button className="btn-primary" disabled={a.busy || !x.configured} onClick={() => a.run(async () => { window.location.href = (await api.get('/api/p/xero/connect')).data.url }, errMsg)}>{x.status === 'broken' ? 'Reconnect Xero' : 'Connect Xero'}</button>
        {!x.configured && <p className="text-xs text-amber-700">Xero is not configured on the server yet.</p>}
      </> : <>
        <p className="text-sm">Connected to <b>{x.org_name}</b>{x.last_sync ? ` · last sync ${new Date(x.last_sync).toLocaleString('en-ZA')}` : ''} · {x.calls_today} calls today{x.waiting ? ` · ${x.waiting} waiting` : ''}</p>
        {st && <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Post invoices and bills as"><select className="input" value={st.post_status} onChange={(e) => setSt({ ...st, post_status: e.target.value })}><option value="DRAFT">Draft</option><option value="AUTHORISED">Authorised</option></select></Field>
          <Field label="Sales tax type"><select className="input" value={st.tax_type} onChange={(e) => setSt({ ...st, tax_type: e.target.value })}>{taxes.map((t: any) => <option key={t.tax_type} value={t.tax_type}>{t.name}</option>)}</select></Field>
          {acct('Sales account', st.sales_accounts?.water, (v) => setSt({ ...st, sales_accounts: { ...st.sales_accounts, water: v, septic: v } }))}
          <Field label="Yoco payments in Xero" hint={(st.yoco_in_xero || 'match') === 'match' ? 'You match each Yoco deposit to its invoice in Xero; the app links it to the Yoco payment.' : 'The app posts each Yoco payment to the clearing account; you reconcile the deposit against it.'}>
            <select className="input" value={st.yoco_in_xero || 'match'} onChange={(e) => setSt({ ...st, yoco_in_xero: e.target.value })}>
              <option value="match">Match deposits to invoices in Xero</option>
              <option value="clearing">Post to a Yoco clearing account</option>
            </select>
          </Field>
          {st.yoco_in_xero === 'clearing' && acct('Yoco clearing account', st.yoco_clearing_account, (v) => setSt({ ...st, yoco_clearing_account: v }), true)}
          {acct('Cash on hand account', st.cash_account, (v) => setSt({ ...st, cash_account: v }), true)}
          <Field label="Tracking category (optional)"><select className="input" value={st.tracking_category_id || ''} onChange={(e) => setSt({ ...st, tracking_category_id: e.target.value || null, tracking_option_id: null })}><option value="">None</option>{tracking.map((t: any) => <option key={t.tracking_category_id} value={t.tracking_category_id}>{t.name}</option>)}</select></Field>
          {st.tracking_category_id && <Field label="Tracking option"><select className="input" value={st.tracking_option_id || ''} onChange={(e) => setSt({ ...st, tracking_option_id: e.target.value })}><option value="">Choose…</option>{(tracking.find((t: any) => t.tracking_category_id === st.tracking_category_id)?.options || []).map((o: any) => <option key={o.tracking_option_id} value={o.tracking_option_id}>{o.name}</option>)}</select></Field>}
        </div>}
        <ErrorBox msg={a.error} />
        <div className="flex flex-wrap justify-end gap-2">
          <button className="btn-ghost" disabled={a.busy} onClick={() => a.run(async () => { await api.post('/api/p/xero/sync'); await load() }, errMsg)}>Sync now</button>
          <button className="btn-danger" onClick={() => confirm('Disconnect Xero?') && a.run(async () => { await api.delete('/api/p/xero'); await load(); await reload() }, errMsg)}>Disconnect</button>
          <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { await api.put('/api/p/xero/settings', st); await load() }, errMsg)}>Save</button>
        </div>
      </>}
    </div>
  )
}

function Yoco() {
  const { reload } = useApp()
  const [y, setY] = useState<any>(null)
  const [key, setKey] = useState('')
  const a = useAction()
  const load = () => api.get('/api/p/yoco').then((r) => setY(r.data))
  useEffect(() => { load() }, [])
  if (!y) return null
  return (
    <div className="card space-y-3 p-4">
      <p className="text-sm text-gray-600">Your own Yoco account. Money goes straight to you. Payment links, the driver's QR code, and card-machine slips all reconcile here.</p>
      {y.connected ? <p className="text-sm">Connected ({y.mode}) · key {y.key_hint} · webhook {y.webhook_registered ? 'registered' : 'missing'}</p> : <p className="text-sm text-amber-700">Not connected: no pay links or driver QR codes until it is.</p>}
      <Field label="Secret key (sk_live_… or sk_test_…)"><input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} /></Field>
      <ErrorBox msg={a.error} />
      <div className="flex justify-end gap-2">
        {y.connected && <button className="btn-danger" onClick={() => a.run(async () => { await api.delete('/api/p/yoco'); await load(); await reload() }, errMsg)}>Disconnect</button>}
        <button className="btn-primary" disabled={!key || a.busy} onClick={() => a.run(async () => { await api.put('/api/p/yoco', { secret_key: key }); setKey(''); await load(); await reload() }, errMsg)}>Save key</button>
      </div>
    </div>
  )
}

/** Cin7 customer export: check first (counts, nothing saved), then import. Running it again
 * updates the same customers rather than adding them twice. */
function ImportCin7() {
  const [cust, setCust] = useState<File | null>(null)
  const [addr, setAddr] = useState<File | null>(null)
  const [res, setRes] = useState<any>(null)
  const a = useAction()
  const send = (apply: boolean) => a.run(async () => {
    const fd = new FormData()
    fd.append('customers', cust as File)
    fd.append('addresses', addr as File)
    fd.append('apply', String(apply))
    setRes((await api.post('/api/p/customers/import/cin7', fd)).data)
  }, errMsg)
  return (
    <div className="card space-y-3 p-4">
      <p className="text-sm text-gray-600">Customers and addresses from the Cin7 export. Check shows what would happen and saves nothing. Import can be run again: it updates the same customers.</p>
      <Field label="Customers file (makx_customers.csv)"><input type="file" accept=".csv" className="input" onChange={(e) => { setCust(e.target.files?.[0] || null); setRes(null) }} /></Field>
      <Field label="Addresses file (makx_customer_addresses.csv)"><input type="file" accept=".csv" className="input" onChange={(e) => { setAddr(e.target.files?.[0] || null); setRes(null) }} /></Field>
      <ErrorBox msg={a.error} />
      {res && <div className="rounded-lg border bg-gray-50 p-3 text-sm">
        <div className="font-semibold">{res.applied ? 'Imported' : 'Check only: nothing saved yet'}</div>
        <ul className="mt-1 space-y-0.5">
          <li>{res.rows} customers in the file: {res.new} new, {res.updated} already imported (updated), {res.merged_with_existing.length} matched to customers already in the app</li>
          <li>{res.trade} on trade prices (Cin7 wholesale list); {res.trade_to_check} look like trade but import as retail, marked to check</li>
          <li>{res.with_delivery_sites} with a delivery address; {res.no_address} with no address at all</li>
          <li>Everyone imports paying upfront. New customers continue after {res.refs_continue_after}.</li>
        </ul>
        {res.merged_with_existing.length > 0 && <details className="mt-2"><summary className="cursor-pointer">Matched to existing customers</summary>
          <ul className="mt-1 text-xs text-gray-600">{res.merged_with_existing.map((m: string) => <li key={m}>{m}</li>)}</ul></details>}
      </div>}
      <div className="flex justify-end gap-2">
        <button className="btn-ghost" disabled={!cust || !addr || a.busy} onClick={() => send(false)}>Check</button>
        <button className="btn-primary" disabled={!cust || !addr || a.busy || !res || res.applied} onClick={() => send(true)}>Import</button>
      </div>
    </div>
  )
}
