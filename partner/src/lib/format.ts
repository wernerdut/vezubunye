export function money(n: number | null | undefined): string {
  const v = Number(n || 0)
  return 'R' + v.toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/ /g, ' ')
}

export function money0(n: number | null | undefined): string {
  return 'R' + Math.round(Number(n || 0)).toLocaleString('en-ZA').replace(/ /g, ' ')
}

export function date(d?: string | null): string {
  if (!d) return ''
  const x = new Date(d.length === 10 ? d + 'T00:00:00' : d)
  return x.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: '2-digit' })
}

export function today(): string {
  const d = new Date(Date.now() + 2 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}

export function label(s?: string | null): string {
  if (!s) return ''
  const map: Record<string, string> = {
    not_invoiced: 'Not invoiced', unpaid: 'Unpaid', part_paid: 'Part paid', paid: 'Paid', overdue: 'Overdue',
    cod_outstanding: 'COD outstanding', not_delivered: 'Not delivered', scheduled: 'Scheduled',
    out_for_delivery: 'Out for delivery', part_delivered: 'Part delivered', delivered: 'Delivered',
    cancelled: 'Cancelled', failed: 'Failed', open: 'Open', complete: 'Complete', upfront: 'Upfront',
    terms: 'Terms', cod: 'COD', flagged: 'Overpaid', void: 'Void', draft: 'Draft', sent: 'Sent',
    accepted: 'Accepted', declined: 'Declined', expired: 'Expired', eft: 'EFT', cash: 'Cash',
    yoco_link: 'Yoco link', yoco_machine: 'Yoco machine', yoco: 'Yoco', submitted: 'Submitted',
    queried: 'Queried', approved: 'Approved', posted: 'Posted', post_failed: 'Post failed',
    posted_attachment_pending: 'Attachment pending', retail: 'Retail', trade: 'Trade',
  }
  return map[s] || s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

/** "Delivery 2 · 32 Montreal Dr, Cape Town"; the address alone when there is no label. */
export function siteLine(site?: { label?: string; address?: string; town?: string } | null): string {
  if (!site) return ''
  const addr = [site.address, site.town].filter(Boolean).join(', ')
  return [site.label, addr].filter(Boolean).join(' · ')
}
