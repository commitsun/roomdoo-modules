# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.addons.component.core import Component


class ChannelChannexPmsFolioBinder(Component):
    """Plain binder: ``booking_id`` is stable, so there is no second key to
    reconcile against, which is what Wubook needs its alternative id for."""

    _name = "channel.channex.pms.folio.binder"
    _inherit = "channel.channex.binder"
    _apply_on = "channel.channex.pms.folio"
