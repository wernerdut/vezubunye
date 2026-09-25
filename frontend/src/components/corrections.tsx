import { useCallback, useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import { api, errMsg } from '../api'
import type { User, VoidStamp } from '../types'

/* Corrections happen where the mistake is seen: a ⋯ menu on the row, a modal that states
   the cascade and demands a reason. Nothing is deleted; voided rows stay, muted. */

export interface RowAction {
  label: string
  onClick: () => void
  danger?: boolean
}

export function RowMenu({ actions }: { actions: RowAction[] }) {
  // fixed-position menu so a scrolling table never clips it; it follows its button on scroll
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const place = () => {
    const r = btn.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 4, left: Math.max(8, r.right - 144) })
  }
  const isOpen = pos !== null
  useEffect(() => {
    if (!isOpen) return
    const close = (e: Event) => { if (!ref.current?.contains(e.target as Node)) setPos(null) }
    document.addEventListener('mousedown', close)
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      document.removeEventListener('mousedown', close)
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [isOpen])
  if (actions.length === 0) return null
  const toggle = () => (isOpen ? setPos(null) : place())
  return (
    <div className="inline-block no-underline" ref={ref}>
      <button type="button" ref={btn} className="text-gray-400 hover:text-brand-blue px-1" title="Correct" onClick={toggle}>
        <MoreHorizontal size={16} />
      </button>
      {pos && (
        <div className="fixed z-30 w-36 bg-white border border-gray-200 rounded shadow-lg py-1" style={pos}>
          {actions.map((a) => (
            <button key={a.label} type="button"
                    className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 ${a.danger ? 'text-brand-red' : 'text-gray-700'}`}
                    onClick={() => { setPos(null); a.onClick() }}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Who may correct what. Admin: everything. Audit: payments and adjustments. Operations: nothing. */
export const canCorrect = (user: User, scope: 'admin' | 'audit') =>
  user.role === 'admin' || (scope === 'audit' && user.role === 'audit')

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const shortDate = (d: string) => `${parseInt(d.slice(8, 10))} ${MONTHS[parseInt(d.slice(5, 7)) - 1]}`

export interface CorrectionRequest {
  title: string
  cascade: string                     // plain statement of what the correction does
  date?: string                       // record date, checked against the latest count
  movesStock?: boolean                // only stock-moving corrections raise the post-count flag
  confirmLabel?: string
  fields?: React.ReactNode            // extra inputs (edits)
  run: (reason: string) => Promise<unknown>
}

/** One modal per tab. `ask()` opens it; the latest count date drives the post-count warning. */
export function useCorrection(nodeId: string, onDone: () => void) {
  const [req, setReq] = useState<CorrectionRequest | null>(null)
  const [countDate, setCountDate] = useState<string | null>(null)
  const refreshCount = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/counts`)
      .then((r) => setCountDate(r.data[0]?.date ?? null))
      .catch(() => setCountDate(null))
  }, [nodeId])
  useEffect(refreshCount, [refreshCount])

  const modal = req && (
    <CorrectionModal
      req={req} countDate={countDate}
      onClose={() => setReq(null)}
      onDone={() => { setReq(null); refreshCount(); onDone() }}
    />
  )
  return { ask: setReq, modal }
}

function CorrectionModal({ req, countDate, onClose, onDone }: {
  req: CorrectionRequest; countDate: string | null; onClose: () => void; onDone: () => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const postCount = (req.movesStock ?? true) && req.date && countDate && req.date <= countDate

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!reason.trim()) { setError('A reason is required.'); return }
    setBusy(true); setError('')
    try {
      await req.run(reason.trim())
      onDone()
    } catch (err) {
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4" onMouseDown={onClose}>
      <form className="card w-full max-w-md space-y-3" onSubmit={confirm} onMouseDown={(e) => e.stopPropagation()}>
        <h3 className="font-headline text-xl text-brand-blue">{req.title}</h3>
        <p className="text-sm text-gray-700">{req.cascade}</p>
        {postCount && (
          <p className="text-sm font-semibold text-brand-orange">
            This is before the {shortDate(countDate!)} count. A post-count flag will be raised.
          </p>
        )}
        {req.fields}
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">Reason (required, goes to the audit log)</label>
          <textarea className="input" rows={2} value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
        </div>
        {error && <p className="text-sm text-brand-red">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !reason.trim()}>{busy ? 'Working…' : (req.confirmLabel || 'Confirm')}</button>
        </div>
      </form>
    </div>
  )
}

export function ShowVoided({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline-flex items-center gap-1 text-xs text-gray-500 cursor-pointer select-none">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} /> Show voided
    </label>
  )
}

export const voidedQuery = (show: boolean) => (show ? '?include_voided=true' : '')

/** Row props for a record that may be void: muted, struck through, with who/when/why. */
export function voidRow(row: { void?: VoidStamp; superseded_by?: string }) {
  if (!row.void) return {}
  const v = row.void
  const title = `Voided by ${v.by} on ${String(v.at).slice(0, 10)}: ${v.reason}` +
    (row.superseded_by ? `. Superseded by ${row.superseded_by}.` : '')
  return { className: 'opacity-50 line-through', title }
}
