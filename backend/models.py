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
    weight_kg: float


class NodeConfigIn(BaseModel):
    tank_types: List[TankType]
    material_cost_per_kg: float  # admin-only visibility
    b_grade_exworks_pct: float = 100.0  # % of ex-works Fenix draws on a B-grade sale
    vat_rate: float = 15.0
    payment_terms_days: int = 30


# ---------- daily capture ---------- #

class ProductionLine(BaseModel):
    tank_type: str
    quantity_a: int = 0
    quantity_b: int = 0
    quantity_reject: int = 0


class DispatchLine(BaseModel):
    tank_type: str
    grade: Literal["A", "B"]
    quantity: int


class CaptureEntriesIn(BaseModel):
    powder_in_kg: float = 0.0
    powder_drawn_kg: float = 0.0
    production: List[ProductionLine] = []
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
    "powder_variance", "finished_goods_mismatch", "delivery_without_invoice",
    "invoice_unpaid", "payment_unmatched", "short_paid", "over_paid", "count_mismatch",
]


class FlagResolveIn(BaseModel):
    resolution_note: str


# ---------- physical counts ---------- #

class FGCountLine(BaseModel):
    tank_type: str
    grade: Literal["A", "B"] = "A"
    quantity: int


class PhysicalCountIn(BaseModel):
    date: str
    powder_kg_counted: float
    finished_goods_counted: List[FGCountLine] = []


# ---------- users / auth ---------- #

class LoginIn(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    email: str
    password: Optional[str] = None
    name: str = ""
    role: Literal["admin", "controller", "capturer"]
    node_access: List[str] | Literal["all"] = "all"


class LedgerAdjustIn(BaseModel):
    date: str
    kg: Optional[float] = None        # powder adjustment
    tank_type: Optional[str] = None   # finished goods adjustment
    grade: Optional[Literal["A", "B"]] = None
    quantity: Optional[int] = None
    notes: str = ""
