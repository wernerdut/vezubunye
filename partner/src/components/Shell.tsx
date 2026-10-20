import {
  Banknote, ClipboardList, Factory, FileText, LayoutDashboard, LogOut, Menu, Package, Receipt, Settings, ShoppingCart, Users, Wallet, X, Zap,
} from 'lucide-react'
import { ReactNode, useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../lib/ctx'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/quotes', label: 'Quotes', icon: FileText },
  { to: '/orders/new', label: 'New order', icon: ShoppingCart },
  { to: '/quick-sale', label: 'Quick sale', icon: Zap },
  { to: '/payments', label: 'Payments', icon: Wallet },
  { to: '/customers', label: 'Customers', icon: Users },
  { to: '/debtors', label: 'Debtors', icon: Banknote },
  { to: '/bills', label: 'Bills', icon: Receipt },
  { to: '/production', label: 'Production', icon: Factory },
  { to: '/stock', label: 'Stock', icon: Package },
]

export default function Shell({ children, onLogout }: { children: ReactNode; onLogout: () => void }) {
  const { brand, me, isAdmin } = useApp()
  const [open, setOpen] = useState(false)
  const [xero, setXero] = useState<{ connected: boolean; status?: string; waiting?: number } | null>(null)
  const loc = useLocation()
  const navigate = useNavigate()
  useEffect(() => setOpen(false), [loc.pathname])
  useEffect(() => {
    if (me.features.xero) api.get('/api/p/xero').then((r) => setXero(r.data)).catch(() => undefined)
  }, [me.features.xero])

  const logo = brand.assets.logo_dark || brand.assets.logo_light
  const nav = (
    <nav className="flex flex-col gap-0.5 p-3">
      {NAV.map((n) => (
        <NavLink key={n.to} to={n.to} end={n.end}
          className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? 'bg-white/15 text-white' : 'text-white/80 hover:bg-white/10 hover:text-white'}`}>
          <n.icon size={18} /> {n.label}
        </NavLink>
      ))}
      {isAdmin && (
        <NavLink to="/settings" className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? 'bg-white/15 text-white' : 'text-white/80 hover:bg-white/10 hover:text-white'}`}>
          <Settings size={18} /> Settings
        </NavLink>
      )}
      <button onClick={onLogout} className="mt-4 flex items-center gap-3 rounded-lg px-3 py-2 text-left text-sm font-medium text-white/70 hover:bg-white/10 hover:text-white">
        <LogOut size={18} /> Sign out
      </button>
      <div className="mt-2 px-3 text-xs text-white/50">{me.name || me.email}{me.employer === 'fenix' ? ' · Fenix' : ''}</div>
    </nav>
  )

  return (
    <div className="min-h-screen lg:flex">
      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 bg-brand-dark lg:block">
        <div className="sticky top-0">
          <button onClick={() => navigate('/')} className="block w-full px-5 pb-2 pt-5">
            {logo ? <img src={logo} alt={brand.display_name} className="h-16 object-contain" /> : <span className="text-lg font-bold text-white">{brand.display_name}</span>}
          </button>
          {nav}
        </div>
      </aside>
      {/* mobile header */}
      <header className="sticky top-0 z-40 flex h-14 items-center justify-between bg-brand-dark px-3 lg:hidden">
        <button onClick={() => setOpen(true)} className="p-2 text-white" aria-label="Menu"><Menu /></button>
        <button onClick={() => navigate('/')} className="h-10">
          {logo ? <img src={logo} alt={brand.display_name} className="h-10 object-contain" /> : <span className="font-bold text-white">{brand.display_name}</span>}
        </button>
        <button onClick={() => navigate('/orders/new')} className="p-2 text-white" aria-label="New order"><ClipboardList /></button>
      </header>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/40 lg:hidden" onClick={() => setOpen(false)}>
          <div className="h-full w-72 bg-brand-dark" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-end p-2"><button onClick={() => setOpen(false)} className="p-2 text-white"><X /></button></div>
            {nav}
          </div>
        </div>
      )}
      <main className="min-w-0 flex-1">
        {xero && xero.status === 'broken' && (
          <div className="bg-red-600 px-4 py-2 text-sm font-semibold text-white">
            Xero is disconnected. Invoices and payments are waiting and will post when it is reconnected.
            {isAdmin && <button className="ml-2 underline" onClick={() => navigate('/settings/xero')}>Reconnect</button>}
          </div>
        )}
        <div className="mx-auto max-w-7xl p-3 sm:p-5 lg:p-6">{children}</div>
      </main>
    </div>
  )
}
