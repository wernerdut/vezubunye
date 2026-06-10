import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { api } from '../api'
import type { User } from '../types'

/* Network header: one number, total kilograms through all nodes, on every page. */
export default function Layout({ user, onLogout, children }: {
  user: User
  onLogout: () => void
  children: React.ReactNode
}) {
  const [networkKg, setNetworkKg] = useState<number | null>(null)
  const location = useLocation()

  useEffect(() => {
    api.get('/api/network/kg').then((r) => setNetworkKg(r.data.total_kg)).catch(() => {})
  }, [location.pathname])

  const navLink = (to: string, label: string) => (
    <Link
      to={to}
      className={`px-3 py-1.5 rounded text-sm font-semibold ${
        location.pathname.startsWith(to) ? 'bg-white/20 text-white' : 'text-blue-100 hover:text-white'
      }`}
    >
      {label}
    </Link>
  )

  return (
    <div className="min-h-screen">
      <header className="bg-brand-blue text-white">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
          <img src="/logos/vezubunye_logo_white_transparent.png" alt="Vezubunye" className="h-12" />
          <nav className="flex gap-1 ml-2">
            {navLink('/node/gogreen', 'GoGreen')}
            {(user.role === 'controller' || user.role === 'admin') && navLink('/recon', 'Reconciliation')}
            {user.role === 'admin' && navLink('/admin', 'Admin')}
          </nav>
          <div className="ml-auto flex items-center gap-4">
            <div className="text-right">
              <div className="font-headline text-2xl leading-none text-brand-yellow">
                {networkKg === null ? '—' : networkKg.toLocaleString('en-ZA')} kg
              </div>
              <div className="text-[10px] uppercase tracking-widest text-blue-200">network total</div>
            </div>
            <div className="text-right text-xs text-blue-100 border-l border-white/20 pl-4">
              <div className="font-semibold text-white">{user.name || user.email}</div>
              <div className="capitalize">{user.role}</div>
            </div>
            <button onClick={onLogout} className="p-2 rounded hover:bg-white/10" title="Log out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">{children}</main>
    </div>
  )
}
