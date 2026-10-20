import { Fragment, useEffect, useState } from 'react'
import { api, errMsg, openAuthed } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import type { TabProps } from '../NodePage'

const rand = (n: number | null | undefined) => `R ${(n || 0).toLocaleString('en-ZA', { minimumFractionDigits: 2 })}`

type View = 'board' | 'quotes' | 'customers' | 'cash' | 'bills' | 'debtors'
const VIEWS: { key: View; label: string }[] = [
  { key: 'board', label: 'Dashboard' }, { key: 'quotes', label: 'Quotes' }, { key: 'customers', label: 'Customers' },
  { key: 'cash', label: 'Cash' }, { key: 'bills', label: 'Bills' }, { key: 'debtors', label: 'Statements' },
]
const KINDS = ['orders', 'deliveries', 'invoices'] as const

/** The node's partner-app records, read-only. Fenix sees every figure, bills included. */
export default function Commercial({ nodeId }: TabProps) {
  const [view, setView] = useState<View>('board')
  const [tiles, setTiles] = useState<any>(null)
  const [kind, setKind] = useState<(typeof KINDS)[number]>('orders')
  const [filters, setFilters] = useState<Record<string, any>>({ order_status: [] })
  const [board, setBoard] = useState<any>(null)
  const [rows, setRows] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get(`/api/nodes/${nodeId}/commercial/tiles`).then((r) => setTiles(r.data)).catch((e) => setError(errMsg(e)))
  }, [nodeId])
  useEffect(() => {
    if (view !== 'board') return
    setBoard(null)
    api.get(`/api/nodes/${nodeId}/commercial/board/${kind}`, { params: { filters: JSON.stringify(filters) } }).then((r) => setBoard(r.data))
  }, [nodeId, view, kind, filters])
  useEffect(() => {
    if (view === 'board') return
    setRows(null)
    api.get(`/api/nodes/${nodeId}/commercial/${view}`).then((r) => setRows(r.data)).catch((e) => setError(errMsg(e)))
  }, [nodeId, view])

  return (
    <div>
      {error && <p className="text-sm text-brand-red">{error}</p>}
      <div className="mb-4 flex flex-wrap gap-1">
        {VIEWS.map((v) => (
          <button key={v.key} onClick={() => setView(v.key)} className={view === v.key ? 'btn-primary text-sm' : 'btn-secondary text-sm'}>{v.label}</button>
        ))}
      </div>

      {view === 'board' && (
        <>
          {tiles && (
            <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                ['To deliver', `${tiles.to_deliver.qty} tanks`, rand(tiles.to_deliver.value)],
                ['Invoiced (month)', rand(tiles.invoices.invoiced), `outstanding ${rand(tiles.invoices.outstanding)}`],
                ['Overdue', rand(tiles.invoices.overdue), `90+ ${rand(tiles.invoices.aging.d90)}`],
                ['Received (month)', rand(tiles.payments.received), Object.entries(tiles.payments.by_method).map(([k, v]) => `${k} ${rand(v as number)}`).join(' · ')],
                ['Collect on delivery', rand(tiles.payments.cod_outstanding), ''],
                ['Cash not banked', rand((tiles.payments.cash_on_hand || []).reduce((s: number, h: any) => s + h.amount, 0)), (tiles.payments.cash_on_hand || []).map((h: any) => h.holder.name).join(', ')],
                ['Deliveries', `${tiles.deliveries.out_now} out now`, `${tiles.deliveries.delivered} delivered · ${tiles.deliveries.failed} failed`],
                ...(tiles.fenix ? [['Fenix draw due (month)', rand(tiles.fenix.draw_due), '']] : []),
              ].map(([l, v, sub]) => (
                <div key={l} className="card"><div className="text-xs uppercase text-gray-500">{l}</div><div className="text-xl font-bold text-brand-blue">{v}</div><div className="truncate text-xs text-gray-500">{sub}</div></div>
              ))}
            </div>
          )}
          <div className="mb-3 flex flex-wrap gap-2">
            {KINDS.map((k) => <button key={k} onClick={() => { setKind(k); setFilters(k === 'orders' ? { order_status: [] } : {}) }} className={kind === k ? 'btn-primary text-sm' : 'btn-secondary text-sm'}>{k}</button>)}
            {kind === 'orders' && ['open', 'overdue', 'cod_outstanding'].map((f) => (
              <button key={f} className="btn-secondary text-xs" onClick={() => setFilters(f === 'open' ? { order_status: ['open'] } : { order_status: [], payment_status: [f] })}>{f.replace('_', ' ')}</button>
            ))}
          </div>
          <div className="card overflow-x-auto p-0">
            {!board ? <p className="p-4 text-gray-400">Loading…</p> : board.rows.length === 0 ? <Empty text="Nothing yet" /> : (
              <table className="w-full text-sm [&_td]:whitespace-nowrap">
                <thead><tr>{kind === 'orders' ? ['Order', 'Date', 'Customer', 'Value', 'Paid', 'Balance', 'Payment', 'Delivery', 'Next'].map((h) => <th key={h} className="th">{h}</th>)
                  : kind === 'deliveries' ? ['DN', 'Order', 'Customer', 'Planned', 'Driver', 'Status', 'POD', 'To collect', 'Collected'].map((h) => <th key={h} className="th">{h}</th>)
                    : ['Invoice', 'Order', 'Customer', 'Date', 'Due', 'Total', 'Paid', 'Balance', 'Status'].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{board.rows.map((r: any) => (
                  <Fragment key={r._id}>
                    {kind === 'orders' ? (
                      <tr><td className="td font-semibold">{r.order_number}</td><td className="td">{r.date}</td><td className="td">{r.customer}</td>
                        <td className="td text-right">{rand(r.value)}</td><td className="td text-right">{rand(r.paid)}</td><td className="td text-right font-semibold">{rand(r.balance)}</td>
                        <td className="td"><StatusBadge status={r.overdue ? 'flagged' : r.payment_status} /></td><td className="td">{r.delivery_status.replace(/_/g, ' ')} · {r.delivered_label}</td><td className="td text-xs">{r.next_action.label}</td></tr>
                    ) : kind === 'deliveries' ? (
                      <tr><td className="td font-semibold">{r.dn_number}</td><td className="td">{r.order_number}</td><td className="td">{r.customer}</td><td className="td">{r.planned_date}</td><td className="td">{r.driver}</td>
                        <td className="td">{r.delivery_status.replace(/_/g, ' ')}</td><td className="td">{r.pod ? 'Yes' : ''}</td><td className="td text-right">{r.to_collect ? rand(r.to_collect) : ''}</td><td className="td text-right">{r.collected != null ? rand(r.collected) : ''}</td></tr>
                    ) : (
                      <tr><td className="td font-semibold">{r.number}</td><td className="td">{r.order_number}</td><td className="td">{r.customer}</td><td className="td">{r.date}</td><td className="td">{r.due_date}</td>
                        <td className="td text-right">{rand(r.total)}</td><td className="td text-right">{rand(r.paid)}</td><td className="td text-right font-semibold">{rand(r.balance)}</td><td className="td"><StatusBadge status={r.status} /></td></tr>
                    )}
                  </Fragment>
                ))}</tbody>
              </table>
            )}
          </div>
        </>
      )}

      {view !== 'board' && (!rows ? <p className="text-gray-400">Loading…</p> : (
        <div className="card overflow-x-auto p-0">
          {view === 'quotes' && <Table heads={['Quote', 'Date', 'Customer', 'Total', 'Valid until', 'Status']} rows={rows.map((q: any) => [q.quote_number + (q.rev > 1 ? ` rev ${q.rev}` : ''), q.date, q.customer_name, rand(q.total), q.valid_until, q.status])} />}
          {view === 'customers' && <Table heads={['Customer', 'Phone', 'Basis', 'Tier', 'Sites']} rows={rows.map((c: any) => [c.name, c.phone, c.payment_basis, c.price_tier, (c.sites || []).map((s: any) => s.label).join(', ')])} />}
          {view === 'bills' && <Table heads={['Bill', 'Supplier', 'Invoice', 'Date', 'Total', 'Status', 'Captured by', 'Documents']} rows={rows.map((b: any) => [b.bill_number, b.supplier_name, b.invoice_number, b.invoice_date, rand(b.total_incl), b.status, `${b.captured_by}${b.captured_by_employer === 'fenix' ? ' (Fenix)' : ''}`,
            <span key="d" className="flex gap-2">{(b.documents || []).map((d: string, i: number) => <button key={d} className="text-brand-light underline" onClick={() => openAuthed(`/api/bill-documents/${d}`)}>{i + 1}</button>)}</span>])} />}
          {view === 'cash' && <>
            <SectionTitle>Not banked</SectionTitle>
            <Table heads={['Holder', 'Receipts', 'Since', 'Amount']} rows={rows.on_hand.map((h: any) => [`${h.holder.name} (${h.holder.type})`, h.receipts, h.oldest, rand(h.amount)])} />
            <SectionTitle>Deposits</SectionTitle>
            <Table heads={['Date', 'Reference', 'Receipts', 'Deposited']} rows={rows.deposits.map((d: any) => [d.date, d.reference, rand(d.receipts_total), rand(d.amount)])} />
          </>}
          {view === 'debtors' && (
            <table className="w-full text-sm [&_td]:whitespace-nowrap"><thead><tr>{['Customer', 'Current', '30', '60', '90+', 'Balance', 'Credit', ''].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>{rows.map((r: any) => <tr key={r.customer_id}><td className="td font-semibold">{r.name}</td>
                {['current', 'd30', 'd60', 'd90', 'balance', 'credit'].map((k) => <td key={k} className="td text-right">{r[k] ? rand(r[k]) : ''}</td>)}
                <td className="td"><button className="text-brand-light underline" onClick={() => openAuthed(`/api/nodes/${nodeId}/customers/${r.customer_id}/statement.pdf`)}>Statement</button></td></tr>)}</tbody></table>
          )}
        </div>
      ))}
    </div>
  )
}

function Table({ heads, rows }: { heads: string[]; rows: any[][] }) {
  if (!rows.length) return <Empty text="Nothing yet" />
  return (
    <table className="w-full text-sm [&_td]:whitespace-nowrap">
      <thead><tr>{heads.map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="td">{c}</td>)}</tr>)}</tbody>
    </table>
  )
}
