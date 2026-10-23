import { createContext, useContext } from 'react'
import type { PUser } from '../api'
import type { Branding } from './brand'

export interface TankType {
  code: string; name: string; kind: string; capacity_l?: number; diameter_mm?: number; height_mm?: number
  allowed_colours?: string[]; active?: boolean; ex_works_price?: number
}
export interface Colour { code: string; name: string; swatch_hex: string; availability: 'standard' | 'on_request'; powder_product: string }
export interface Charge { code: string; name: string; amount: number | null; includes_vat: boolean; xero_account?: string }
export interface Tier { code: string; name: string; prices_include_vat: boolean }
export interface Driver { name: string; phone: string; vehicle_reg?: string }

export interface Config {
  tank_types: TankType[]
  colours: Colour[]
  charges: Charge[]
  price_tiers: Tier[]
  drivers: Driver[]
  powder_products: { code: string; colour: string; is_black: boolean }[]
  vat_rate: number
  quote_validity_days: number
  proforma?: boolean
  cash_banking_days: number
  payment_terms_days: number
  track_colour_stock: boolean
  commercial_model: string
  company: Record<string, any>
  branding: Record<string, any>
  slug: string
  prefix: string
  tiers_versions: Record<string, { prices: Record<string, { A?: number; B?: number }>; version: number; effective_from: string } | null>
}

export interface Me extends PUser {
  business: string
  features: { yoco: boolean; xero: boolean; colour_stock: boolean; fenix_split: boolean }
}

export interface AppState {
  brand: Branding
  me: Me
  cfg: Config
  reload: () => Promise<void>
  isAdmin: boolean
}

export const Ctx = createContext<AppState | null>(null)

export function useApp(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('no app context')
  return v
}

export function colourOf(cfg: Config, code?: string) {
  return cfg.colours?.find((c) => c.code === code)
}

export function tankName(cfg: Config, code: string) {
  return cfg.tank_types.find((t) => t.code === code)?.name || code
}

export function lineLabel(cfg: Config, ln: any): string {
  if (ln.kind === 'charge') return ln.name || ln.code
  const c = colourOf(cfg, ln.colour)
  return `${tankName(cfg, ln.tank_type)}${c ? ' ' + c.name : ''}${ln.grade === 'B' ? ' (B)' : ''}`
}
