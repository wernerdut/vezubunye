import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Crosshair, MapPin, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, errMsg } from '../api'
import { useApp } from '../lib/ctx'
import { label, money } from '../lib/format'
import { ErrorBox, Field, Modal, useAction } from './ui'

// ---------------------------------------------------------------- lines

export interface Line {
  line_id?: string; kind: 'tank' | 'charge'; tank_type?: string; colour?: string; grade?: 'A' | 'B'
  qty: number; unit_price?: number | null; code?: string; name?: string
}

export function LineEditor({ lines, setLines, tier }: { lines: Line[]; setLines: (l: Line[]) => void; tier: string }) {
  const { cfg } = useApp()
  const version = cfg.tiers_versions?.[tier]
  const tierDef = cfg.price_tiers.find((t) => t.code === tier)
  const tanks = cfg.tank_types.filter((t) => t.active !== false)
  const listPrice = (l: Line) => (l.kind === 'tank' && l.tank_type ? version?.prices?.[l.tank_type]?.[l.grade || 'A'] : undefined)
  const unit = (l: Line) => (l.unit_price ?? (l.kind === 'charge' ? cfg.charges.find((c) => c.code === l.code)?.amount : listPrice(l)) ?? 0)
  const upd = (i: number, patch: Partial<Line>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const total = lines.reduce((s, l) => s + unit(l) * (l.qty || 0), 0)
  const delivery = cfg.charges.find((c) => /deliver/i.test(c.name))
  return (
    <div className="space-y-2">
      {lines.map((l, i) => {
        const t = cfg.tank_types.find((x) => x.code === l.tank_type)
        const colours = cfg.colours.filter((c) => !t?.allowed_colours?.length || t.allowed_colours.includes(c.code))
        return (
          <div key={i} className="rounded-lg border p-2">
            {l.kind === 'tank' ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-12">
                <select className="input col-span-2 sm:col-span-3" value={l.tank_type || ''} onChange={(e) => upd(i, { tank_type: e.target.value, colour: undefined, unit_price: undefined })}>
                  <option value="">Tank…</option>{tanks.map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}
                </select>
                <select className="input col-span-2 sm:col-span-3" value={l.colour || ''} onChange={(e) => upd(i, { colour: e.target.value })}>
                  <option value="">Colour…</option>
                  {colours.map((c) => <option key={c.code} value={c.code}>{c.name}{c.availability === 'on_request' ? ' (made to order)' : ''}</option>)}
                </select>
                <select className="input sm:col-span-1" value={l.grade || 'A'} onChange={(e) => upd(i, { grade: e.target.value as 'A' | 'B', unit_price: undefined })}>
                  <option value="A">A</option><option value="B">B</option>
                </select>
                <input type="number" min={1} className="input sm:col-span-1" value={l.qty} onChange={(e) => upd(i, { qty: Number(e.target.value) })} />
                <input type="number" step="0.01" className="input sm:col-span-2" placeholder={listPrice(l) != null ? String(listPrice(l)) : 'Price'}
                  value={l.unit_price ?? ''} onChange={(e) => upd(i, { unit_price: e.target.value === '' ? undefined : Number(e.target.value) })} />
                <div className="flex items-center justify-between gap-1 sm:col-span-2">
                  <span className="text-sm font-semibold tabular-nums">{money(unit(l) * l.qty)}</span>
                  <button className="p-1 text-gray-400 hover:text-red-600" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-12">
                <select className="input col-span-2 sm:col-span-6" value={l.code || ''} onChange={(e) => upd(i, { code: e.target.value, unit_price: undefined })}>
                  {cfg.charges.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
                </select>
                <input type="number" min={1} className="input sm:col-span-1" value={l.qty} onChange={(e) => upd(i, { qty: Number(e.target.value) })} />
                <input type="number" step="0.01" className="input sm:col-span-3"
                  placeholder={cfg.charges.find((c) => c.code === l.code)?.amount != null ? String(cfg.charges.find((c) => c.code === l.code)?.amount) : 'Quoted amount'}
                  value={l.unit_price ?? ''} onChange={(e) => upd(i, { unit_price: e.target.value === '' ? undefined : Number(e.target.value) })} />
                <div className="flex items-center justify-between gap-1 sm:col-span-2">
                  <span className="text-sm font-semibold tabular-nums">{money(unit(l) * l.qty)}</span>
                  <button className="p-1 text-gray-400 hover:text-red-600" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, { kind: 'tank', qty: 1, grade: 'A' }])}><Plus size={14} /> Tank</button>
        {delivery && <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, { kind: 'charge', qty: 1, code: delivery.code }])}><Plus size={14} /> Delivery</button>}
        {cfg.charges.some((c) => c !== delivery) && <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, { kind: 'charge', qty: 1, code: (cfg.charges.find((c) => c !== delivery) || cfg.charges[0]).code }])}><Plus size={14} /> Charge</button>}
        <span className="ml-auto text-sm">Total <b className="tabular-nums">{money(total)}</b> <span className="text-gray-500">{tierDef?.prices_include_vat ? 'incl VAT' : 'excl VAT'} · {label(tier)}</span></span>
      </div>
    </div>
  )
}

