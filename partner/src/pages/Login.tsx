import { FormEvent, useState } from 'react'
import { errMsg, login, PUser } from '../api'
import { ErrorBox } from '../components/ui'
import { Branding } from '../lib/brand'

export default function Login({ brand, onLogin }: { brand: Branding; onLogin: (u: PUser) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      onLogin(await login(email.trim(), password))
    } catch (x) {
      setErr(errMsg(x))
    } finally {
      setBusy(false)
    }
  }
  const pattern = brand.assets.pattern
  return (
    <div className="gradient relative flex min-h-screen items-center justify-center p-4">
      {pattern && <div className="absolute inset-0 opacity-10" style={{ backgroundImage: `url(${pattern})`, backgroundSize: '480px' }} />}
      <form onSubmit={submit} className="relative w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl sm:p-8">
        <div className="mb-6 flex justify-center">
          {brand.assets.logo_light ? <img src={brand.assets.logo_light} alt={brand.display_name} className="h-28 object-contain" />
            : <h1 className="text-2xl font-bold text-brand-dark">{brand.display_name}</h1>}
        </div>
        <div className="space-y-3">
          <input className="input" type="email" autoComplete="username" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input className="input" type="password" autoComplete="current-password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          <ErrorBox msg={err} />
          <button className="btn-primary w-full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </div>
        {brand.company?.phone && <p className="mt-6 text-center text-xs text-gray-400">{brand.company.phone}</p>}
      </form>
    </div>
  )
}
