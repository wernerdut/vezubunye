export type Role = 'admin' | 'audit' | 'operations'

export interface User {
  email: string
  name: string
  role: Role
  node_access: string[] | 'all'
}

export interface NodeInfo {
  _id: string
  node_id: string
  name: string
  location: string
  prefix?: string
  status: 'active' | 'inactive'
}

export interface TankType {
  code: string
  name: string
  ex_works_price: number
  weight_kg: number
  lid_weight_kg: number
}

export interface PowderProduct {
  code: string
  colour: string
  description?: string
  is_black: boolean
}

export interface FittingType {
  code: string
  name: string
}

export interface Tolerances {
  powder_kg: number
  tank_qty: number
  fittings_qty: number
}

export interface NodeConfig {
  node_id: string
  tank_types: TankType[]
  material_cost_per_kg?: number
  b_grade_exworks_pct: number
  vat_rate: number
  payment_terms_days: number
  powder_products: PowderProduct[]
  fitting_types: FittingType[]
  fittings_per_tank: Record<string, Record<string, number>>
  tolerances: Tolerances
}

export interface Capture {
  _id: string
  node_id: string
  date: string
  photo_url: string | null
  captured_by: string
  status: 'pending' | 'captured' | 'reconciled'
  entries?: CaptureEntries
}

export interface ProductionLine {
  tank_type: string
  colour: string
  quantity_a: number
  quantity_b: number
  quantity_reject: number
}

export interface PowderMoveLine {
  powder_type: string
  received_kg: number
  issued_kg: number
}

export interface FittingMoveLine {
  fitting_type: string
  received_qty: number
  issued_qty: number
}

export interface BookedLine {
  tank_type: string
  quantity_a: number
  quantity_b: number
}

export interface DispatchLine {
  tank_type: string
  grade: 'A' | 'B'
  quantity: number
  dn_number: string
}

export interface CaptureEntries {
  powder: PowderMoveLine[]
  fittings: FittingMoveLine[]
  production: ProductionLine[]
  booked: BookedLine[]
  dispatched: DispatchLine[]
  notes?: string
}

export interface PowderEntry {
  _id: string
  date: string
  powder_type: string
  type: 'received' | 'issued' | 'count_adjustment'
  kg: number
  notes: string
}

export interface PowderData {
  entries: PowderEntry[]
  stock: { powder_type: string; colour: string; is_black: boolean; warehouse: number; floor: number }[]
}

export interface FittingsData {
  entries: { _id: string; date: string; fitting_type: string; type: string; quantity: number }[]
  warehouse: { fitting_type: string; name: string; balance: number; issued: number; expected: number; variance: number }[]
}

export interface FGPosition {
  tank_type: string
  grade: 'A' | 'B'
  floor: number
  store: number
  total: number
}

export interface ProductionRun {
  _id: string
  date: string
  tank_type: string
  colour?: string
  quantity_a: number
  quantity_b: number
  quantity_reject: number
  implied_powder_kg: number
}

export interface FGEntry {
  _id: string
  date: string
  tank_type: string
  grade: 'A' | 'B'
  type: 'booked' | 'dispatched' | 'count_adjustment'
  quantity: number
  dn_number?: string
}

export interface OnHand {
  tank_type: string
  grade: 'A' | 'B'
  quantity: number
}

export interface ScrapEntry {
  _id: string
  date: string
  tank_type: string
  quantity: number
  kg_lost: number
  material_cost_lost?: number
  notes: string
}

export interface DNLine {
  tank_type: string
  grade: 'A' | 'B'
  quantity: number
}

export interface DeliveryNote {
  _id: string
  dn_number: string
  date: string
  client_name: string
  client_details: string
  lines: DNLine[]
  linked_invoice_id: string | null
  pdf_url: string
}

export interface InvoiceLine {
  tank_type: string
  grade: 'A' | 'B'
  quantity: number
  unit_price: number
}

export interface Invoice {
  _id: string
  invoice_number: string
  date: string
  client_name: string
  client_details: string
  lines: InvoiceLine[]
  subtotal: number
  vat: number
  vat_rate: number
  total: number
  linked_delivery_note_ids: string[]
  linked_delivery_note_numbers: string[]
  status: 'unpaid' | 'part_paid' | 'paid' | 'flagged'
  pdf_url: string
}

export interface Payment {
  _id: string
  date: string
  amount: number
  bank_reference: string
  matched_invoice_id: string | null
  split: { fenix_exworks_value: number; partner_balance: number } | null
  status: 'unmatched' | 'matched' | 'flagged'
}

export interface Flag {
  _id: string
  node_id: string
  date_raised: string
  type: string
  references: Record<string, string>
  description: string
  status: 'open' | 'resolved'
  resolved_by: string | null
  resolution_note: string | null
}

export interface PhysicalCount {
  _id: string
  date: string
  variances: {
    powder_warehouse: { powder_type: string; system: number; counted: number; variance: number }[]
    powder_floor: { powder_type: string; system: number; counted: number; variance: number }[]
    tanks: { tank_type: string; grade: string; system: number; counted: number; variance: number; store_counted: number; floor_counted: number }[]
    fittings: { fitting_type: string; system: number; counted: number; variance: number }[]
  }
  counted_by: string
}

export interface ReconDay {
  date: string
  status: 'clear' | 'flagged' | 'captured' | 'no_capture'
  capture_id: string | null
}

export interface ReconData {
  month: string
  days: ReconDay[]
  open_flags: Flag[]
  unmatched_payments: Payment[]
  unpaid_invoices: Invoice[]
}

export interface MonthlyReport {
  month: string
  kg_through_plant: number
  tanks_by_type: { tank_type: string; a: number; b: number; reject: number }[]
  ex_works_value_invoiced: number
  invoiced_value: number
  cash_received: number
  outstanding: number
  scrap_kg: number
  scrap_material_cost?: number
}
