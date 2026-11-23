from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessDenied

# Seconds a refresh token stays usable once it has been exchanged.
REFRESH_TOKEN_REUSE_INTERVAL = 60


class ResUsers(models.Model):
    _inherit = "res.users"

    fastapi_refresh_token_ids = fields.One2many("fastapi.user.refresh.token", "user_id")

    feature_flag_ids = fields.Many2many(
        "feature.flag",
        "feature_flag_res_users_rel",
        "user_id",
        "flag_id",
        string="Feature Flags",
    )

    def _add_refresh_token(self, token, expire):
        self.ensure_one()
        expire_datetime = fields.Datetime.now() + timedelta(seconds=expire)
        self.env["fastapi.user.refresh.token"].sudo().create(
            {"user_id": self.id, "token": token, "expire": expire_datetime}
        )

    @api.model
    def invalidate_refresh_token(self, token):
        self.env["fastapi.user.refresh.token"].sudo().search(
            [("token", "=", token)], limit=1
        ).unlink()

    @api.model
    def retire_refresh_token(self, token):
        # Every tab of the client shares the cookie and renews it on its own, so
        # two of them can present the same token at once. Dropping it on first
        # use logs out all but the fastest one: it lives on for a short while
        # instead, never longer than it already had.
        limit = fields.Datetime.now() + timedelta(seconds=REFRESH_TOKEN_REUSE_INTERVAL)
        self.env["fastapi.user.refresh.token"].sudo().search(
            [("token", "=", token), ("expire", ">", limit)], limit=1
        ).write({"expire": limit})

    def invalidate_all_refresh_tokens(self):
        self.ensure_one()
        self.sudo().fastapi_refresh_token_ids.unlink()

    @api.model
    def get_user_by_refresh_token(self, token):
        token_exists = (
            self.env["fastapi.user.refresh.token"]
            .sudo()
            .search(
                [("token", "=", token), ("expire", ">", fields.Datetime.now())], limit=1
            )
        )
        if token_exists:
            return token_exists.user_id
        else:
            raise AccessDenied()


class FastapiUserRefreshToken(models.Model):
    _name = "fastapi.user.refresh.token"
    _description = "Fastapi refresh tokens"

    user_id = fields.Many2one("res.users")
    token = fields.Char()
    expire = fields.Datetime()

    _sql_constraints = [
        (
            "unique_token",
            "unique(token)",
            "The token must be unique!",
        ),
    ]

    @api.autovacuum
    def _remove_expired_tokens(self):
        domain = [
            ("expire", "<", fields.Datetime.now()),
        ]
        records = self.search(domain, limit=models.GC_UNLINK_LIMIT)
        if len(records) >= models.GC_UNLINK_LIMIT:
            self.env.ref("base.autovacuum_job")._trigger()
        return records.unlink()
