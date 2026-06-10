import { useCallback, useEffect, useState } from 'react'
import { api, errMsg } from '../../api'
import { Empty, SectionTitle, StatusBadge } from '../../components/ui'
import type { Flag } from '../../types'
import type { TabProps } from '../NodePage'

export default function FlagsTab({ nodeId, user }: TabProps) {
  const [flags, setFlags] = useState<Flag[]>([])
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  const canResolve = user.role === 'audit' || user.role === 'admin'

  const load = useCallback(() => {
    api.get(`/api/nodes/${nodeId}/flags`).then((r) => setFlags(r.data))
  }, [nodeId])
  useEffect(load, [load])

  const resolve = async (id: string) => {
    setError('')
    try {
      await api.post(`/api/flags/${id}/resolve`, { resolution_note: notes[id] || '' })
      load()
    } catch (err) {
      setError(errMsg(err))
    }
  }

  const open = flags.filter((f) => f.status === 'open')
  const resolved = flags.filter((f) => f.status === 'resolved')

  const FlagCard = ({ f }: { f: Flag }) => (
    <div className={`card ${f.status === 'open' ? 'border-brand-red' : ''}`}>
      <div className="flex items-center gap-2 mb-1">
        <StatusBadge status={f.status} />
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">{f.type.replace(/_/g, ' ')}</span>
        <span className="text-xs text-gray-400 ml-auto">{f.date_raised}</span>
      </div>
      <p className="text-sm mb-2">{f.description}</p>
      {f.status === 'open' && canResolve && (
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="Resolution note (required)"
            value={notes[f._id] || ''}
            onChange={(e) => setNotes({ ...notes, [f._id]: e.target.value })}
          />
          <button className="btn-primary" disabled={!(notes[f._id] || '').trim()} onClick={() => resolve(f._id)}>
            Resolve
          </button>
        </div>
      )}
      {f.status === 'resolved' && (
        <p className="text-xs text-gray-500">
          Resolved by {f.resolved_by}: {f.resolution_note}
        </p>
      )}
    </div>
  )

  return (
    <div>
      <SectionTitle>Flags</SectionTitle>
      <p className="text-sm text-gray-500 mb-3">No flag auto-clears. Every resolution carries a note and an audit trail.</p>
      {error && <p className="text-sm text-brand-red mb-3">{error}</p>}
      <div className="space-y-3">
        {open.length === 0 ? (
          <div className="card border-brand-green text-brand-green font-semibold text-sm">No open flags. The chain is clean.</div>
        ) : (
          open.map((f) => <FlagCard key={f._id} f={f} />)
        )}
      </div>
      {resolved.length > 0 && (
        <>
          <h3 className="font-headline text-2xl text-gray-400 mt-6 mb-3">Resolved</h3>
          <div className="space-y-3 opacity-70">
            {resolved.map((f) => <FlagCard key={f._id} f={f} />)}
          </div>
        </>
      )}
      {flags.length === 0 && <Empty text="No flags ever raised" />}
    </div>
  )
}
