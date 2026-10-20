import { X } from 'lucide-react'
import { ReactNode, useEffect, useState } from 'react'
import { label } from '../lib/format'

const TONES: Record<string, string> = {
  green: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
  amber: 'bg-amber-50 text-amber-800 ring-amber-600/25',
  red: 'bg-red-50 text-red-800 ring-red-600/20',
  blue: 'bg-sky-50 text-sky-800 ring-sky-600/20',
  violet: 'bg-violet-50 text-violet-800 ring-violet-600/20',
  grey: 'bg-gray-100 text-gray-700 ring-gray-500/20',
}

const STATUS_TONE: Record<string, string> = {
  paid: 'green', delivered: 'green', complete: 'green', accepted: 'green', posted: 'green', approved: 'green',
  part_paid: 'amber', part_delivered: 'amber', scheduled: 'blue', out_for_delivery: 'violet', sent: 'blue',
  unpaid: 'amber', not_invoiced: 'grey', not_delivered: 'grey', draft: 'grey', open: 'blue', submitted: 'blue',
  overdue: 'red', failed: 'red', flagged: 'red', declined: 'grey', expired: 'grey', cancelled: 'grey', void: 'grey',
  cod_outstanding: 'violet', queried: 'amber', post_failed: 'red',
}

export function Chip({ children, tone = 'grey' }: { children: ReactNode; tone?: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset whitespace-nowrap ${TONES[tone] || TONES.grey}`}>
      {children}
    </span>
  )
}

export function Status({ s }: { s?: string | null }) {
  if (!s) return null
  return <Chip tone={STATUS_TONE[s] || 'grey'}>{label(s)}</Chip>
}

export function PageTitle({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold text-brand-dark sm:text-2xl">{title}</h1>
        {sub && <div className="mt-0.5 text-sm text-gray-500">{sub}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Empty({ text }: { text: string }) {
  return <p className="py-10 text-center text-sm text-gray-400">{text}</p>
}

export function Spinner() {
  return (
    <div className="flex justify-center py-12">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-brand-primary" />
    </div>
  )
}

export function ErrorBox({ msg }: { msg?: string | null }) {
  if (!msg) return null
  return <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{msg}</div>
}

export function Field({ label: l, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{l}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-gray-500">{hint}</span>}
    </label>
  )
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', k)
      document.body.style.overflow = ''
    }
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4" onClick={onClose}>
      <div
        className={`max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-4 shadow-xl sm:rounded-2xl sm:p-6 ${wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-brand-dark">{title}</h2>
          <button onClick={onClose} className="rounded-full p-1 text-gray-500 hover:bg-gray-100" aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: string }[]; value: T; onChange: (k: T) => void }) {
  return (
    <div className="mb-4 flex gap-1 overflow-x-auto rounded-lg bg-gray-100 p-1">
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={`whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-semibold transition ${value === t.key ? 'bg-white text-brand-dark shadow-sm' : 'text-gray-600 hover:text-gray-900'}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Stat({ label: l, value, sub, onClick, active, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; onClick?: () => void; active?: boolean; tone?: 'red' | 'default'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={`min-w-0 rounded-lg px-3 py-2 text-left transition ${onClick ? 'hover:bg-gray-50' : ''} ${active ? 'bg-brand-primary/10 ring-1 ring-brand-primary/40' : ''}`}
    >
      <div className="truncate text-[11px] font-semibold uppercase tracking-wide text-gray-500">{l}</div>
      <div className={`truncate text-lg font-bold tabular-nums ${tone === 'red' ? 'text-red-700' : 'text-brand-dark'}`}>{value}</div>
      {sub && <div className="truncate text-xs text-gray-500">{sub}</div>}
    </button>
  )
}

/** Run an async action with a busy flag and an error message. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function run<T>(fn: () => Promise<T>, errMsg: (e: unknown) => string): Promise<T | undefined> {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(errMsg(e))
      return undefined
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, setError, run }
}
