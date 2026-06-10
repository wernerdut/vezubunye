import axios from 'axios'
import type { User } from './types'

export const API_URL = import.meta.env.VITE_API_URL || ''

export const api = axios.create({ baseURL: API_URL })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('vz_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && !err.config?.url?.includes('/auth/login')) {
      localStorage.removeItem('vz_token')
      localStorage.removeItem('vz_user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export function storedUser(): User | null {
  const raw = localStorage.getItem('vz_user')
  return raw ? (JSON.parse(raw) as User) : null
}

export async function login(email: string, password: string): Promise<User> {
  const r = await api.post('/api/auth/login', { email, password })
  localStorage.setItem('vz_token', r.data.token)
  localStorage.setItem('vz_user', JSON.stringify(r.data.user))
  return r.data.user
}

export function logout() {
  localStorage.removeItem('vz_token')
  localStorage.removeItem('vz_user')
}

/** Open an authed PDF/photo endpoint in a new tab via blob (header auth, not cookies). */
export async function openAuthed(path: string) {
  const r = await api.get(path, { responseType: 'blob' })
  const url = URL.createObjectURL(r.data)
  window.open(url, '_blank')
}

export function errMsg(e: unknown): string {
  const ax = e as { response?: { data?: { detail?: string } }; message?: string }
  return ax.response?.data?.detail || ax.message || 'Something went wrong'
}
