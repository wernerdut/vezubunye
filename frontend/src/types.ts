export type Role = 'admin' | 'controller' | 'capturer'

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
}

export interface NodeConfig {
  node_id: string
  tank_types: TankType[]
  material_cost_per_kg?: number
  b_grade_exworks_pct: number
  vat_rate: number
  payment_terms_days: number
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
  quantity_a: number
  quantity_b: number
  quantity_reject: number
}

export interface CaptureEntries {
  powder_in_kg: number
  powder_drawn_kg: number
  production: ProductionLine[]
  notes?: string
}

export interface PowderEntry {
  _id: string
  date: string
  type: 'in' | 'drawn' | 'count_adjustment'
  kg: number
  notes: string
  running_balance: number
}

export interface ProductionRun {
  _id: string
  date: string
  tank_type: string
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
  type: 'produced' | 'delivered' | 'count_adjustment'
  quantity: number
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
  powder_kg_counted: number
  finished_goods_counted: OnHand[]
  system_values_at_count: { powder_kg: number; finished_goods: OnHand[] }
  variances: {
    powder_kg: number
    finished_goods: { tank_type: string; grade: string; system: number; counted: number; variance: number }[]
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
