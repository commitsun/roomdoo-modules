# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, fields, models

from ..exceptions import RESULTS


class PmsPaymentCardAccessLog(models.Model):
    """Who did what with the payment card of a folio, and how it went.

    It never holds card data nor passwords: only who, when, which folio and
    the outcome.
    """

    _name = "pms.payment.card.access.log"
    _description = "Payment card access log"
    _order = "timestamp desc, id desc"
    _rec_name = "timestamp"

    timestamp = fields.Datetime(
        required=True,
        readonly=True,
        default=fields.Datetime.now,
        index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        required=True,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    folio_id = fields.Many2one(
        comodel_name="pms.folio",
        readonly=True,
        ondelete="set null",
        index=True,
    )
    pms_property_id = fields.Many2one(
        comodel_name="pms.property",
        readonly=True,
        ondelete="set null",
        index=True,
    )
    action = fields.Selection(
        selection=[
            ("view_details", "View card details"),
            ("add", "Add card"),
            ("edit", "Edit card"),
            ("delete", "Delete card"),
            ("set_password", "Set card password"),
            ("change_password", "Change card password"),
            ("reset_password", "Reset card password"),
        ],
        required=True,
        readonly=True,
    )
    result = fields.Selection(selection=RESULTS, required=True, readonly=True)
    remote_addr = fields.Char(string="IP address", readonly=True)

    @api.model
    def _log(self, action, result, folio=None, pms_property=None):
        folio = folio or self.env["pms.folio"]
        return self.sudo().create(
            {
                "user_id": self.env.uid,
                "folio_id": folio.id,
                "pms_property_id": (pms_property or folio.pms_property_id).id,
                "action": action,
                "result": result,
                "remote_addr": self.env.context.get("payment_card_remote_addr"),
            }
        )
