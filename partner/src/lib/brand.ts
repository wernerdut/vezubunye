import { api } from '../api'

export interface Branding {
  slug: string
  display_name: string
  colors: { primary?: string; accent?: string; dark?: string; gradient?: string[] }
  font: { family?: string; fallback?: string; adobe_kit?: string | null }
  assets: Record<string, string>
  company: Record<string, string | null>
}

function rgb(hex?: string): string | null {
  if (!hex) return null
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const n = parseInt(full, 16)
  if (Number.isNaN(n)) return null
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`
}

/** Load the node's public branding and apply it: colours, font, title and favicon. */
export async function loadBranding(slug: string): Promise<Branding> {
  const r = await api.get('/api/p/branding', { params: { slug } })
  const b: Branding = r.data
  const root = document.documentElement.style
  const c = b.colors || {}
  const set = (k: string, v: string | null) => v && root.setProperty(k, v)
  set('--brand-primary', rgb(c.primary))
  set('--brand-accent', rgb(c.accent || c.primary))
  set('--brand-dark', rgb(c.dark))
  if (c.gradient?.length) root.setProperty('--brand-gradient', `linear-gradient(45deg, ${c.gradient.join(', ')})`)
  if (b.font?.adobe_kit) {
    const l = document.createElement('link')
    l.rel = 'stylesheet'
    l.href = `https://use.typekit.net/${b.font.adobe_kit}.css`
    document.head.appendChild(l)
    root.setProperty('--brand-font', `"${b.font.family}"`)
  }
  document.title = b.display_name
  const fav = b.assets.favicon || b.assets.icon
  if (fav) {
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = fav
  }
  if (b.assets.app_icon) {
    const t = document.createElement('link')
    t.rel = 'apple-touch-icon'
    t.href = b.assets.app_icon
    document.head.appendChild(t)
  }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta && c.dark) meta.setAttribute('content', c.dark)
  return b
}
