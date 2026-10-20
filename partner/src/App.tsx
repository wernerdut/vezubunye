import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, logout, storedUser } from './api'
import Shell from './components/Shell'
import { Spinner } from './components/ui'
import { Branding, loadBranding } from './lib/brand'
import { Config, Ctx, Me } from './lib/ctx'
import { currentSlug } from './lib/slug'
import Bills from './pages/Bills'
import CustomerDetail from './pages/CustomerDetail'
import Customers from './pages/Customers'
import Dashboard from './pages/Dashboard'
import Debtors from './pages/Debtors'
import DeliveryDetail from './pages/DeliveryDetail'
import Driver from './pages/Driver'
import InvoiceDetail from './pages/InvoiceDetail'
import Login from './pages/Login'
import OrderDetail from './pages/OrderDetail'
import OrderNew from './pages/OrderNew'
import Pay from './pages/Pay'
import Payments from './pages/Payments'
import Production from './pages/Production'
import QuoteDetail from './pages/QuoteDetail'
import Quotes from './pages/Quotes'
import Settings from './pages/Settings'
import Stock from './pages/Stock'

export default function App() {
  const loc = useLocation()
  const [brand, setBrand] = useState<Branding | null>(null)
  const [brandErr, setBrandErr] = useState(false)

  useEffect(() => {
    loadBranding(currentSlug()).then(setBrand).catch(() => setBrandErr(true))
  }, [])

  // Public pages: the driver link and the customer's pay link (no login).
  if (loc.pathname.startsWith('/d/')) return <Driver />
  if (loc.pathname.startsWith('/pay/')) return <Pay />

  if (brandErr) return <div className="p-10 text-center text-gray-500">This address is not set up.</div>
  if (!brand) return <Spinner />
  return <Authed brand={brand} />
}

function Authed({ brand }: { brand: Branding }) {
  const navigate = useNavigate()
  const [user, setUser] = useState(storedUser())
  const [me, setMe] = useState<Me | null>(null)
  const [cfg, setCfg] = useState<Config | null>(null)

  const reload = useCallback(async () => {
    const [m, c] = await Promise.all([api.get('/api/p/auth/me'), api.get('/api/p/config')])
    setMe(m.data)
    setCfg(c.data)
  }, [])

  useEffect(() => {
    if (user) reload().catch(() => undefined)
  }, [user, reload])

  if (!user) {
    return <Login brand={brand} onLogin={(u) => { setUser(u); navigate('/') }} />
  }
  if (!me || !cfg) return <Spinner />
  const isAdmin = me.role === 'partner_admin'
  return (
    <Ctx.Provider value={{ brand, me, cfg, reload, isAdmin }}>
      <Shell onLogout={() => { logout(); setUser(null); navigate('/login') }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/login" element={<Navigate to="/" />} />
          <Route path="/quotes" element={<Quotes />} />
          <Route path="/quotes/:id" element={<QuoteDetail />} />
          <Route path="/orders/new" element={<OrderNew />} />
          <Route path="/quick-sale" element={<OrderNew quick />} />
          <Route path="/orders/:id" element={<OrderDetail />} />
          <Route path="/deliveries/:id" element={<DeliveryDetail />} />
          <Route path="/invoices/:id" element={<InvoiceDetail />} />
          <Route path="/payments" element={<Payments />} />
          <Route path="/customers" element={<Customers />} />
          <Route path="/customers/:id" element={<CustomerDetail />} />
          <Route path="/debtors" element={<Debtors />} />
          <Route path="/bills" element={<Bills />} />
          <Route path="/production" element={<Production />} />
          <Route path="/stock" element={<Stock />} />
          <Route path="/settings/*" element={isAdmin ? <Settings /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Shell>
    </Ctx.Provider>
  )
}
