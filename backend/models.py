"""Pydantic models for Vezubunye. node_id is required on every record."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.utcnow()


# ---------- nodes & config ---------- #

class NodeIn(BaseModel):
    node_id: str
    name: str
    location: str
    status: Literal["active", "inactive"] = "active"
    # Brief 2: the node's partner app
    slug: Optional[str] = None            # <slug>.vezubunye.com, unique, lowercase
    prefix: Optional[str] = None          # document numbers: <PREFIX>-INV-0001
    partner_app_live: Optional[bool] = None
    branding: Optional[dict] = None
    company: Optional[dict] = None


class TankType(BaseModel):
    code: str
    name: str
    ex_works_price: float  # ex VAT, Rand
    weight_kg: float       # body weight; 50% colour + 50% black
    lid_weight_kg: float = 1.0  # black lid, same black stock, configurable per tank type
    kind: Literal["water", "septic"] = "water"
    capacity_l: Optional[float] = None
    diameter_mm: Optional[float] = None
    height_mm: Optional[float] = None
    allowed_colours: List[str] = []     # empty = all colours
    active: bool = True


class Colour(BaseModel):
    code: str
    name: str
    swatch_hex: str = "#999999"
    availability: Literal["standard", "on_request"] = "standard"
    powder_product: str = ""            # outer-layer powder it is moulded from


class PowderProduct(BaseModel):
    code: str
    colour: str = ""
    description: str = ""
    is_black: bool = False  # every black product feeds one black pool for R1


class FittingType(BaseModel):
    code: str
    name: str = ""


class Tolerances(BaseModel):
    powder_kg: float = 0.0
    tank_qty: int = 0
    fittings_qty: int = 0


class NodeConfigIn(BaseModel):
    tank_types: List[TankType]
    material_cost_per_kg: float  # admin-only visibility
    b_grade_exworks_pct: float = 100.0  # % of ex-works Fenix draws on a B-grade sale
    vat_rate: float = 15.0
    payment_terms_days: int = 30
    powder_products: List[PowderProduct] = []
    fitting_types: List[FittingType] = []
    fittings_per_tank: dict = {}        # { tank_code: { fitting_code: qty } }
    paraffin_litres_per_tank: float = 11.0  # release agent drawn per tank moulded
    tolerances: Tolerances = Tolerances()
    # Brief 2 (left out of a PUT means unchanged)
    colours: Optional[List[Colour]] = None
    track_colour_stock: Optional[bool] = None
    commercial_model: Optional[Literal["fenix_owns_tanks", "powder_sale"]] = None
    price_tiers: Optional[List[dict]] = None
    charges: Optional[List[dict]] = None
    quote_validity_days: Optional[int] = None
    cash_banking_days: Optional[int] = None
    drivers: Optional[List[dict]] = None


# ---------- daily capture ---------- #

class PowderMoveLine(BaseModel):
    powder_type: str
    received_kg: float = 0.0   # from Fenix into the powder warehouse
    issued_kg: float = 0.0     # warehouse -> production floor


class FittingMoveLine(BaseModel):
    fitting_type: str
    received_qty: int = 0
    issued_qty: int = 0


class ProductionLine(BaseModel):
    tank_type: str
    colour: str = ""          # powder grade moulded (outer layer powder product code)
    tank_colour: str = ""     # the node's colour code; derived from `colour` when blank
    quantity_a: int = 0
    quantity_b: int = 0
    quantity_reject: int = 0


class BookedLine(BaseModel):
    tank_type: str
    quantity_a: int = 0   # floor -> finished-goods store
    quantity_b: int = 0


class DispatchLine(BaseModel):
    tank_type: str
    grade: Literal["A", "B"]
    quantity: int = 0
    dn_number: str = ""   # required when quantity > 0


class CaptureEntriesIn(BaseModel):
    powder: List[PowderMoveLine] = []
    fittings: List[FittingMoveLine] = []
    production: List[ProductionLine] = []
    booked: List[BookedLine] = []
    paraffin_received: float = 0.0   # litres of paraffin received into stock that day
    notes: Optional[str] = None
    reason: Optional[str] = None     # required when re-capturing an already captured day
    # Tank dispatch (stock-out) is no longer captured here — it happens on the Deliveries tab.


# ---------- delivery notes (priced in-app, no price on the PDF) ---------- #

class DNLine(BaseModel):
    tank_type: str
    colour: str = ""
    grade: Literal["A", "B"] = "A"
    quantity: int
    unit_price: float = 0.0   # ex-VAT sale price; tracked in-app to reconcile, never on the PDF


class DeliveryNoteIn(BaseModel):
    date: str  # YYYY-MM-DD
    client_name: str
    client_details: str = ""
    lines: List[DNLine]


class DeliveryNoteEditIn(BaseModel):
    """Header-only edit. Lines, quantities, prices, grade or date change = reissue."""
    client_name: Optional[str] = None
    client_details: Optional[str] = None
    reason: str


class DeliveryNoteReissueIn(DeliveryNoteIn):
    reason: str


# ---------- payments ---------- #

class PaymentIn(BaseModel):
    date: str
    amount: float
    bank_reference: str = ""


class PaymentMatchIn(BaseModel):
    delivery_id: str


class PaymentEditIn(BaseModel):
    date: Optional[str] = None
    amount: Optional[float] = None
    bank_reference: Optional[str] = None
    reason: str


# ---------- corrections ---------- #

class CorrectionIn(BaseModel):
    """Void, unmatch, reopen: the reason is mandatory and lands in the audit log."""
    reason: str


# ---------- flags ---------- #

FlagType = Literal[
    "powder_variance", "fittings_variance", "finished_goods_mismatch",
    "delivery_unpaid", "payment_unmatched",
    "short_paid", "over_paid", "count_mismatch", "post_count_correction",
    # Brief 2
    "invoice_unpaid", "dn_not_invoiced", "released_unpaid", "cod_not_collected", "refund_due",
    "payment_unallocated", "cash_not_banked", "cash_deposit_mismatch",
]


class FlagResolveIn(BaseModel):
    resolution_note: str


# ---------- physical counts ---------- #

class FGCountLine(BaseModel):
    tank_type: str
    colour: str = ""
    grade: Literal["A", "B"] = "A"
    quantity: int = 0


class PowderCountLine(BaseModel):
    powder_type: str
    warehouse_kg: float = 0.0
    floor_kg: float = 0.0


class FittingCountLine(BaseModel):
    fitting_type: str
    warehouse_qty: int = 0


class PhysicalCountIn(BaseModel):
    date: str
    powder_counted: List[PowderCountLine] = []      # per type: warehouse + floor
    fg_warehouse_counted: List[FGCountLine] = []     # finished-goods store
    tank_floor_counted: List[FGCountLine] = []       # moulded, not yet booked
    fittings_counted: List[FittingCountLine] = []


# ---------- users / auth ---------- #

class LoginIn(BaseModel):
    email: str
    password: str


class PartnerUserIn(BaseModel):
    email: str
    password: Optional[str] = None
    name: str = ""
    role: Literal["partner_admin", "partner_staff"] = "partner_staff"
    employer: Literal["partner", "fenix"] = "partner"
    active: bool = True


class UserIn(BaseModel):
    email: str
    password: Optional[str] = None
    name: str = ""
    role: Literal["admin", "audit", "operations"]
    node_access: List[str] | Literal["all"] = "all"


class LedgerAdjustIn(BaseModel):
    date: str
    kg: Optional[float] = None        # powder adjustment
    powder_type: Optional[str] = None
    scope: Literal["warehouse", "floor"] = "warehouse"  # powder position adjusted
    tank_type: Optional[str] = None   # finished goods / tank-floor adjustment
    grade: Optional[Literal["A", "B"]] = None
    colour: str = ""
    quantity: Optional[int] = None
    fitting_type: Optional[str] = None  # fittings adjustment
    notes: str = ""
