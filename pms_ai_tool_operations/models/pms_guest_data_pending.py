from collections import defaultdict
from datetime import timedelta

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT

MAX_DAYS = 7
MAX_FOLIOS = 50
MAX_MISSING_ITEMS = 10
# Arrivals that still have to hand in their data: expected, late, or with only
# some of the guests on board.
ARRIVAL_STATES = ("confirm", "arrival_delayed", "onboard")
# Checkin partner states with data to complete: nothing yet, or not enough.
NO_DATA = "dummy"
INCOMPLETE = "draft"


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date_from": {
                "type": "string",
                "format": "date",
                "description": "First arrival day (YYYY-MM-DD). Default: today.",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DAYS,
                "description": f"Arrival days to review, 1 to {MAX_DAYS} (3).",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
            "folios": {"type": "array", "items": {"type": "object"}},
        },
        log_result=False,
    )
    def _ai_pms_guest_data_pending(self, date_from=None, days=3, property_ids=None):
        date_from = date_from or self._ai_ops_today()
        date_to = date_from + timedelta(days=days - 1)
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        reservations = self.env["pms.reservation"]
        domain = [
            ("pms_property_id", "in", scope),
            ("checkin", ">=", date_from),
            ("checkin", "<=", date_to),
            ("reservation_type", "=", "normal"),
            ("room_type_id.overnight_room", "=", True),
            ("state", "in", ARRIVAL_STATES),
        ]
        pending_domain = [*domain, ("pending_checkin_data", ">", 0)]
        arrivals = {
            self._ai_ops_group_property(group): group["__count"]
            for group in reservations.read_group(
                domain, ["pms_property_id"], ["pms_property_id"], lazy=False
            )
        }
        pending = {
            self._ai_ops_group_property(group): group
            for group in reservations.read_group(
                pending_domain,
                ["pending_checkin_data:sum", "folio_id:count_distinct"],
                ["pms_property_id"],
                lazy=False,
            )
        }
        names = self._ai_ops_property_names(scope)
        by_property = []
        for prop_id in scope:
            group = pending.get(prop_id)
            by_property.append(
                {
                    "property_id": prop_id,
                    "property": names.get(prop_id, ""),
                    "arrivals": arrivals.get(prop_id, 0),
                    "with_pending_data": group["__count"] if group else 0,
                    "guests_pending": (group["pending_checkin_data"] or 0)
                    if group
                    else 0,
                    "folios": group["folio_id"] if group else 0,
                }
            )
        by_property.sort(key=lambda row: (-row["guests_pending"], row["property"]))
        totals = {
            key: sum(row[key] for row in by_property)
            for key in ("arrivals", "with_pending_data", "guests_pending", "folios")
        }
        folios = self._ai_ops_pending_folios(pending_domain, warnings)
        return {
            "summary": _(
                "%(guests)s guests in %(folios)s folios (%(reservations)s of "
                "%(arrivals)s arriving reservations) still have to complete their "
                "check-in data between %(date_from)s and %(date_to)s."
            )
            % {
                "guests": totals["guests_pending"],
                "folios": totals["folios"],
                "reservations": totals["with_pending_data"],
                "arrivals": totals["arrivals"],
                "date_from": date_from,
                "date_to": date_to,
            },
            "period": {"date_from": date_from, "date_to": date_to},
            "definitions": [
                _(
                    "Arrivals: normal reservations with check-in in the period, in "
                    "a room for overnight stays, confirmed and not cancelled "
                    "(also those on board with only some of the guests in). Staff "
                    "stays, out of service blocks and pre-reservations (not "
                    "confirmed) are left out."
                ),
                _(
                    "A guest has pending data when the check-in record has no data "
                    "at all (unknown guest) or lacks some mandatory field "
                    "(incomplete data): the pre-check-in is not done."
                ),
                _(
                    "Missing: the mandatory fields, such as the identity document, "
                    "that the incomplete guests of the folio still lack. Holders "
                    "are shown as first name and initial."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(by_property, warnings),
                "folios": folios,
            },
        }

    def _ai_ops_pending_folios(self, pending_domain, warnings):
        """Folios with arrivals pending data, by first arrival (at most 50)."""
        reservations = self.env["pms.reservation"]
        arrival = {}
        folio_reservations = defaultdict(list)
        for record in reservations.search_read(
            pending_domain, ["folio_id", "checkin"], order="checkin, folio_id, id"
        ):
            folio_id = record["folio_id"][0]
            arrival.setdefault(folio_id, record["checkin"])
            folio_reservations[folio_id].append(record["id"])
        shown = self._ai_limit_rows(list(arrival), warnings, MAX_FOLIOS)
        guests_by_folio = {}
        missing_by_folio = {}
        for folio_id in shown:
            guests = (
                reservations.browse(folio_reservations[folio_id])
                .checkin_partner_ids.filtered(
                    lambda guest: guest.state in (NO_DATA, INCOMPLETE)
                )
                .sorted("id")
            )
            guests_by_folio[folio_id] = guests
            missing = defaultdict(int)
            for guest in guests.filtered(lambda guest: guest.state == INCOMPLETE):
                for name in guest._checkin_mandatory_fields():
                    if not guest[name]:
                        missing[name] += 1
            missing_by_folio[folio_id] = missing
        every_name = {name for missing in missing_by_folio.values() for name in missing}
        labels = {}
        if every_name:
            fields_info = self.env["pms.checkin.partner"].fields_get(
                list(every_name), attributes=["string"]
            )
            labels = {name: info["string"] for name, info in fields_info.items()}
        result = []
        for folio in self.env["pms.folio"].browse(shown):
            guests = guests_by_folio[folio.id]
            missing = missing_by_folio[folio.id]
            result.append(
                {
                    "folio_id": folio.id,
                    "folio": folio.name,
                    "property_id": folio.pms_property_id.id,
                    "property": folio.pms_property_id.name,
                    "arrival": arrival[folio.id],
                    "holder": self._ai_ops_mask_name(
                        folio.partner_name or folio.partner_id.name
                    ),
                    "reservations": len(folio_reservations[folio.id]),
                    "guests_pending": len(guests),
                    "unknown_guests": len(
                        guests.filtered(lambda g: g.state == NO_DATA)
                    ),
                    "incomplete_guests": len(
                        guests.filtered(lambda g: g.state == INCOMPLETE)
                    ),
                    "missing": [
                        labels.get(name, name)
                        for name in sorted(
                            missing, key=lambda name: (-missing[name], name)
                        )[:MAX_MISSING_ITEMS]
                    ],
                }
            )
        result.sort(key=lambda row: (row["arrival"], row["folio"]))
        return result
