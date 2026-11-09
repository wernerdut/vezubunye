import { ClipboardList, Plus, Printer, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { ShareBar } from '../components/Actions'
import Board, { Kind, Sort } from '../components/Board'
import { Modal, PageTitle, Stat } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { date as fmtDate, label, money0 } from '../lib/format'

const PERIODS = [
  { key: 'today', label: 'Today' }, { key: 'week', label: 'This week' }, { key: 'month', label: 'This month' },
  { key: 'last_month', label: 'Last month' }, { key: 'custom', label: 'Custom' },
]

export default function Dashboard({ readOnlyNode }: { readOnlyNode?: string }) {
  const { cfg, isAdmin, brand } = useApp()
  const navigate = useNavigate()
  const [period, setPeriod] = useState('month')
  const [range, setRange] = useState({ start: '', end: '' })
  const [t, setT] = useState<any>(null)
  const [kind, setKind] = useState<Kind>('orders')
  const [filters, setFilters] = useState<Record<string, any>>({})
  const [sort, setSort] = useState<Sort>({ key: 'date', dir: 'desc' })
  const [prodList, setProdList] = useState(false)
  const base = readOnlyNode ? `/api/nodes/${readOnlyNode}/commercial` : '/api/p/dashboard'

  useEffect(() => {
    if (period === 'custom' && (!range.start || !range.end)) return
    api.get(`${base}/tiles`, { params: { period, ...(period === 'custom' ? range : {}) } }).then((r) => setT(r.data))
  }, [period, range, base])

  const go = (k: Kind, f: Record<string, any>, s: Sort = {}) => {
    setKind(k); setFilters(f); setSort(s)
    document.getElementById('board')?.scrollIntoView({ behavior: 'smooth' })
  }
  const is = (k: Kind, key: string, v?: any) => kind === k && (v === undefined ? !!filters[key] : JSON.stringify(filters[key]) === JSON.stringify(v))

  return (
    <div>
      <PageTitle title={brand.display_name} sub="Every order, its payment and its delivery."
        actions={!readOnlyNode && <>
          <button className="btn-ghost" onClick={() => navigate('/quick-sale')}><Zap size={16} /> Quick sale</button>
          <button className="btn-primary" onClick={() => navigate('/orders/new')}><Plus size={16} /> New order</button>
        </>} />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {PERIODS.map((p) => (
          <button key={p.key} onClick={() => setPeriod(p.key)}
            className={`rounded-full px-3 py-1 text-xs font-semibold ring-1 ${period === p.key ? 'bg-brand-dark text-white ring-brand-dark' : 'bg-white text-gray-700 ring-gray-300'}`}>{p.label}</button>
        ))}
        {period === 'custom' && <>
          <input type="date" className="input w-auto py-1" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} />
          <input type="date" className="input w-auto py-1" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} />
        </>}
      </div>

      {t && (
        <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <Group title="To deliver">
            <Stat label="Tanks" value={t.to_deliver.qty} sub={money0(t.to_deliver.value)} onClick={() => go('orders', { to_deliver: true }, { key: 'requested_date', dir: 'asc' })} active={is('orders', 'to_deliver')} />
            <Stat label="Oldest" value={`${t.to_deliver.oldest_days}d`} />
            <Stat label="Made to order" value={t.to_deliver.made_to_order_lines} onClick={() => go('orders', { made_to_order: true })} active={is('orders', 'made_to_order')} />
            {t.to_deliver.shortfall.length > 0 && (
              <div className="col-span-3 rounded-lg bg-red-50 p-2 text-xs text-red-800">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-semibold">Short of stock</div>
                  <button className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-0.5 font-semibold text-red-800 ring-1 ring-red-200 hover:bg-red-100"
                    onClick={() => setProdList(true)}><ClipboardList size={13} /> Production list</button>
                </div>
                {t.to_deliver.shortfall.map((s: any, i: number) => (
                  <div key={i}>{tankName(cfg, s.tank_type)} {colourOf(cfg, s.colour)?.name || ''} {s.grade !== 'A' ? `(${s.grade})` : ''}: need {s.remaining}, have {s.on_hand}</div>
                ))}
              </div>
            )}
          </Group>
          <Group title="Payments">
            <Stat label="Received" value={money0(t.payments.received)} sub={Object.entries(t.payments.by_method).map(([k, v]) => `${label(k)} ${money0(v as number)}`).join(' · ') || 'none'} />
            <Stat label="Pay links unpaid" value={t.payments.pay_links_unpaid.count} sub={money0(t.payments.pay_links_unpaid.value)} onClick={() => go('orders', { payment_status: ['unpaid', 'part_paid'] }, { key: 'date', dir: 'asc' })} active={is('orders', 'payment_status', ['unpaid', 'part_paid'])} />
            <Stat label="Collect on delivery" value={money0(t.payments.cod_outstanding)} onClick={() => go('orders', { payment_status: ['cod_outstanding'] })} active={is('orders', 'payment_status', ['cod_outstanding'])} />
            {isAdmin && t.payments.cash_on_hand && (
              <div className="col-span-3 rounded-lg bg-gray-50 p-2 text-xs">
                <span className="font-semibold">Cash on hand: </span>
                {t.payments.cash_on_hand.length === 0 ? 'none' : t.payments.cash_on_hand.map((h: any) => `${h.holder.name} ${money0(h.amount)} (since ${h.oldest})`).join(' · ')}
              </div>
            )}
          </Group>
          <Group title="Invoices">
            <Stat label="Invoiced" value={money0(t.invoices.invoiced)} />
            <Stat label="Outstanding" value={money0(t.invoices.outstanding)} onClick={() => go('invoices', { status: ['unpaid', 'part_paid'] }, { key: 'due_date', dir: 'asc' })} active={is('invoices', 'status', ['unpaid', 'part_paid'])} />
            <Stat label="Overdue" value={money0(t.invoices.overdue)} tone={t.invoices.overdue > 0 ? 'red' : 'default'} onClick={() => go('orders', { payment_status: ['overdue'] }, { key: 'balance', dir: 'desc' })} active={is('orders', 'payment_status', ['overdue'])} />
            <Aging a={t.invoices.aging} />
          </Group>
          <Group title="Deliveries">
            <Stat label="Scheduled today" value={t.deliveries.scheduled_today} onClick={() => go('deliveries', { delivery_status: ['scheduled'], today: true })} active={is('deliveries', 'today')} />
            <Stat label="Out now" value={t.deliveries.out_now} onClick={() => go('deliveries', { delivery_status: ['out_for_delivery'] })} active={is('deliveries', 'delivery_status', ['out_for_delivery'])} />
            <Stat label="Delivered" value={t.deliveries.delivered} sub={t.deliveries.failed ? `${t.deliveries.failed} failed` : undefined} onClick={() => go('deliveries', { delivery_status: ['delivered'] })} active={is('deliveries', 'delivery_status', ['delivered'])} />
            {t.deliveries.unpinned > 0 && <Stat label="Not pinned" value={t.deliveries.unpinned} sub="ask for a location" tone="red" onClick={() => go('deliveries', { unpinned: true })} active={is('deliveries', 'unpinned')} />}
          </Group>
          <Group title={cfg.proforma ? 'Proformas' : 'Quotes'}>
            <Stat label="Open" value={t.quotes.open} sub={money0(t.quotes.open_value)} onClick={() => navigate('/quotes')} />
            {!cfg.proforma && <Stat label="Expiring 7 days" value={t.quotes.expiring_7d} onClick={() => navigate('/quotes')} />}
            <Stat label="Conversion" value={t.quotes.conversion == null ? '–' : `${Math.round(t.quotes.conversion * 100)}%`} sub={`${t.quotes.accepted} of ${t.quotes.decided}`} />
          </Group>
          {t.fenix && (
            <Group title="Fenix">
              <Stat label="Draw due this period" value={money0(t.fenix.draw_due)} />
            </Group>
          )}
        </div>
      )}

      {prodList && t && <ProductionList items={t.to_deliver.shortfall} onClose={() => setProdList(false)} />}

      <div id="board">
        <Board kind={kind} onKind={setKind} filters={filters} setFilters={setFilters} sort={sort} setSort={setSort}
          readOnlyBase={readOnlyNode ? base : undefined} />
      </div>
    </div>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-2">
      <div className="px-3 pt-1 text-xs font-bold uppercase tracking-wider text-brand-primary">{title}</div>
      <div className="grid grid-cols-3 gap-1">{children}</div>
    </div>
  )
}

