from enum import Enum

from pydantic import Field

from .base import CurrencyAmount, PmsBaseModel
from .currency import CurrencySummary
from .pms_reservation import reservationSummary


class saleLineTypeEnum(str, Enum):
    ROOM = "room"
    SERVICE = "service"


class saleLineInvoiceStateEnum(str, Enum):
    PENDING = "pending"
    PARTIALLY_INVOICED = "partiallyInvoiced"
    INVOICED = "invoiced"


class SaleLineTax(PmsBaseModel):
    id: int
    name: str
    base: float
    amount: float

    @classmethod
    def from_tax_data(cls, tax_data: dict):
        return cls(
            id=tax_data["id"],
            name=tax_data["name"],
            base=round(tax_data["base"], 2),
            amount=round(tax_data["amount"], 2),
        )


class SaleLineInvoice(PmsBaseModel):
    id: int
    name: str
    quantityInvoiced: float


class FolioSaleLine(PmsBaseModel):
    id: int
    lineType: saleLineTypeEnum
    description: str = Field("")
    reservation: reservationSummary | None = None
    quantity: float
    quantityInvoiced: float = Field(0.0)
    priceUnit: float = Field(0.0)
    discount: float = Field(0.0)
    taxes: list[SaleLineTax] = Field(default_factory=list)
    subtotal: CurrencyAmount = Field(0.0)
    taxAmount: CurrencyAmount = Field(0.0)
    total: CurrencyAmount = Field(0.0)
    currency: CurrencySummary
    invoiceState: saleLineInvoiceStateEnum
    invoices: list[SaleLineInvoice] = Field(default_factory=list)

    @classmethod
    def from_folio_sale_line(cls, line):
        line_type = (
            saleLineTypeEnum.SERVICE if line.service_id else saleLineTypeEnum.ROOM
        )

        taxes = []
        if line.tax_ids and line.product_uom_qty:
            taxes_data = line.tax_ids.compute_all(
                price_unit=line.price_unit * (1.0 - line.discount / 100.0),
                currency=line.currency_id,
                quantity=line.product_uom_qty,
            )
            for tax_data in taxes_data["taxes"]:
                taxes.append(SaleLineTax.from_tax_data(tax_data))

        invoice_by_move = {}
        for inv_line in line.invoice_lines:
            move = inv_line.move_id
            if move.state == "cancel":
                continue
            if move.id not in invoice_by_move:
                invoice_by_move[move.id] = {"move": move, "qty": 0.0}
            if move.move_type in ("out_invoice", "out_receipt"):
                invoice_by_move[move.id]["qty"] += inv_line.quantity
            elif move.move_type == "out_refund":
                invoice_by_move[move.id]["qty"] -= inv_line.quantity
        invoices = [
            SaleLineInvoice(
                id=entry["move"].id,
                name=entry["move"].name,
                quantityInvoiced=entry["qty"],
            )
            for entry in invoice_by_move.values()
        ]

        if line.invoice_status == "invoiced":
            invoice_state = saleLineInvoiceStateEnum.INVOICED
        elif line.invoice_status == "to_invoice" and line.qty_invoiced > 0:
            invoice_state = saleLineInvoiceStateEnum.PARTIALLY_INVOICED
        else:
            invoice_state = saleLineInvoiceStateEnum.PENDING

        decimal_places = line.currency_id.decimal_places if line.currency_id else 2

        return cls(
            **{
                "_decimal_places": decimal_places,
                "id": line.id,
                "lineType": line_type,
                "description": line.name or "",
                "reservation": (
                    reservationSummary.from_pms_reservation(line.reservation_id)
                    if line.reservation_id
                    else None
                ),
                "quantity": line.product_uom_qty,
                "quantityInvoiced": line.qty_invoiced,
                "priceUnit": round(line.price_unit, 2),
                "discount": line.discount or 0.0,
                "taxes": taxes,
                "subtotal": line.price_subtotal,
                "taxAmount": line.price_tax,
                "total": line.price_total,
                "currency": CurrencySummary.from_res_currency(line.currency_id),
                "invoiceState": invoice_state,
                "invoices": invoices,
            }
        )
