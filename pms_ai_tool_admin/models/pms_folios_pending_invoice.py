from collections import defaultdict

from odoo import _, fields, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .common import DATE_FROM_INPUT, MAX_DAYS, PROPERTY_IDS_INPUT, amount

REASONS = ("no_customer", "no_vat", "payment_pending", "partial_invoice")

# Folios that the PMS flags "To invoice" with an amount above 0, whose last
# checkout (cancelled reservations aside) falls in the period. The cheap
# conditions come first: last_checkout is the latest checkout of all the
# reservations, so it can only be later than the one measured here, and
# first_checkin is never later than it.
PENDING_FOLIOS_QUERY = """
WITH cand AS (
    SELECT f.id, f.name, f.pms_property_id AS property_id, f.partner_id,
           f.pending_amount, f.untaxed_amount_to_invoice AS amount
      FROM pms_folio f
     WHERE f.pms_property_id = ANY(%(property_ids)s)
       AND f.company_id = ANY(%(company_ids)s)
       AND f.state IN ('confirm', 'done')
       AND f.reservation_type = 'normal'
       AND f.invoice_status = 'to_invoice'
       AND f.untaxed_amount_to_invoice > 0
       AND f.last_checkout >= %(date_from)s
       AND f.first_checkin <= %(date_to)s
),
res AS (
    SELECT r.folio_id,
           max(r.checkout) FILTER (WHERE r.state <> 'cancel') AS checkout,
           count(*) FILTER (WHERE r.state = 'done') AS checked_out,
           count(*) FILTER (WHERE r.state NOT IN ('done', 'cancel')) AS open_count
      FROM pms_reservation r
     WHERE r.folio_id IN (SELECT id FROM cand)
  GROUP BY r.folio_id
)
SELECT c.id, c.name, c.property_id, c.amount, res.checkout,
       res.open_count > 0 AS not_checked_out,
       (c.partner_id IS NULL OR c.partner_id = %(anonymous_id)s) AS no_customer,
       (c.partner_id IS NOT NULL AND c.partner_id <> %(anonymous_id)s
        AND coalesce(cp.vat, '') = '') AS no_vat,
       c.pending_amount > 0 AS payment_pending,
       EXISTS (
           SELECT 1
             FROM folio_sale_line sl
            WHERE sl.folio_id = c.id
              AND sl.qty_invoiced > 0
              AND sl.display_type IS NULL
              AND NOT coalesce(sl.is_downpayment, false)
       ) AS partial_invoice
  FROM cand c
  JOIN res ON res.folio_id = c.id
  LEFT JOIN res_partner p ON p.id = c.partner_id
  LEFT JOIN res_partner cp ON cp.id = p.commercial_partner_id
 WHERE res.checkout BETWEEN %(date_from)s AND %(date_to)s
   AND ((res.open_count = 0 AND res.checked_out > 0)
        OR (res.open_count > 0 AND res.checkout < %(today)s))
"""


def _new_counters():
    return {"folios": 0, "amount": 0.0, **dict.fromkeys(REASONS, 0)}


def _metrics(counters):
    return {
        "folios": counters["folios"],
        "amount_to_invoice_untaxed": amount(counters["amount"]),
        "reasons": {reason: counters[reason] for reason in REASONS},
    }


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date_from": dict(
                DATE_FROM_INPUT,
                description="First checkout date of the period (YYYY-MM-DD).",
            ),
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Last checkout date, included (YYYY-MM-DD). "
                f"At most {MAX_DAYS} days after date_from.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "folios": {"type": "array", "items": {"type": "object"}},
        },
        required_inputs=["date_from", "date_to"],
    )
    def _ai_pms_folios_pending_invoice(self, date_from, date_to, property_ids=None):
        period = self._ai_check_period(date_from, date_to, MAX_DAYS)
        anonymous = self.env.ref("pms.various_pms_partner", raise_if_not_found=False)
        rows = self._ai_fetch(
            PENDING_FOLIOS_QUERY,
            {
                "date_from": date_from,
                "date_to": date_to,
                "today": fields.Date.context_today(self),
                "property_ids": self._ai_scope_property_ids(property_ids),
                "company_ids": self._ai_scope_company_ids(),
                "anonymous_id": anonymous.id or 0,
            },
        )
        finished = [row for row in rows if not row["not_checked_out"]]
        open_rows = [row for row in rows if row["not_checked_out"]]
        totals = _new_counters()
        by_property = defaultdict(_new_counters)
        for row in finished:
            for counters in (totals, by_property[row["property_id"]]):
                counters["folios"] += 1
                counters["amount"] += float(row["amount"])
                for reason in REASONS:
                    counters[reason] += 1 if row[reason] else 0
        names = {
            prop.id: prop.name
            for prop in self.env["pms.property"].browse(list(by_property))
        }
        property_rows = sorted(
            (
                {
                    "property_id": property_id,
                    "property": names.get(property_id, ""),
                    **_metrics(counters),
                }
                for property_id, counters in by_property.items()
            ),
            key=lambda r: (-r["amount_to_invoice_untaxed"], r["property"]),
        )
        warnings = []
        largest = self._ai_limit_rows(
            sorted(
                finished,
                key=lambda r: (-float(r["amount"]), r["checkout"], r["name"]),
            ),
            warnings,
        )
        if open_rows:
            warnings.append(
                _(
                    "%(folios)s folios with an amount to invoice (%(amount)s "
                    "without taxes) have their last checkout in the period but "
                    "reservations not checked out in the PMS (for example "
                    "no-shows or delayed departures), so they are not counted "
                    "until the hotel closes them."
                )
                % {
                    "folios": len(open_rows),
                    "amount": amount(sum(float(r["amount"]) for r in open_rows)),
                }
            )
        total_metrics = _metrics(totals)
        return {
            "summary": _(
                "%(folios)s finished folios still to invoice (%(amount)s without "
                "taxes) with checkout from %(from)s to %(to)s."
            )
            % {
                "folios": total_metrics["folios"],
                "amount": total_metrics["amount_to_invoice_untaxed"],
                "from": date_from,
                "to": date_to,
            },
            "period": period,
            "definitions": [
                _(
                    "Finished folio: normal folio with at least one reservation "
                    "that is not cancelled and all of them checked out (state "
                    "Out). Checkout: the latest checkout date of its reservations "
                    "that are not cancelled."
                ),
                _(
                    "Still to invoice: the PMS flags the folio as To invoice and "
                    "its amount to invoice is above 0. amount_to_invoice_untaxed "
                    "is that PMS amount, without taxes and without down payments, "
                    "in the folio currency. A folio whose whole amount is already "
                    "in a draft invoice is not counted."
                ),
                _(
                    "Reason hints, not exclusive: no_customer (the folio has no "
                    "customer or it is the anonymous one), no_vat (the customer "
                    "has no tax id), payment_pending (the folio still has an "
                    "amount to be paid), partial_invoice (some nights or services "
                    "are already in an invoice, posted or draft). A folio without "
                    "hints has simply not been invoiced yet."
                ),
                _(
                    "The listed folios are the ones with the largest amounts; "
                    "the figures by property cover all of them."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": total_metrics,
                "by_property": property_rows,
                "folios": [
                    {
                        "folio": row["name"],
                        "property_id": row["property_id"],
                        "property": names.get(row["property_id"], ""),
                        "checkout": row["checkout"],
                        "amount_to_invoice_untaxed": amount(row["amount"]),
                        "reasons": [reason for reason in REASONS if row[reason]],
                    }
                    for row in largest
                ],
            },
        }
