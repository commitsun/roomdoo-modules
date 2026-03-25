from odoo import api, fields, models

# Priority order: first matching reservation state determines the folio sort group.
# Lower value = higher sort priority.
_RESERVATION_STATE_SORT = {
    "arrival_delayed": "0_arriving",
    "confirm": "0_arriving",
    "onboard": "1_onboard",
    "departure_delayed": "1_onboard",
    "draft": "3_other",
    "cancel": "3_other",
    "done": "2_departed",
}

# When checking reservation states, the first match in this list wins.
_STATE_PRIORITY = [
    "arrival_delayed",
    "confirm",
    "onboard",
    "departure_delayed",
    "draft",
    "cancel",
    "done",
]


class PmsFolio(models.Model):
    _inherit = "pms.folio"

    fastapi_sort_state = fields.Char(
        string="Sort State",
        compute="_compute_fastapi_sort_state",
        store=True,
        index=True,
    )

    @api.depends("reservation_ids.state", "reservation_ids.cancelled_reason")
    def _compute_fastapi_sort_state(self):
        for folio in self:
            states = set(
                folio.reservation_ids.filtered(
                    lambda r: r.cancelled_reason != "modified"
                ).mapped("state")
            )
            sort_value = "3_other"
            for state in _STATE_PRIORITY:
                if state in states:
                    sort_value = _RESERVATION_STATE_SORT[state]
                    break
            folio.fastapi_sort_state = sort_value
