import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Columns3, Download, Filter, Search, Star, X } from 'lucide-react'
import { Fragment, ReactNode, useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, downloadCsv, errMsg } from '../api'
import { lineLabel, useApp } from '../lib/ctx'
import { date, label, money, money0, today } from '../lib/format'
import { PayLinkModal, RecordPayment, ScheduleDelivery, sendToDriver, ShareModal } from './Actions'
import { Chip, Empty, ErrorBox, Spinner, Status } from './ui'

export type Kind = 'orders' | 'deliveries' | 'invoices'
export interface Sort { key?: string; dir?: 'asc' | 'desc' }
export interface View { _id: string; name: string; kind: Kind; filters: Record<string, any>; sort: Sort; columns?: string[]; default?: boolean }

interface Col { key: string; label: string; render: (r: any) => ReactNode; align?: 'right'; sortable?: boolean; always?: boolean }

const ORDER_COLS: Col[] = [
  { key: 'order_number', label: 'Order', render: (r) => <span className="font-semibold text-brand-dark">{r.order_number}</span>, always: true },
  { key: 'date', label: 'Date', render: (r) => date(r.date) },
  { key: 'customer', label: 'Customer', render: (r) => <span className="font-medium">{r.customer}</span>, always: true },
  { key: 'site', label: 'Site', render: (r) => <span className="text-gray-600">{[r.site, r.town].filter(Boolean).join(', ')}</span> },
  { key: 'requested_date', label: 'Requested', render: (r) => date(r.requested_date) },
  { key: 'value', label: 'Value', render: (r) => money(r.value), align: 'right' },
  { key: 'invoiced', label: 'Invoiced', render: (r) => money(r.invoiced), align: 'right' },
  { key: 'paid', label: 'Paid', render: (r) => money(r.paid), align: 'right' },
  { key: 'balance', label: 'Balance', render: (r) => <span className={r.balance > 1 ? 'font-semibold' : 'text-gray-400'}>{money(r.balance)}</span>, align: 'right' },
  { key: 'payment_status', label: 'Payment', render: (r) => <span className="flex gap-1"><Status s={r.payment_status} />{r.overdue && <Status s="overdue" />}</span>, always: true },
  { key: 'delivery_status', label: 'Delivery', render: (r) => <span className="flex items-center gap-1"><Status s={r.delivery_status} /><span className="text-xs text-gray-500">{r.delivered_label}</span></span>, always: true },
  { key: 'basis', label: 'Basis', render: (r) => <Chip tone={r.basis === 'cod' ? 'violet' : r.basis === 'terms' ? 'blue' : 'grey'}>{label(r.basis)}</Chip> },
  { key: 'methods', label: 'Received by', render: (r) => <span className="text-xs text-gray-600">{(r.methods || []).map(label).join(', ')}</span>, sortable: false },
  { key: 'age_days', label: 'Age', render: (r) => `${r.age_days}d`, align: 'right' },
  { key: 'next_action', label: 'Next', render: (r) => <span className="text-xs font-semibold text-brand-primary">{r.next_action?.label}</span>, always: true },
]

const DN_COLS: Col[] = [
  { key: 'dn_number', label: 'DN', render: (r) => <span className="font-semibold text-brand-dark">{r.dn_number}</span>, always: true },
  { key: 'order_number', label: 'Order', render: (r) => r.order_number },
  { key: 'customer', label: 'Customer', render: (r) => r.customer, always: true },
  { key: 'site', label: 'Site', render: (r) => [r.site, r.town].filter(Boolean).join(', ') },
  { key: 'planned_date', label: 'Planned', render: (r) => date(r.planned_date) },
  { key: 'driver', label: 'Driver', render: (r) => r.driver },
  { key: 'delivery_status', label: 'Status', render: (r) => <Status s={r.delivery_status} />, always: true },
  { key: 'pod', label: 'POD', render: (r) => (r.pod ? 'Yes' : '') },
  { key: 'to_collect', label: 'To collect', render: (r) => (r.to_collect ? money(r.to_collect) : ''), align: 'right' },
  { key: 'collected', label: 'Collected', render: (r) => (r.collected != null ? money(r.collected) : ''), align: 'right' },
]

