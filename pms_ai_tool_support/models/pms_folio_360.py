from collections import Counter

from odoo import _, api, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from ..privacy import mask_email, mask_phone
from .pms_support_tools import FOLIO_INPUTS

INVOICE_TYPES = ("out_invoice", "out_refund", "out_receipt")


def _money(amount):
    return round(float(amount or 0.0), 2)


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @api.model
    def _support_folio_info(self, folio):
        channel = folio.sale_channel_origin_id
        return {
            "id": folio.id,
            "code": folio.name,
            "state": folio.state,
            "state_label": self._support_label(folio, "state"),
            "type": folio.reservation_type,
            "type_label": self._support_label(folio, "reservation_type"),
            "property": {
                "id": folio.pms_property_id.id,
                "name": folio.pms_property_id.name,
            },
            "created": self._support_local(folio.date_order),
            "confirmed": self._support_local(folio.confirmation_date),
            "first_checkin": folio.first_checkin,
            "last_checkout": folio.last_checkout,
            "external_reference": folio.external_reference or None,
            "sale_channel": channel.name or None,
            "sale_channel_type": channel.channel_type or None,
            "agency": folio.agency_id.name or None,
        }

    @api.model
    def _support_customer_info(self, folio):
        return {
            "name": folio.partner_name or folio.partner_id.name or None,
            "email": mask_email(folio.email),
            "mobile": mask_phone(folio.mobile),
        }

    @api.model
    def _support_reservation_row(self, reservation, labels):
        board = reservation.board_service_room_id.pms_board_service_id
        return {
            "id": reservation.id,
            "code": reservation.name,
            "rooms": reservation.rooms or None,
            "room_type": reservation.room_type_id.name or None,
            "board_service": board.name or None,
            "state": reservation.state,
            "state_label": labels.get(reservation.state, reservation.state),
            "adults": reservation.adults,
            "children": reservation.children,
            "checkin": reservation.checkin,
            "checkout": reservation.checkout,
            "nights": reservation.nights,
            "room_without_vat": _money(reservation.price_subtotal),
            "room_with_vat": _money(reservation.price_total),
            "services_with_vat": _money(reservation.price_services),
        }

    @api.model
    def _support_reservations(self, folio, warnings):
        reservations = folio.reservation_ids.sorted(
            lambda reservation: (reservation.folio_sequence or 0, reservation.id)
        )
        labels = self._support_labels("pms.reservation", "state")
        rows = [
            self._support_reservation_row(reservation, labels)
            for reservation in self._ai_limit_rows(list(reservations), warnings)
        ]
        counts = Counter(reservations.mapped("state"))
        return rows, {
            "total": len(reservations),
            "by_state": [
                {"state": state, "label": labels.get(state, state), "count": count}
                for state, count in counts.items()
            ],
        }

    @api.model
    def _support_payments(self, folio, warnings):
        """Posted payments and statement lines of the folio, by journal and method."""
        groups = {}

        def add(journal, method, amount):
            row = groups.setdefault(
                (journal.id, method),
                {
                    "journal": journal.name,
                    "method": method,
                    "payments": 0,
                    "refunds": 0,
                    "amount": 0.0,
                },
            )
            row["refunds" if amount < 0 else "payments"] += 1
            row["amount"] += amount

        other_currency = shared = 0
        for payment in folio.payment_ids.filtered(lambda pay: pay.state == "posted"):
            sign = -1 if payment.payment_type == "outbound" else 1
            add(
                payment.journal_id,
                payment.payment_method_line_id.name or "",
                sign * payment.amount,
            )
            if payment.currency_id != folio.currency_id:
                other_currency += 1
            if len(payment.folio_ids) > 1:
                shared += 1
        statement_label = _("Statement line")
        for line in folio.statement_line_ids.filtered(
            lambda st_line: st_line.move_id.state == "posted"
        ):
            add(line.journal_id, statement_label, line.amount)
        if other_currency:
            warnings.append(
                _(
                    "%s payments are not in the currency of the folio: their amounts "
                    "are added without conversion."
                )
                % other_currency
            )
        if shared:
            warnings.append(
                _(
                    "%s payments are shared with other folios: their whole amount "
                    "is listed here."
                )
                % shared
            )
        rows = sorted(
            (dict(row, amount=_money(row["amount"])) for row in groups.values()),
            key=lambda row: (row["journal"] or "", row["method"] or ""),
        )
        return {
            "net_total": _money(sum(row["amount"] for row in rows)),
            "by_journal_and_method": rows,
        }

    @api.model
    def _support_invoices(self, folio, warnings):
        invoices = (
            folio.move_ids
            | self.env["account.move"].search(
                [("folio_ids", "in", folio.ids), ("move_type", "in", INVOICE_TYPES)]
            )
        ).filtered(lambda move: move.move_type in INVOICE_TYPES)
        invoices = invoices.sorted(lambda move: (move.date, move.id))
        types = self._support_labels("account.move", "move_type")
        states = self._support_labels("account.move", "state")
        rows = []
        for move in self._ai_limit_rows(list(invoices), warnings):
            sign = -1 if move.move_type == "out_refund" else 1
            rows.append(
                {
                    "number": move.name if move.name and move.name != "/" else None,
                    "type": move.move_type,
                    "type_label": types.get(move.move_type, move.move_type),
                    "state": move.state,
                    "state_label": states.get(move.state, move.state),
                    "payment_state": move.payment_state,
                    "date": move.invoice_date or move.date,
                    "without_vat": _money(sign * move.amount_untaxed),
                    "with_vat": _money(sign * move.amount_total),
                }
            )
        return rows

    @api.model
    def _support_notification_counts(self, folio):
        logs = self.env["pms.notification.log"]
        groups = logs.read_group(
            self._support_notification_domain(folio),
            ["state"],
            ["state", "channel"],
            lazy=False,
        )
        states, channels = Counter(), Counter()
        for group in groups:
            states[group["state"]] += group["__count"]
            channels[group["channel"]] += group["__count"]
        state_labels = self._support_labels("pms.notification.log", "state")
        channel_labels = self._support_labels("pms.notification.log", "channel")
        return {
            "total": sum(states.values()),
            "by_state": [
                {"state": key, "label": state_labels.get(key, key), "count": count}
                for key, count in states.items()
            ],
            "by_channel": [
                {"channel": key, "label": channel_labels.get(key, key), "count": count}
                for key, count in channels.items()
            ],
        }

    @api.model
    def _support_amounts(self, folio):
        return {
            "currency": folio.currency_id.name,
            "without_vat": _money(folio.amount_untaxed),
            "vat": _money(folio.amount_tax),
            "with_vat": _money(folio.amount_total),
            "paid": _money(folio.invoices_paid),
            "pending": _money(folio.pending_amount),
            "payment_state": folio.payment_state,
            "payment_state_label": self._support_label(folio, "payment_state"),
            "invoice_status": folio.invoice_status,
            "invoice_status_label": self._support_label(folio, "invoice_status"),
        }

    @guarded_tool(
        input_schema=FOLIO_INPUTS,
        output_schema={
            "folio": {"type": "object"},
            "customer": {"type": "object"},
            "reservations": {"type": "array", "items": {"type": "object"}},
            "reservation_counts": {"type": "object"},
            "amounts": {"type": "object"},
            "payments": {"type": "object"},
            "invoices": {"type": "array", "items": {"type": "object"}},
            "notifications": {"type": "object"},
        },
        log_result=False,
    )
    def _ai_pms_folio_360(self, folio_id=None, folio_code=None):
        folio = self._support_find_folio(folio_id, folio_code)
        warnings = []
        reservations, reservation_counts = self._support_reservations(folio, warnings)
        payments = self._support_section(
            _("payments"),
            warnings,
            lambda: self._support_payments(folio, warnings),
            None,
        )
        invoices = self._support_section(
            _("invoices"),
            warnings,
            lambda: self._support_invoices(folio, warnings),
            None,
        )
        notifications = self._support_section(
            _("notifications"),
            warnings,
            lambda: self._support_notification_counts(folio),
            None,
        )
        amounts = self._support_amounts(folio)
        if folio.reservation_type != "out" and not folio.amount_total:
            warnings.append(
                _(
                    "The folio total is 0: it may be a courtesy, staff or "
                    "billed outside the PMS (not verified here)."
                )
            )
        if folio.payment_multi:
            warnings.append(
                _(
                    "The payments of this folio are shared with other folios: "
                    "the pending amount takes all of them into account."
                )
            )
        return {
            "summary": _(
                "Folio %(code)s (%(state)s) of %(property)s: %(reservations)s "
                "reservations, %(total).2f %(currency)s with VAT, %(pending).2f "
                "pending, %(notifications)s notifications."
            )
            % {
                "code": folio.name,
                "state": self._support_label(folio, "state"),
                "property": folio.pms_property_id.name,
                "reservations": reservation_counts["total"],
                "total": amounts["with_vat"],
                "currency": amounts["currency"] or "",
                "pending": amounts["pending"],
                "notifications": (notifications or {}).get("total", 0),
            },
            "definitions": [
                _(
                    "with_vat is the total of the folio (rooms and services, taxes "
                    "included) as the PMS computes it; without_vat is the net "
                    "amount and vat the taxes. Room prices of the PMS include VAT."
                ),
                _(
                    "Reservation amounts are for the room only (room_without_vat, "
                    "room_with_vat) and the services of that reservation. In "
                    "folios with several rooms the price can be set on only one "
                    "reservation and the others show 0: use the totals of the folio."
                ),
                _(
                    "paid and pending are the ones the PMS keeps on the folio: "
                    "paid is what the posted payments and statement lines linked "
                    "to the folio add up to, and pending the rest of the total."
                ),
                _(
                    "Payments: posted payments and statement lines linked to the "
                    "folio by journal and method, net of refunds. Draft and "
                    "cancelled payments are not counted."
                ),
                _(
                    "Invoices: invoices and refunds linked to the folio (refunds "
                    "are negative)."
                ),
                _(
                    "Notifications: logs of the notification system (e-mail or "
                    "WhatsApp to the guest) linked to the folio. 'Sent' means "
                    "handed over to the channel, not delivered; the detail is in "
                    "pms_folio_notifications."
                ),
                _(
                    "The customer is the holder of the folio; the e-mail and the "
                    "mobile are masked. Times are in the time zone %s."
                )
                % self._ai_timezone(),
            ],
            "warnings": warnings,
            "result": {
                "folio": self._support_folio_info(folio),
                "customer": self._support_customer_info(folio),
                "reservations": reservations,
                "reservation_counts": reservation_counts,
                "amounts": amounts,
                "payments": payments,
                "invoices": invoices,
                "notifications": notifications,
            },
        }
