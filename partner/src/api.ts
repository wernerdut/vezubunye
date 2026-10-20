import axios from 'axios'
import { currentSlug } from './lib/slug'

const slug = currentSlug()
const TOKEN = `p_token_${slug}`
const USER = `p_user_${slug}`

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || '' })

api.interceptors.request.use((config) => {
  const t = localStorage.getItem(TOKEN)
  if (t && !config.url?.startsWith('/api/d/')) config.headers.Authorization = `Bearer ${t}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const url: string = err.config?.url || ''
    if (err.response?.status === 401 && url.startsWith('/api/p/') && !url.includes('/auth/login')) {
      localStorage.removeItem(TOKEN)
      localStorage.removeItem(USER)
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export interface PUser {
  email: string
  name: string
  role: 'partner_admin' | 'partner_staff'
  employer: 'partner' | 'fenix'
}

export function storedUser(): PUser | null {
  const raw = localStorage.getItem(USER)
  return raw ? (JSON.parse(raw) as PUser) : null
}

export async function login(email: string, password: string): Promise<PUser> {
  const r = await api.post('/api/p/auth/login', { slug, email, password })
  localStorage.setItem(TOKEN, r.data.token)
  localStorage.setItem(USER, JSON.stringify(r.data.user))
  return r.data.user
}

export function logout() {
  localStorage.removeItem(TOKEN)
  localStorage.removeItem(USER)
}

/** Open an authed PDF in a new tab (header auth, not cookies). */
export async function openPdf(path: string) {
  const w = window.open('', '_blank')
  const r = await api.get(path, { responseType: 'blob' })
  const url = URL.createObjectURL(r.data)
  if (w) w.location.href = url
  else window.location.href = url
}

export async function downloadCsv(path: string, name: string) {
  const r = await api.get(path, { responseType: 'blob' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(r.data)
  a.download = name
  a.click()
}

export function errMsg(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: unknown } }; message?: string }
  const d = ax.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).join('; ')
  return ax.message || 'Something went wrong'
}
