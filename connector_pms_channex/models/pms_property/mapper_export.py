# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ChannelChannexPmsPropertyExportMapper(Component):
    _name = "channel.channex.pms.property.export.mapper"
    _inherit = "channel.channex.export.mapper"
    _apply_on = "channel.channex.pms.property"

    @mapping
    def identity(self, record):
        # ``company_id`` and ``tz`` are required on pms.property, and a company
        # always has a currency, so none of these needs a guard here.
        pms_property = record.odoo_id
        return {
            "title": pms_property.name,
            "currency": pms_property.company_id.currency_id.name,
            "timezone": pms_property.tz,
            "property_type": record.channex_property_type,
            "group_id": record.backend_id._channex_group_id(),
        }

    @mapping
    def contact(self, record):
        partner = record.odoo_id.partner_id
        street = " ".join(filter(None, [partner.street, partner.street2]))
        return {
            "email": partner.email or None,
            "phone": partner.phone or partner.mobile or None,
            "address": street or None,
            "city": partner.city or None,
            "zip_code": partner.zip or None,
            "country": partner.country_id.code or None,
            "state": partner.state_id.name or None,
            "latitude": partner.partner_latitude or None,
            "longitude": partner.partner_longitude or None,
        }

    @mapping
    def cancellation_policy(self, record):
        """The policy the OTAs show for this hotel.

        Left out of the payload rather than sent empty when Odoo has nothing to
        say. A hotel that set a policy by hand in Channex before this connector
        existed would otherwise lose it on the first export, and what a guest
        was promised is not something to drop quietly.
        """
        policy = record._channex_default_cancellation_policy_id()
        if not policy:
            return {}
        return {"default_cancellation_policy_id": policy}

    @mapping
    def settings(self, record):
        return {
            "settings": {
                "allow_availability_autoupdate_on_confirmation": (
                    record.allow_avail_autoupdate_on_confirmation
                ),
                "allow_availability_autoupdate_on_modification": (
                    record.allow_avail_autoupdate_on_modification
                ),
                "allow_availability_autoupdate_on_cancellation": (
                    record.allow_avail_autoupdate_on_cancellation
                ),
                "min_stay_type": record.min_stay_type,
                "state_length": record.state_length,
                "cut_off_time": record.cut_off_time or "00:00:00",
                "cut_off_days": record.cut_off_days,
            }
        }