function Aging({ a }: { a: Record<string, number> }) {
  const total = Object.values(a).reduce((s, v) => s + v, 0)
  const parts = [['current', 'bg-emerald-500', 'Current'], ['d30', 'bg-amber-400', '30'], ['d60', 'bg-orange-500', '60'], ['d90', 'bg-red-600', '90+']]
  return (
    <div className="col-span-3 px-3 pb-1">
      <div className="flex h-2 overflow-hidden rounded-full bg-gray-100">
        {total > 0 && parts.map(([k, c]) => <div key={k} className={c} style={{ width: `${(a[k] / total) * 100}%` }} />)}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-gray-500">
        {parts.map(([k, , l]) => <span key={k}>{l} {money0(a[k])}</span>)}
      </div>
    </div>
  )
}

/** Short-of-stock as a production list Steven can print for the factory or send on WhatsApp. */
function ProductionList({ items, onClose }: { items: any[]; onClose: () => void }) {
  const { cfg, brand } = useApp()
  const [withOrders, setWithOrders] = useState(true)
  const name = (s: any) => [tankName(cfg, s.tank_type), colourOf(cfg, s.colour)?.name, s.grade && s.grade !== 'A' ? `(${s.grade})` : '']
    .filter(Boolean).join(' ')
  const day = new Date().toLocaleDateString('en-ZA', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  const total = items.reduce((n, s) => n + s.short, 0)
  const oldest = items.flatMap((s) => s.orders || []).map((o: any) => o.date).sort()[0]
  const oldestDays = oldest ? Math.max(0, Math.round((Date.now() - Date.parse(oldest)) / 864e5)) : 0
  const title = `${brand.display_name} production list, ${day}`

  const lines: string[] = [title, '']
  items.forEach((s) => {
    lines.push(`[ ] ${s.short} x ${name(s)} (need ${s.remaining}, have ${s.on_hand})`)
    if (withOrders) (s.orders || []).forEach((o: any) => lines.push(`      ${o.order_number || 'order'} · ${o.customer} · ${fmtDate(o.date)} · ${o.qty}`))
  })
  lines.push('', `Total to mould: ${total} ${total === 1 ? 'tank' : 'tanks'}${oldest ? ` · oldest order waiting ${oldestDays} ${oldestDays === 1 ? 'day' : 'days'}` : ''}`)
  const message = lines.join('\n')

  const print = () => {
    const esc = (x: any) => String(x ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' } as any)[c])
    const rows = items.map((s) => `
      <tr><td class="box"></td><td class="qty">${s.short}</td><td><b>${esc(name(s))}</b><div class="sub">need ${s.remaining}, have ${s.on_hand}</div>
      ${withOrders ? (s.orders || []).map((o: any) => `<div class="ord">${esc(o.order_number)} · ${esc(o.customer)} · ${esc(fmtDate(o.date))} · ${o.qty}</div>`).join('') : ''}</td></tr>`).join('')
    const w = window.open('', '_blank', 'width=820,height=1000')
    if (!w) return
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
      body{font-family:Arial,Helvetica,sans-serif;color:#000;margin:28px}
      h1{font-size:22px;margin:0 0 4px} .meta{font-size:13px;margin-bottom:18px}
      table{width:100%;border-collapse:collapse} td{border-bottom:1px solid #999;padding:10px 8px;vertical-align:top;font-size:16px}
      td.box{width:26px} td.box:before{content:"";display:inline-block;width:20px;height:20px;border:2px solid #000}
      td.qty{width:48px;font-size:22px;font-weight:bold} .sub{font-size:13px;color:#333;margin-top:2px}
      .ord{font-size:13px;margin-top:3px;color:#333} .tot{margin-top:16px;font-size:16px;font-weight:bold}
      @page{size:A4;margin:14mm}</style></head><body>
      <h1>${esc(title)}</h1><div class="meta">Tanks to mould to fill open orders.</div>
      <table>${rows}</table>
      <div class="tot">Total to mould: ${total} ${total === 1 ? 'tank' : 'tanks'}${oldest ? ` · oldest order waiting ${oldestDays} ${oldestDays === 1 ? 'day' : 'days'}` : ''}</div>
      <script>window.onload=function(){window.print()}<\/script></body></html>`)
    w.document.close()
  }

  const number = (cfg as any).production_whatsapp?.replace(/\D/g, '')
  const whatsapp = `https://wa.me/${number || ''}?text=${encodeURIComponent(message)}`
  return (
    <Modal title="Production list" onClose={onClose}>
      <label className="mb-2 flex items-center gap-2 text-sm text-gray-700">
        <input type="checkbox" checked={withOrders} onChange={(e) => setWithOrders(e.target.checked)} /> Show the orders waiting for each tank
      </label>
      <pre className="mb-4 max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm text-gray-800">{message}</pre>
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" onClick={print}><Printer size={16} /> Print</button>
        <ShareBar whatsapp={whatsapp} message={message} />
      </div>
    </Modal>
  )
}