const INV_COLS: Col[] = [
  { key: 'number', label: 'Invoice', render: (r) => <span className="font-semibold text-brand-dark">{r.number}</span>, always: true },
  { key: 'order_number', label: 'Order', render: (r) => r.order_number },
  { key: 'customer', label: 'Customer', render: (r) => r.customer, always: true },
  { key: 'date', label: 'Date', render: (r) => date(r.date) },
  { key: 'due_date', label: 'Due', render: (r) => date(r.due_date) },
  { key: 'total', label: 'Total', render: (r) => money(r.total), align: 'right' },
  { key: 'paid', label: 'Paid', render: (r) => money(r.paid), align: 'right' },
  { key: 'balance', label: 'Balance', render: (r) => money(r.balance), align: 'right' },
  { key: 'status', label: 'Status', render: (r) => <Status s={r.status} />, always: true },
  { key: 'days_overdue', label: 'Overdue', render: (r) => (r.days_overdue ? <span className="font-semibold text-red-700">{r.days_overdue}d</span> : ''), align: 'right' },
]

export const COLS: Record<Kind, Col[]> = { orders: ORDER_COLS, deliveries: DN_COLS, invoices: INV_COLS }

// Columns off until the user turns them on (the full set does not fit a laptop screen).
const DEFAULT_HIDDEN: Record<Kind, string[]> = { orders: ['requested_date', 'invoiced', 'methods', 'basis'], deliveries: [], invoices: [] }

function hiddenCols(kind: Kind): string[] {
  const raw = localStorage.getItem(`cols_${kind}`)
  return raw ? JSON.parse(raw) : DEFAULT_HIDDEN[kind]
}

const FILTERS: Record<Kind, { key: string; label: string; options: string[] }[]> = {
  orders: [
    { key: 'payment_status', label: 'Payment', options: ['not_invoiced', 'unpaid', 'part_paid', 'paid', 'overdue', 'cod_outstanding'] },
    { key: 'delivery_status', label: 'Delivery', options: ['not_delivered', 'scheduled', 'out_for_delivery', 'part_delivered', 'delivered', 'cancelled'] },
    { key: 'order_status', label: 'Order', options: ['open', 'complete', 'cancelled'] },
    { key: 'basis', label: 'Basis', options: ['upfront', 'terms', 'cod'] },
    { key: 'method', label: 'Received by', options: ['yoco_link', 'yoco_machine', 'cash', 'eft'] },
  ],
  deliveries: [{ key: 'delivery_status', label: 'Status', options: ['scheduled', 'out_for_delivery', 'delivered', 'failed', 'void'] }],
  invoices: [{ key: 'status', label: 'Status', options: ['unpaid', 'part_paid', 'paid', 'overdue', 'flagged', 'void'] }],
}

