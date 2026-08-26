# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ChannelChannexPmsFolio(models.Model):
    """The booking as Channex identifies it.

    Bound on ``booking_id``, which is the same across every revision of one
    booking: it is the identifier that means "this reservation", where the
    revision id means "this message about it".
    """

    _name = "channel.channex.pms.folio"
    _inherit = "channel.channex.binding"
    _inherits = {"pms.folio": "odoo_id"}
    _description = "Channel Channex PMS Folio"

    odoo_id = fields.Many2one(
        comodel_name="pms.folio",
        string="Folio",
        required=True,
        ondelete="cascade",
    )

    revision_external_id = fields.Char(
        string="Applied revision",
        readonly=True,
        help="The message this folio is at. It is what makes taking the same "
        "message twice, or an older one after a newer one, decidable without "
        "asking Channex again.",
    )
    revision_inserted_at = fields.Datetime(
        string="Applied revision date",
        readonly=True,
    )
