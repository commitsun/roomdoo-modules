from datetime import datetime

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, models


class PmsAiTools(models.AbstractModel):
    """Helpers shared by the revenue tools (the tools themselves are in their
    own files). Their names start with ``_ai_rev_`` to stay clear of the
    helpers of the other packs."""

    _inherit = "pms.ai.tools"

    @api.model
    def _ai_rev_now(self):
        """The current moment as a local datetime and as a naive UTC one."""
        local = datetime.now(pytz.timezone(self._ai_timezone()))
        return local, local.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _ai_rev_a_year_ago(self, local):
        """The same local moment one year earlier: local and naive UTC."""
        tz = pytz.timezone(self._ai_timezone())
        earlier = tz.localize(local.replace(tzinfo=None) - relativedelta(years=1))
        return earlier, earlier.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _ai_rev_property_names(self, property_ids):
        return {
            prop.id: prop.name
            for prop in self.env["pms.property"].browse(list(property_ids))
        }

    @api.model
    def _ai_rev_definitions(self):
        """Definitions that several revenue tools state in their envelope."""
        return {
            "nights": _(
                "Room night: a reservation line dated in the period that occupies "
                "availability (neither cancelled nor resold) in a normal "
                "reservation (staff and out of service are left out) and in a "
                "room for overnight stays. Pre-reservations count, as in the "
                "other tools."
            ),
            "revenue": _(
                "Room revenue without VAT: final night price (after discounts) "
                "divided by 1 plus the percentage of the taxes included in the "
                "price of the reservation. Services and extras are not included. "
                "room_revenue_incl_vat is the final night price as the PMS stores "
                "it and the other tools use it: it carries VAT when the room "
                "prices do (the Alda configuration)."
            ),
            "adr": _(
                "ADR: room revenue without VAT / room nights, leaving out the "
                "nights of folios whose whole stay is worth 0 (courtesies or "
                "invoiced outside the PMS; reported as zero_value_nights). Group "
                "folios with the price in one room do count."
            ),
        }

    @api.model
    def _ai_rev_quality_warnings(self, counters):
        """Warnings about the nights counted in ``counters``."""
        warnings = []
        if counters["zero_nights"]:
            warnings.append(
                _(
                    "%s nights of stays worth 0 (courtesies or invoiced outside "
                    "the PMS) count as room nights but are left out of the ADR."
                )
                % counters["zero_nights"]
            )
        if counters["untaxed_nights"]:
            warnings.append(
                _(
                    "%s nights belong to reservations with no taxes: their price "
                    "is taken as already without VAT."
                )
                % counters["untaxed_nights"]
            )
        return warnings