export default function Board({ kind, filters, setFilters, sort, setSort, onKind, readOnlyBase }: {
  kind: Kind; filters: Record<string, any>; setFilters: (f: Record<string, any>) => void
  sort: Sort; setSort: (s: Sort) => void; onKind: (k: Kind) => void; readOnlyBase?: string
}) {
  const { cfg } = useApp()
  const navigate = useNavigate()
  const base = readOnlyBase || '/api/p/dashboard'
  const [rows, setRows] = useState<any[] | null>(null)
  const [totals, setTotals] = useState<any>({})
  const [views, setViews] = useState<View[]>([])
  const [q, setQ] = useState(filters.q || '')
  const [showFilters, setShowFilters] = useState(false)
  const [showCols, setShowCols] = useState(false)
  const [hidden, setHidden] = useState<string[]>(() => hiddenCols(kind))
  const [expanded, setExpanded] = useState<string | null>(null)
  const [modal, setModal] = useState<ReactNode>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setErr(null)
    try {
      const r = await api.get(`${base}/board/${kind}`, { params: { filters: JSON.stringify(filters), sort: JSON.stringify(sort) } })
      setRows(r.data.rows)
      setTotals(r.data.totals)
    } catch (e) { setErr(errMsg(e)) }
  }, [base, kind, filters, sort])
  useEffect(() => { setRows(null); load() }, [load])
  useEffect(() => { if (!readOnlyBase) api.get('/api/p/dashboard/views').then((r) => setViews(r.data)) }, [readOnlyBase])
  useEffect(() => { setHidden(hiddenCols(kind)) }, [kind])
  useEffect(() => {
    const t = setTimeout(() => { if ((filters.q || '') !== q) setFilters({ ...filters, q: q || undefined }) }, 300)
    return () => clearTimeout(t)
  }, [q]) // eslint-disable-line

  const cols = COLS[kind].filter((c) => c.always || !hidden.includes(c.key))
  const toggleSort = (key: string) => setSort(sort.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' })
  const toggleFilter = (key: string, opt: string) => {
    const cur: string[] = filters[key] || (key === 'order_status' && kind === 'orders' && !(key in filters) ? ['open'] : [])
    const next = cur.includes(opt) ? cur.filter((x) => x !== opt) : [...cur, opt]
    setFilters({ ...filters, [key]: next })
  }
  const activeChips = Object.entries(filters).flatMap(([k, v]) => {
    if (k === 'q' || v === undefined || v === null || v === false) return []
    if (Array.isArray(v)) return v.map((x) => ({ k, v: x, text: label(x) }))
    if (v === true) return [{ k, v, text: label(k) }]
    return [{ k, v, text: `${label(k)}: ${v}` }]
  })
  const removeChip = (k: string, v: any) => {
    const cur = filters[k]
    if (Array.isArray(cur)) setFilters({ ...filters, [k]: cur.filter((x: any) => x !== v) })
    else { const n = { ...filters }; delete n[k]; setFilters(n) }
  }
  const saveView = async () => {
    const name = prompt('Name this view')
    if (!name) return
    await api.post('/api/p/dashboard/views', { name, kind, filters, sort, columns: cols.map((c) => c.key) })
    setViews((await api.get('/api/p/dashboard/views')).data)
  }
  const applyView = (v: View) => { onKind(v.kind); setFilters(v.filters); setSort(v.sort || {}); setQ(v.filters.q || '') }
  const open = (r: any) => navigate(kind === 'orders' ? `/orders/${r._id}` : kind === 'deliveries' ? `/deliveries/${r._id}` : `/invoices/${r._id}`)

  const actions = (r: any) => {
    if (readOnlyBase || kind !== 'orders' || r.order_status !== 'open') return null
    const inv = (r.timeline?.invoices || []).find((i: any) => i.status !== 'void' && i.status !== 'paid')
    const sched = (r.timeline?.dns || []).find((d: any) => !d.void && d.delivery_status === 'scheduled')
    const toInvoice = (r.timeline?.dns || []).find((d: any) => !d.void && !d.invoiced)
    const btn = 'btn-ghost btn-sm'
    return (
      <div className="flex flex-wrap gap-1.5" onClick={(e) => e.stopPropagation()}>
        {inv && r.basis !== 'terms' && <button className={btn} onClick={() => setModal(<PayLinkModal invoiceId={inv._id} onClose={() => { setModal(null); load() }} />)}>Pay link</button>}
        {inv && <button className={btn} onClick={() => setModal(<RecordPayment customerId={r.customer_id} invoiceId={inv._id} defaultAmount={r.balance} methods={['cash', 'yoco_machine']} onClose={() => setModal(null)} onDone={() => { setModal(null); load() }} />)}>Record cash</button>}
        {r.qty_remaining > 0 && <button className={btn} onClick={() => setModal(<ScheduleDelivery orderId={r._id} onClose={() => setModal(null)} onDone={() => { setModal(null); load() }} />)}>Schedule</button>}
        {sched && <button className={btn} onClick={async () => {
          try { const d = await sendToDriver(sched._id); setModal(<ShareModal title="Send to driver" data={d} onClose={() => { setModal(null); load() }} />) } catch (e) { setErr(errMsg(e)) }
        }}>Send to driver</button>}
        {toInvoice && r.basis === 'terms' && <button className={btn} onClick={async () => {
          try { await api.post(`/api/p/deliveries/${toInvoice._id}/invoice`); load() } catch (e) { setErr(errMsg(e)) }
        }}>Invoice {toInvoice.dn_number}</button>}
      </div>
    )
  }

  const expandRow = (r: any) => (
    <div className="grid gap-4 p-3 sm:grid-cols-3">
      <div>
        <div className="label">Lines</div>
        {(r.lines || []).map((l: any) => (
          <div key={l.line_id} className="flex justify-between gap-2 text-sm">
            <span>{lineLabel(cfg, l)}</span>
            {l.kind === 'charge' ? <span className="text-gray-500">{money(l.line_total)}</span>
              : <span className="tabular-nums text-gray-600">{l.qty_ordered} ord · {l.qty_delivered} del · <b>{l.qty_remaining}</b> left</span>}
          </div>
        ))}
      </div>
      <div>
        <div className="label">Deliveries</div>
        {(r.timeline?.dns || []).length === 0 && <span className="text-sm text-gray-400">None yet</span>}
        {(r.timeline?.dns || []).map((d: any) => (
          <div key={d._id} className={`flex items-center justify-between gap-2 text-sm ${d.void ? 'line-through opacity-50' : ''}`}>
            <span>{d.dn_number} · {date(d.date)}{d.driver ? ` · ${d.driver}` : ''}</span><Status s={d.delivery_status} />
          </div>
        ))}
      </div>
      <div>
        <div className="label">Invoices</div>
        {(r.timeline?.invoices || []).length === 0 && <span className="text-sm text-gray-400">None yet</span>}
        {(r.timeline?.invoices || []).map((i: any) => (
          <div key={i._id} className="flex items-center justify-between gap-2 text-sm">
            <span>{i.number} · {money(i.paid)} / {money(i.total)}</span><Status s={i.status} />
          </div>
        ))}
      </div>
      <div className="sm:col-span-3">{actions(r)}</div>
    </div>
  )

  return (
    <div className="card">
      <div className="flex flex-wrap items-center gap-2 border-b p-3">
        <div className="flex gap-1 rounded-lg bg-gray-100 p-1">
          {(['orders', 'deliveries', 'invoices'] as Kind[]).map((k) => (
            <button key={k} onClick={() => { onKind(k); setFilters({}); setSort({}) }}
              className={`rounded-md px-3 py-1.5 text-sm font-semibold ${kind === k ? 'bg-white text-brand-dark shadow-sm' : 'text-gray-600'}`}>
              {label(k)}
            </button>
          ))}
        </div>
        <div className="relative min-w-[180px] flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input className="input pl-9" placeholder="Search number, customer, site" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <button className="btn-ghost btn-sm" onClick={() => setShowFilters(!showFilters)}><Filter size={14} /> Filters</button>
        <button className="btn-ghost btn-sm hidden sm:inline-flex" onClick={() => setShowCols(!showCols)}><Columns3 size={14} /> Columns</button>
        {!readOnlyBase && <button className="btn-ghost btn-sm" onClick={saveView}><Star size={14} /> Save view</button>}
        <button className="btn-ghost btn-sm" onClick={() => downloadCsv(`${base}/board/${kind}.csv?filters=${encodeURIComponent(JSON.stringify(filters))}&sort=${encodeURIComponent(JSON.stringify(sort))}`, `${kind}-${today()}.csv`)}><Download size={14} /> CSV</button>
      </div>

      {!readOnlyBase && views.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-b px-3 py-2">
          {views.filter((v) => v.kind === kind || kind === 'orders').map((v) => (
            <button key={v._id} onClick={() => applyView(v)} className="whitespace-nowrap rounded-full border px-3 py-1 text-xs font-semibold text-gray-700 hover:border-brand-primary hover:text-brand-primary">
              {v.name}
              {!v.default && <span className="ml-1 text-gray-400" onClick={async (e) => { e.stopPropagation(); if (confirm(`Delete view ${v.name}?`)) { await api.delete(`/api/p/dashboard/views/${v._id}`); setViews((await api.get('/api/p/dashboard/views')).data) } }}>×</span>}
            </button>
          ))}
        </div>
      )}

      {showFilters && (
        <div className="space-y-3 border-b bg-gray-50 p-3">
          {FILTERS[kind].map((f) => {
            const cur: string[] = filters[f.key] ?? (f.key === 'order_status' ? ['open'] : [])
            return (
              <div key={f.key}>
                <div className="label">{f.label}</div>
                <div className="flex flex-wrap gap-1.5">
                  {f.options.map((o) => (
                    <button key={o} onClick={() => toggleFilter(f.key, o)}
                      className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ${cur.includes(o) ? 'bg-brand-primary text-white ring-brand-primary' : 'bg-white text-gray-700 ring-gray-300'}`}>
                      {label(o)}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
          <div className="grid gap-2 sm:grid-cols-4">
            <select className="input" value={filters.tank_type || ''} onChange={(e) => setFilters({ ...filters, tank_type: e.target.value || undefined })}>
              <option value="">Any tank</option>{cfg.tank_types.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
            <select className="input" value={filters.colour || ''} onChange={(e) => setFilters({ ...filters, colour: e.target.value || undefined })}>
              <option value="">Any colour</option>{cfg.colours.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
            <input className="input" type="date" value={filters.date_from || ''} onChange={(e) => setFilters({ ...filters, date_from: e.target.value || undefined })} title="From" />
            <input className="input" type="date" value={filters.date_to || ''} onChange={(e) => setFilters({ ...filters, date_to: e.target.value || undefined })} title="To" />
            {kind === 'orders' && <>
              <input className="input" placeholder="Town" value={filters.town || ''} onChange={(e) => setFilters({ ...filters, town: e.target.value || undefined })} />
              <input className="input" type="date" value={filters.requested_from || ''} onChange={(e) => setFilters({ ...filters, requested_from: e.target.value || undefined })} title="Requested from" />
              <input className="input" type="date" value={filters.requested_to || ''} onChange={(e) => setFilters({ ...filters, requested_to: e.target.value || undefined })} title="Requested to" />
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!filters.made_to_order} onChange={(e) => setFilters({ ...filters, made_to_order: e.target.checked || undefined })} /> Made to order</label>
            </>}
          </div>
        </div>
      )}

      {showCols && (
        <div className="flex flex-wrap gap-2 border-b bg-gray-50 p-3">
          {COLS[kind].filter((c) => !c.always).map((c) => (
            <label key={c.key} className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={!hidden.includes(c.key)} onChange={() => {
                const n = hidden.includes(c.key) ? hidden.filter((x) => x !== c.key) : [...hidden, c.key]
                setHidden(n); localStorage.setItem(`cols_${kind}`, JSON.stringify(n))
              }} /> {c.label}
            </label>
          ))}
        </div>
      )}

      {activeChips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
          {activeChips.map((c, i) => (
            <button key={i} onClick={() => removeChip(c.k, c.v)} className="inline-flex items-center gap-1 rounded-full bg-brand-primary/10 px-2.5 py-0.5 text-xs font-semibold text-brand-dark">
              {c.text} <X size={12} />
            </button>
          ))}
          <button className="text-xs text-gray-500 underline" onClick={() => { setFilters({}); setQ('') }}>Clear</button>
        </div>
      )}

      <ErrorBox msg={err} />
      {rows === null ? <Spinner /> : rows.length === 0 ? <Empty text="Nothing matches." /> : (
        <>
          {/* desktop table */}
          <div className="hidden overflow-x-auto md:block">
            <table className="min-w-full">
              <thead className="border-b bg-gray-50">
                <tr>
                  {kind === 'orders' && <th className="w-8" />}
                  {cols.map((c) => (
                    <th key={c.key} className={`th ${c.align === 'right' ? 'text-right' : ''} ${c.sortable === false ? '' : 'cursor-pointer select-none hover:text-gray-900'}`}
                      onClick={() => c.sortable !== false && toggleSort(c.key)}>
                      <span className="inline-flex items-center gap-1">{c.label}
                        {sort.key === c.key && (sort.dir === 'desc' ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Fragment key={r._id}>
                    <tr className="cursor-pointer border-b hover:bg-gray-50" onClick={() => open(r)}>
                      {kind === 'orders' && (
                        <td className="pl-2" onClick={(e) => { e.stopPropagation(); setExpanded(expanded === r._id ? null : r._id) }}>
                          {expanded === r._id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </td>
                      )}
                      {cols.map((c) => <td key={c.key} className={`td ${c.align === 'right' ? 'text-right tabular-nums' : ''}`}>{c.render(r)}</td>)}
                    </tr>
                    {expanded === r._id && <tr className="border-b bg-gray-50/60"><td colSpan={cols.length + 1}>{expandRow(r)}</td></tr>}
                  </Fragment>
                ))}
              </tbody>
              <tfoot className="bg-gray-50 font-semibold">
                <tr>
                  {kind === 'orders' && <td />}
                  {cols.map((c, i) => (
                    <td key={c.key} className={`td ${c.align === 'right' ? 'text-right tabular-nums' : ''}`}>
                      {i === 0 ? `${totals.count} ${kind}` : totals[c.key] !== undefined ? money(totals[c.key]) : ''}
                    </td>
                  ))}
                </tr>
              </tfoot>
            </table>
          </div>
          {/* mobile cards */}
          <div className="divide-y md:hidden">
            {rows.map((r) => (
              <div key={r._id} className="p-3" onClick={() => open(r)}>
                {kind === 'orders' ? (
                  <>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0"><div className="font-semibold text-brand-dark">{r.order_number} · {r.customer}</div>
                        <div className="truncate text-xs text-gray-500">{[r.site, r.town].filter(Boolean).join(', ')} · {date(r.date)} · {r.age_days}d</div></div>
                      <div className="text-right"><div className="font-bold tabular-nums">{money0(r.value)}</div>
                        {r.balance > 1 && <div className="text-xs text-gray-500">due {money0(r.balance)}</div>}</div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Status s={r.payment_status} />{r.overdue && <Status s="overdue" />}<Status s={r.delivery_status} />
                      <span className="text-xs text-gray-500">{r.delivered_label}</span>
                      <span className="ml-auto text-xs font-semibold text-brand-primary">{r.next_action?.label}</span>
                    </div>
                    {r.order_status === 'open' && !readOnlyBase && <div className="mt-2">{actions(r)}</div>}
                  </>
                ) : kind === 'deliveries' ? (
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0"><div className="font-semibold">{r.dn_number} · {r.customer}</div>
                      <div className="text-xs text-gray-500">{date(r.planned_date)}{r.driver ? ` · ${r.driver}` : ''}{r.to_collect ? ` · collect ${money0(r.to_collect)}` : ''}</div></div>
                    <Status s={r.delivery_status} />
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0"><div className="font-semibold">{r.number} · {r.customer}</div>
                      <div className="text-xs text-gray-500">due {date(r.due_date)}{r.days_overdue ? ` · ${r.days_overdue}d overdue` : ''}</div></div>
                    <div className="text-right"><div className="font-bold tabular-nums">{money0(r.balance)}</div><Status s={r.status} /></div>
                  </div>
                )}
              </div>
            ))}
            <div className="bg-gray-50 p-3 text-sm font-semibold">
              {totals.count} {kind}{totals.value !== undefined ? ` · ${money(totals.value)} · balance ${money(totals.balance)}` : totals.total !== undefined ? ` · balance ${money(totals.balance)}` : ''}
            </div>
          </div>
        </>
      )}
      {modal}
    </div>
  )
}
