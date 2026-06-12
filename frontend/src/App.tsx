import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { logout, storedUser } from './api'
import type { User } from './types'
import Layout from './components/Layout'
import Admin from './pages/Admin'
import Login from './pages/Login'
import NodePage from './pages/NodePage'
import ReconDashboard from './pages/ReconDashboard'

export default function App() {
  const [user, setUser] = useState<User | null>(storedUser())
  const navigate = useNavigate()

  useEffect(() => {
    if (!user && window.location.pathname !== '/login') navigate('/login')
  }, [user, navigate])

  // Site-wide: focusing a number field selects its contents, so a leftover 0
  // (or any value) is replaced as soon as you start typing instead of persisting.
  useEffect(() => {
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLInputElement
      if (t?.tagName === 'INPUT' && t.type === 'number') {
        requestAnimationFrame(() => { try { t.select() } catch { /* noop */ } })
      }
    }
    document.addEventListener('focusin', onFocus)
    return () => document.removeEventListener('focusin', onFocus)
  }, [])

  const handleLogout = () => {
    logout()
    setUser(null)
    navigate('/login')
  }

  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<Login onLogin={(u) => { setUser(u); navigate(u.role === 'audit' ? '/recon' : '/node/gogreen') }} />} />
      </Routes>
    )
  }

  return (
    <Layout user={user} onLogout={handleLogout}>
      <Routes>
        <Route path="/login" element={<Navigate to={user.role === 'audit' ? '/recon' : '/node/gogreen'} />} />
        <Route path="/" element={<Navigate to={user.role === 'audit' ? '/recon' : '/node/gogreen'} />} />
        <Route path="/node/:nodeId/*" element={<NodePage user={user} />} />
        <Route path="/recon" element={<ReconDashboard user={user} />} />
        {user.role === 'admin' && <Route path="/admin" element={<Admin />} />}
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  )
}
