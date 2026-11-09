/** The node's slug from the hostname: makx.vezubunye.com -> makx. Local dev: ?slug= or VITE_DEV_SLUG. */
export function currentSlug(): string {
  const host = window.location.hostname
  // pay links on a partner's own website (backend yoco.PAY_HOSTS)
  const own: Record<string, string> = { 'www.makulutanks.co.za': 'makx', 'makulutanks.co.za': 'makx' }
  if (own[host]) return own[host]
  const m = host.match(/^([a-z0-9-]+)\.vezubunye\.com$/)
  if (m) return m[1]
  const q = new URLSearchParams(window.location.search).get('slug')
  if (q) {
    localStorage.setItem('dev_slug', q)
    return q
  }
  return localStorage.getItem('dev_slug') || import.meta.env.VITE_DEV_SLUG || 'makx'
}
