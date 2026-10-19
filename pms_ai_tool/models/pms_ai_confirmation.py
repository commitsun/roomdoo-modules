from datetime import timedelta
from hashlib import sha256

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import mute_logger


class PmsAiConfirmation(models.Model):
    """Confirmation tokens already spent, so that each is used only once."""

    _name = "pms.ai.confirmation"
    _description = "Spent confirmation of an AI tool"
    _order = "id desc"

    token_hash = fields.Char(required=True, readonly=True)
    tool = fields.Char(readonly=True)
    user_id = fields.Many2one("res.users", readonly=True)

    _sql_constraints = [
        (
            "token_hash_uniq",
            "unique(token_hash)",
            "This confirmation was already used.",
        ),
    ]

    @api.model
    def _consume(self, token, tool):
        try:
            with self.env.cr.savepoint(), mute_logger("odoo.sql_db"):
                self.sudo().create(
                    {
                        "token_hash": sha256(token.encode()).hexdigest(),
                        "tool": tool,
                        "user_id": self.env.uid,
                    }
                )
        except psycopg2.IntegrityError:
            raise UserError(
                _(
                    "This confirmation was already used: preview the change "
                    "again if it is still needed."
                )
            ) from None

    @api.model
    def _purge(self):
        """Spent tokens only matter while they are valid (minutes)."""
        limit = fields.Datetime.now() - timedelta(days=2)
        self.sudo().search([("create_date", "<", limit)]).unlink()
