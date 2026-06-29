export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    // clear / reconciled states render green; flags and errors red; warnings orange
    reconciled: 'bg-brand-green text-white',
    clear: 'bg-brand-green text-white',
    paid: 'bg-brand-green text-white',
    matched: 'bg-brand-green text-white',
    resolved: 'bg-brand-green text-white',
    active: 'bg-brand-green text-white',
    captured: 'bg-brand-green text-white',
    part_paid: 'bg-brand-orange text-white',
    pending: 'bg-gray-300 text-gray-700',
    no_capture: 'bg-gray-200 text-gray-500',
    unpaid: 'bg-brand-orange text-white',
    unmatched: 'bg-brand-orange text-white',
    flagged: 'bg-brand-red text-white',
    open: 'bg-brand-red text-white',
    inactive: 'bg-gray-300 text-gray-700',
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${styles[status] || 'bg-gray-200'}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="font-headline text-3xl text-brand-blue mb-3">{children}</h2>
}

export function Empty({ text }: { text: string }) {
  return <p className="text-sm text-gray-400 py-6 text-center">{text}</p>
}
