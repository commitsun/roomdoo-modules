from odoo import fields, models


class AuthJwtValidator(models.Model):
    _inherit = "auth.jwt.validator"

    refresh_cookie_name = fields.Char(default="refresh")
    refresh_cookie_max_age = fields.Integer(default=86400)
    refresh_token_secret = fields.Char(default="pms_secret_key_refresh")
    refresh_token_path = fields.Char(default="/pmsApi/refresh-token")
