import { Plus, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import Board, { Kind, Sort } from '../components/Board'
import { PageTitle, Stat } from '../components/ui'
import { colourOf, tankName, useApp } from '../lib/ctx'
import { label, money0 } from '../lib/format'

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
                <div className="font-semibold">Short of stock</div>
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
