from odoo import api, fields, models


class PmsFastapiMfaSetup(models.Model):
    """A second factor a user has been offered but has not confirmed yet.

    The secret is kept here rather than handed to the client and taken back on
    trust, so that what ends up protecting the account is what the server
    offered and nothing else.
    """

    _name = "pms.fastapi.mfa.setup"
    _description = "Second factor pending confirmation"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade"
    )
    secret = fields.Char(required=True)
    expire = fields.Datetime(required=True)

    _sql_constraints = [
        ("unique_user", "unique(user_id)", "Only one setup at a time per user!"),
    ]

    @api.autovacuum
    def _remove_expired_setups(self):
        records = self.search([("expire", "<", fields.Datetime.now())])
        return records.unlink()
