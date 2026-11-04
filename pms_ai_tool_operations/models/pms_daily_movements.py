from collections import defaultdict

from odoo import _, models

from odoo.addons.pms_ai_tool.tools import guarded_tool

from .pms_ai_operations_common import PROPERTY_IDS_INPUT

# A reservation whose guests have arrived, and one that has left.
ARRIVED_STATES = ("onboard", "departure_delayed", "done")
DEPARTED_STATES = ("done",)

COUNTERS = ("reservations", "pax", "done", "pre_reservations")


def _fold(groups, done_states):
    """Group counts by property: reservations, guests and how many are done.

    ``groups`` come from ``read_group`` by property and state. Pre-reservations
    (draft) are only counted apart.
    """
    folded = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    for group in groups:
        entry = folded[group["pms_property_id"][0]]
        if group["state"] == "draft":
            entry["pre_reservations"] += group["__count"]
            continue
        entry["reservations"] += group["__count"]
        entry["pax"] += (group["adults"] or 0) + (group["children"] or 0)
        if group["state"] in done_states:
            entry["done"] += group["__count"]
    return folded


class PmsAiTools(models.AbstractModel):
    _inherit = "pms.ai.tools"

    @guarded_tool(
        input_schema={
            "date": {
                "type": "string",
                "format": "date",
                "description": "Day of the movements (YYYY-MM-DD). Default: today.",
            },
            "property_ids": PROPERTY_IDS_INPUT,
        },
        output_schema={
            "totals": {"type": "object"},
            "by_property": {"type": "array", "items": {"type": "object"}},
        },
    )
    def _ai_pms_daily_movements(self, date=None, property_ids=None):
        day = date or self._ai_ops_today()
        warnings = []
        scope = self._ai_ops_scope(property_ids, warnings)
        reservations = self.env["pms.reservation"]
        base = [
            ("pms_property_id", "in", scope),
            ("reservation_type", "!=", "out"),
            ("room_type_id.overnight_room", "=", True),
        ]
        measures = ["adults:sum", "children:sum"]
        group_by = ["pms_property_id", "state"]

        def folded(domain, done_states=()):
            groups = reservations.read_group(
                [*base, *domain], measures, group_by, lazy=False
            )
            return _fold(groups, done_states)

        arrivals = folded(
            [("checkin", "=", day), ("state", "!=", "cancel")], ARRIVED_STATES
        )
        departures = folded(
            [("checkout", "=", day), ("state", "!=", "cancel")], DEPARTED_STATES
        )
        in_house = folded(
            [
                ("checkin", "<=", day),
                ("checkout", ">", day),
                ("state", "not in", ("draft", "cancel")),
            ]
        )
        names = self._ai_ops_property_names(scope)
        rows = []
        for prop_id in sorted(scope, key=lambda pid: names.get(pid, "")):
            arr, dep, house = arrivals[prop_id], departures[prop_id], in_house[prop_id]
            rows.append(
                {
                    "property_id": prop_id,
                    "property": names.get(prop_id, ""),
                    "arrivals": {
                        "reservations": arr["reservations"],
                        "pax": arr["pax"],
                        "checked_in": arr["done"],
                        "pending": arr["reservations"] - arr["done"],
                    },
                    "departures": {
                        "reservations": dep["reservations"],
                        "pax": dep["pax"],
                        "checked_out": dep["done"],
                        "pending": dep["reservations"] - dep["done"],
                    },
                    "in_house": {"rooms": house["reservations"], "pax": house["pax"]},
                    "pre_reservations": arr["pre_reservations"]
                    + dep["pre_reservations"],
                }
            )
        totals = {
            "arrivals": {
                key: sum(row["arrivals"][key] for row in rows)
                for key in ("reservations", "pax", "checked_in", "pending")
            },
            "departures": {
                key: sum(row["departures"][key] for row in rows)
                for key in ("reservations", "pax", "checked_out", "pending")
            },
            "in_house": {
                key: sum(row["in_house"][key] for row in rows)
                for key in ("rooms", "pax")
            },
            "pre_reservations": sum(row["pre_reservations"] for row in rows),
        }
        if totals["pre_reservations"]:
            warnings.append(
                _(
                    "Pre-reservations (not confirmed) arriving or leaving on this "
                    "day are not counted: %s."
                )
                % totals["pre_reservations"]
            )
        return {
            "summary": _(
                "%(arr)s arrivals (%(arr_in)s already checked in), %(dep)s "
                "departures (%(dep_out)s already out) and %(rooms)s rooms with "
                "%(pax)s guests in house that night."
            )
            % {
                "arr": totals["arrivals"]["reservations"],
                "arr_in": totals["arrivals"]["checked_in"],
                "dep": totals["departures"]["reservations"],
                "dep_out": totals["departures"]["checked_out"],
                "rooms": totals["in_house"]["rooms"],
                "pax": totals["in_house"]["pax"],
            },
            "period": {"date_from": day, "date_to": day},
            "definitions": [
                _(
                    "Arrival: reservation with check-in on the day, in a room for "
                    "overnight stays and not cancelled. Staff stays count; out of "
                    "service blocks and pre-reservations (not confirmed) do not."
                ),
                _(
                    "Checked in: the guests of the reservation have arrived (it is "
                    "on board, or already out). Pending: still expected; if the day "
                    "has passed, a no-show not yet cancelled."
                ),
                _(
                    "Departure: reservation with check-out on the day. Checked out: "
                    "already out. Pending: still on board, or never arrived."
                ),
                _(
                    "In house: reservations with a night on the day (check-in on or "
                    "before it, check-out after it), whatever their arrival state: "
                    "rooms are reservations and pax adults plus children."
                ),
            ],
            "warnings": warnings,
            "result": {
                "totals": totals,
                "by_property": self._ai_limit_rows(rows, warnings),
            },
        }
