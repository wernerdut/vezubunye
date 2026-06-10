import { useState } from 'react'
import { errMsg, login } from '../api'
import type { User } from '../types'

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      onLogin(await login(email, password))
    } catch (err) {
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-brand-blue flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <img
          src="/logos/vezubunye_logo_white_transparent.png"
          alt="Vezubunye Industries"
          className="mx-auto h-40 mb-2"
        />
        <p className="text-center font-headline text-brand-yellow text-2xl mb-8">We Build Together!</p>
        <form onSubmit={submit} className="bg-white rounded-lg p-6 shadow-lg space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Email</label>
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">Password</label>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {error && <p className="text-sm text-brand-red">{error}</p>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
