import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Empty, SectionTitle } from '../../components/ui'
import type { ProductionRun } from '../../types'
import type { TabProps } from '../NodePage'

export default function Production({ nodeId, config }: TabProps) {
  const [runs, setRuns] = useState<ProductionRun[]>([])

  useEffect(() => {
    api.get(`/api/nodes/${nodeId}/production`).then((r) => setRuns(r.data))
  }, [nodeId])

  const names = Object.fromEntries(config.tank_types.map((t) => [t.code, t.name]))
  const colourName = (code: string) => config.powder_products?.find((p) => p.code === code)?.colour || code || '—'

  return (
    <div>
      <SectionTitle>Production Runs</SectionTitle>
      <p className="text-sm text-gray-500 mb-3">
        Every moulded tank draws its full weight in powder regardless of grade: half its colour, half black, plus a black lid.
        Rejects go to the scrap log, never into finished goods.
      </p>
      <div className="card p-0 overflow-hidden">
        {runs.length === 0 ? (
          <Empty text="No production captured yet" />
        ) : (
          <table className="w-full">
            <thead>
              <tr>
                <th className="th">Date</th>
                <th className="th">Tank</th>
                <th className="th">Colour</th>
                <th className="th text-right">A</th>
                <th className="th text-right">B</th>
                <th className="th text-right">Reject</th>
                <th className="th text-right">Implied powder (kg)</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r._id}>
                  <td className="td">{r.date}</td>
                  <td className="td font-semibold">{names[r.tank_type] || r.tank_type}</td>
                  <td className="td text-brand-blue font-semibold">{colourName(r.colour || '')}</td>
                  <td className="td text-right text-brand-green font-semibold">{r.quantity_a}</td>
                  <td className="td text-right text-brand-orange font-semibold">{r.quantity_b}</td>
                  <td className="td text-right text-brand-red font-semibold">{r.quantity_reject}</td>
                  <td className="td text-right">{r.implied_powder_kg.toLocaleString('en-ZA')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