export function cleanLines(lines: Line[]) {
  return lines.map((l) => ({ ...l, unit_price: l.unit_price === undefined || l.unit_price === null ? undefined : l.unit_price }))
}

// ---------------------------------------------------------------- customers

/** One search box over all customers: name, contact, phone (partial), email, suburb, ref.
 * Results as you type (150 ms), arrow keys and Enter on desktop, big rows on a phone. Empty box
 * shows customers active in the past 30 days. */
export function CustomerSearch({ onPick, autoFocus = true, action }: { onPick: (row: any) => void; autoFocus?: boolean; action?: React.ReactNode }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<any[] | null>(null)
  const [hi, setHi] = useState(0)
  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      api.get('/api/p/customers/search', { params: { q } }).then((r) => { if (live) { setRows(r.data); setHi(0) } })
    }, q ? 150 : 0)
    return () => { live = false; clearTimeout(t) }
  }, [q])
  const key = (e: React.KeyboardEvent) => {
    if (!rows?.length) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, rows.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
    if (e.key === 'Enter') { e.preventDefault(); onPick(rows[hi]) }
  }
  return (
    <div>
      <div className="flex gap-2">
        <input className="input" autoFocus={autoFocus} placeholder="Name, phone, suburb, email or MKC number" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={key} inputMode="search" />
        {action}
      </div>
      {!q && rows && rows.length > 0 && <div className="mt-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Recent</div>}
      <div className="mt-1 divide-y rounded-lg border">
        {rows === null ? <div className="px-3 py-3 text-sm text-gray-400">Searching…</div>
          : rows.length === 0 ? <div className="px-3 py-3 text-sm text-gray-400">{q ? 'No match' : 'Type to search'}</div>
          : rows.map((c, i) => (
            <button key={c._id} onMouseEnter={() => setHi(i)} onClick={() => onPick(c)}
              className={`flex w-full items-center justify-between gap-3 px-3 py-3 text-left ${i === hi ? 'bg-brand-primary/10' : 'hover:bg-gray-50'}`}>
              <div className="min-w-0">
                <div className="truncate font-semibold">{c.name}</div>
                <div className="truncate text-xs text-gray-500">
                  {[c.suburb, c.last_order_date ? `last order ${c.last_order_date}` : 'no orders yet', c.phone_last4 ? `••${c.phone_last4}` : '', c.ref].filter(Boolean).join(' · ')}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {c.payment_basis === 'terms' && <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-800">Terms</span>}
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${c.price_tier === 'trade' ? 'bg-violet-50 text-violet-800' : 'bg-gray-100 text-gray-700'}`}>{label(c.price_tier)}</span>
              </div>
            </button>
          ))}
      </div>
    </div>
  )
}

export function CustomerPicker({ value, onChange }: { value: any; onChange: (c: any) => void }) {
  const [adding, setAdding] = useState(false)
  const pick = async (row: any) => onChange((await api.get(`/api/p/customers/${row._id}`, { params: { brief: true } })).data)
  if (value) {
    return (
      <div className="flex items-center justify-between rounded-lg border bg-gray-50 p-3">
        <div><div className="font-semibold">{value.name}</div>
          <div className="text-xs text-gray-500">{[value.ref, label(value.payment_basis), label(value.price_tier), value.phone].filter(Boolean).join(' · ')}</div></div>
        <button className="btn-ghost btn-sm" onClick={() => onChange(null)}>Change</button>
      </div>
    )
  }
  return (
    <div>
      <CustomerSearch onPick={pick} action={<button className="btn-ghost" onClick={() => setAdding(true)}><Plus size={16} /> New</button>} />
      {adding && <CustomerForm onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); onChange(c) }} />}
    </div>
  )
}

export function CustomerForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: (c: any) => void }) {
  const { isAdmin, cfg } = useApp()
  const [c, setC] = useState<any>(initial || { name: '', phone: '', email: '', contact_person: '', billing_address: '', vat_number: '', payment_basis: 'upfront', price_tier: 'retail' })
  const [site, setSite] = useState<any>(initial ? null : { label: 'Main', address: '', town: '', maps_link: '' })
  const a = useAction()
  const [dup, setDup] = useState<{ message: string; matches: any[] } | null>(null)
  const save = (force = false) => a.run(async () => {
    try {
      const r = initial ? await api.put(`/api/p/customers/${initial._id}`, c)
        : await api.post('/api/p/customers', { ...c, force, sites: site && (site.address || site.maps_link || site.lat) ? [site] : [] })
      onSaved(r.data)
    } catch (e: any) {
      if (e?.response?.status === 409 && e.response.data?.detail?.matches) { setDup(e.response.data.detail); return }
      throw e
    }
  }, errMsg)
  const pickExisting = (m: any) => a.run(async () => onSaved((await api.get(`/api/p/customers/${m._id}`, { params: { brief: true } })).data), errMsg)
  const f = (k: string) => ({ value: c[k] || '', onChange: (e: any) => setC({ ...c, [k]: e.target.value }) })
  return (
    <Modal title={initial ? 'Edit customer' : 'New customer'} onClose={onClose} wide>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><input className="input" {...f('name')} /></Field>
        <Field label="Contact person"><input className="input" {...f('contact_person')} /></Field>
        <Field label="WhatsApp number"><input className="input" {...f('phone')} /></Field>
        <Field label="Email"><input className="input" {...f('email')} /></Field>
        <Field label="Billing address"><input className="input" {...f('billing_address')} /></Field>
        <Field label="VAT number"><input className="input" {...f('vat_number')} /></Field>
        {isAdmin && <>
          <Field label="Pays"><select className="input" {...f('payment_basis')}><option value="upfront">Upfront</option><option value="terms">On terms</option></select></Field>
          <Field label="Price tier"><select className="input" {...f('price_tier')}>{cfg.price_tiers.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}</select></Field>
          {c.payment_basis === 'terms' && <Field label="Terms (days)"><input type="number" className="input" {...f('payment_terms_days')} /></Field>}
        </>}
      </div>
      {site && <div className="mt-4"><div className="label">Delivery site</div><SiteFields site={site} setSite={setSite} /></div>}
      <ErrorBox msg={a.error} />
      {dup && <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
        <div className="font-semibold text-amber-900">{dup.message}</div>
        {dup.matches.map((m) => (
          <div key={m._id} className="mt-2 flex items-center justify-between gap-2">
            <span>{m.name}{m.suburb ? ` · ${m.suburb}` : ''}{m.ref ? ` · ${m.ref}` : ''} <span className="text-gray-500">(same {m.match})</span></span>
            <button className="btn-primary btn-sm" disabled={a.busy} onClick={() => pickExisting(m)}>Use existing</button>
          </div>
        ))}
        <div className="mt-3 flex justify-end"><button className="btn-ghost btn-sm" disabled={a.busy} onClick={() => save(true)}>Create anyway</button></div>
      </div>}
      <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={a.busy || !c.name} onClick={() => save()}>Save</button></div>
    </Modal>
  )
}

// ---------------------------------------------------------------- sites and maps

export const NEW_SITE = '__new'

/** Where the order goes: one of the customer's addresses, or a new one typed in here. A new
 * address is saved on the customer (so it is there next time) when the order is saved. */
export function DeliverTo({ customer, siteId, setSiteId, site, setSite, optional }: {
  customer: any; siteId: string; setSiteId: (v: string) => void; site: any; setSite: (s: any) => void; optional?: boolean
}) {
  const sites = customer.sites || []
  const choose = (v: string) => {
    setSiteId(v)
    if (v === NEW_SITE && !site) setSite({ label: sites.length ? `Delivery ${sites.length + 1}` : 'Delivery', address: '', town: '', maps_link: '' })
  }
  // no address on file: go straight to typing one
  useEffect(() => { if (!optional && sites.length === 0 && !siteId) choose(NEW_SITE) }, [customer?._id])  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="sm:col-span-2">
      <Field label={optional ? 'Delivery address (optional)' : 'Deliver to'}>
        <select className="input" value={siteId} onChange={(e) => choose(e.target.value)}>
          {optional && <option value="">Not yet</option>}
          {sites.map((s: any) => <option key={s.site_id} value={s.site_id}>{[s.label, s.address, s.town].filter(Boolean).join(', ')}</option>)}
          <option value={NEW_SITE}>+ New address…</option>
        </select>
      </Field>
      {siteId === NEW_SITE && site && <div className="mt-2 rounded-lg border p-3"><SiteFields site={site} setSite={setSite} /></div>}
    </div>
  )
}

/** Save a new address typed on an order or quote; returns the site id to use. */
export async function siteIdFor(customer: any, siteId: string, site: any, onSaved?: (site: any) => void): Promise<string | undefined> {
  if (siteId !== NEW_SITE) return siteId || undefined
  if (!site || !(site.address || site.town || site.maps_link || (site.lat != null && site.lat !== ''))) {
    throw new Error('Type the new address, paste a Maps link or pin it')
  }
  const r = await api.post(`/api/p/customers/${customer._id}/sites`, site)
  onSaved?.(r.data)         // a retry after a failed save reuses it instead of adding it twice
  return r.data.site_id
}

/** Paste a Google Maps link, use my location, or type coordinates. The link is always kept;
 * coordinates are parsed server-side (short links resolved). A link that does not parse shows
 * "location not pinned" so it is never silently wrong. */
export function SiteFields({ site, setSite }: { site: any; setSite: (s: any) => void }) {
  const [status, setStatus] = useState<string | null>(null)
  const pinned = site.lat != null && site.lng != null && site.lat !== '' && site.lng !== ''
  const paste = async (url: string) => {
    setSite({ ...site, maps_link: url, lat: null, lng: null })
    if (!url) return
    setStatus('Finding the pin…')
    try {
      const r = await api.post('/api/p/maps/locate', { url })
      setSite({ ...site, maps_link: url, lat: r.data.lat, lng: r.data.lng })
      setStatus(r.data.pinned ? null : 'Location not pinned: the link is kept, but add coordinates or use your location.')
    } catch { setStatus('Location not pinned.') }
  }
  const here = () => {
    if (!navigator.geolocation) return setStatus('This device cannot share its location.')
    setStatus('Getting your location…')
    navigator.geolocation.getCurrentPosition(
      (p) => { setSite({ ...site, lat: +p.coords.latitude.toFixed(6), lng: +p.coords.longitude.toFixed(6) }); setStatus(null) },
      () => setStatus('Location permission denied.'), { enableHighAccuracy: true, timeout: 15000 })
  }
  const f = (k: string) => ({ value: site[k] ?? '', onChange: (e: any) => setSite({ ...site, [k]: e.target.value }) })
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Label"><input className="input" placeholder="Farm, Komani road" {...f('label')} /></Field>
        <Field label="Town"><input className="input" {...f('town')} /></Field>
        <Field label="Address"><input className="input" {...f('address')} /></Field>
        <Field label="Google Maps link"><input className="input" placeholder="Paste the link" value={site.maps_link || ''} onChange={(e) => paste(e.target.value.trim())} /></Field>
        <Field label="Site contact"><input className="input" {...f('contact_name')} /></Field>
        <Field label="Contact phone"><input className="input" {...f('contact_phone')} /></Field>
      </div>
      <Field label="Directions"><textarea className="input" rows={2} placeholder="Gate code, second turn after the silo" {...f('directions')} /></Field>
      <div className="flex flex-wrap items-end gap-2">
        <button type="button" className="btn-ghost btn-sm" onClick={here}><Crosshair size={14} /> Use my location</button>
        <input className="input w-32" placeholder="Latitude" value={site.lat ?? ''} onChange={(e) => setSite({ ...site, lat: e.target.value === '' ? null : Number(e.target.value) })} />
        <input className="input w-32" placeholder="Longitude" value={site.lng ?? ''} onChange={(e) => setSite({ ...site, lng: e.target.value === '' ? null : Number(e.target.value) })} />
        <span className={`text-xs font-semibold ${pinned ? 'text-emerald-700' : 'text-amber-700'}`}><MapPin size={12} className="inline" /> {pinned ? 'Pinned' : 'Location not pinned'}</span>
      </div>
      {status && <p className="text-xs text-amber-700">{status}</p>}
      {pinned && <MapPreview lat={Number(site.lat)} lng={Number(site.lng)} />}
    </div>
  )
}

export function MapPreview({ lat, lng }: { lat: number; lng: number }) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  useEffect(() => {
    if (!el.current) return
    if (!map.current) {
      map.current = L.map(el.current, { zoomControl: false, attributionControl: true }).setView([lat, lng], 14)
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 19 }).addTo(map.current)
    }
    map.current.setView([lat, lng], 14)
    map.current.eachLayer((l) => { if (l instanceof L.CircleMarker) l.remove() })
    L.circleMarker([lat, lng], { radius: 8, color: '#fff', weight: 2, fillColor: '#d33', fillOpacity: 1 }).addTo(map.current)
  }, [lat, lng])
  useEffect(() => () => { map.current?.remove(); map.current = null }, [])
  return <div ref={el} className="h-44 w-full overflow-hidden rounded-lg border" />
}

export function SiteModal({ customerId, initial, onClose, onSaved }: { customerId: string; initial?: any; onClose: () => void; onSaved: () => void }) {
  const [site, setSite] = useState<any>(initial || { label: '', address: '', town: '', maps_link: '' })
  const a = useAction()
  return (
    <Modal title={initial ? 'Edit site' : 'Add delivery site'} onClose={onClose} wide>
      <SiteFields site={site} setSite={setSite} />
      <ErrorBox msg={a.error} />
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={a.busy} onClick={() => a.run(async () => { await api.post(`/api/p/customers/${customerId}/sites`, site); onSaved() }, errMsg)}>Save site</button>
      </div>
    </Modal>
  )
}
