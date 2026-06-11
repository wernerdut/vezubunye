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


class TankType(BaseModel):
    code: str
    name: str
    ex_works_price: float  # ex VAT, Rand
    weight_kg: float       # body weight; 50% colour + 50% black
    lid_weight_kg: float = 1.0  # black lid, same black stock, configurable per tank type


class PowderProduct(BaseModel):
    code: str
    colour: str = ""
    description: str = ""
    is_black: bool = False  # exactly one product is the black stock; rest are colour


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
    tolerances: Tolerances = Tolerances()


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
    dispatched: List[DispatchLine] = []
    notes: Optional[str] = None


# ---------- delivery notes & invoices ---------- #

class DNLine(BaseModel):
    tank_type: str
    grade: Literal["A", "B"] = "A"
    quantity: int


class DeliveryNoteIn(BaseModel):
    date: str  # YYYY-MM-DD
    client_name: str
    client_details: str = ""
    lines: List[DNLine]


class InvoiceLine(BaseModel):
    tank_type: str
    grade: Literal["A", "B"] = "A"
    quantity: int
    unit_price: float  # partner's sale price ex VAT; B-grade lines carry the reduced price


class InvoiceIn(BaseModel):
    date: str
    client_name: str
    client_details: str = ""
    lines: List[InvoiceLine]
    delivery_note_ids: List[str] = []


# ---------- payments ---------- #

class PaymentIn(BaseModel):
    date: str
    amount: float
    bank_reference: str = ""


class PaymentMatchIn(BaseModel):
    invoice_id: str


# ---------- flags ---------- #

FlagType = Literal[
    "powder_variance", "fittings_variance", "finished_goods_mismatch",
    "delivery_without_invoice", "invoice_unpaid", "payment_unmatched",
    "short_paid", "over_paid", "count_mismatch",
]


class FlagResolveIn(BaseModel):
    resolution_note: str


# ---------- physical counts ---------- #

class FGCountLine(BaseModel):
    tank_type: str
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
    quantity: Optional[int] = None
    fitting_type: Optional[str] = None  # fittings adjustment
    notes: str = ""
