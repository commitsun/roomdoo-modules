from odoo import api, fields, models


class PmsFastapiMfaChallenge(models.Model):
    """A login that passed the password and still owes its second factor.

    It is the API's equivalent of the pre-authenticated session the web login
    keeps: on its own it grants nothing, it only says which user is allowed to
    hand in a code. Attempts are counted here so that a wrong code costs the
    caller its challenge instead of its account.
    """

    _name = "pms.fastapi.mfa.challenge"
    _description = "Pending second factor"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade"
    )
    token = fields.Char(required=True, index=True)
    expire = fields.Datetime(required=True)
    attempts = fields.Integer(default=0)

    _sql_constraints = [
        ("unique_token", "unique(token)", "The token must be unique!"),
    ]

    @api.autovacuum
    def _remove_expired_challenges(self):
        records = self.search([("expire", "<", fields.Datetime.now())])
        return records.unlink()
