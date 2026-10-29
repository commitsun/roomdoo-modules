from odoo import fields, models

from odoo.addons.base.models.res_users import INDEX_SIZE, KEY_CRYPT_CONTEXT


class ResUsers(models.Model):
    _inherit = "res.users"

    pms_api_user_role = fields.Selection(
        help="PMS API User Role",
        selection=[
            ("receptionist", "Receptionist"),
            ("manager", "Manager"),
            ("revenue", "Revenue"),
            ("administration", "Administration"),
        ],
        default="receptionist",
    )
    availability_rule_field_ids = fields.Many2many(
        string="Availability Rules",
        help="Configurable availability rules",
        comodel_name="ir.model.fields",
        default=lambda self: self._get_default_avail_rule_fields(),
        relation="ir_model_fields_res_users_rel",
        column1="ir_model_fields",
        column2="res_users",
    )
    pms_api_client = fields.Boolean(
        help="PMS API Client",
    )
    url_endpoint_prices = fields.Char(
        help="URL Endpoint Prices",
    )
    url_endpoint_availability = fields.Char(
        help="URL Endpoint Availability",
    )
    url_endpoint_rules = fields.Char(
        help="URL Endpoint Rules",
    )
    external_public_token = fields.Char(
        help="External Public Token",
    )

    def _get_default_avail_rule_fields(self):
        default_avail_rule_fields = self.env["ir.model.fields"].search(
            [
                ("model_id", "=", "pms.availability.plan.rule"),
                ("name", "in", ("min_stay", "quota")),
            ]
        )
        if default_avail_rule_fields:
            return default_avail_rule_fields.ids
        else:
            return []


class OneTimeAPIKeys(models.Model):
    """Keys that open a session for their owner without a password.

    A model of their own, as :class:`auth_totp.device` is, so that a key meant
    to walk past the second factor on one route is never a credential anywhere
    else. Only the controller that hands these links out accepts them, and it
    asks for them by scope and by owner.

    They are spent on arrival, but until then each one stands in for both
    factors.
    """

    _name = "one.time.res.users.apikeys"
    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        assert scope, "scope is required"
        index = key[:INDEX_SIZE]
        self.env.cr.execute(
            f"""
            SELECT k.id as id, user_id, key
            FROM {self._table} k INNER JOIN res_users u ON (u.id = user_id)
            WHERE u.active and index = %s AND (scope IS NULL OR scope = %s)
        """,
            [index, scope],
        )
        for id, user_id, current_key in self.env.cr.fetchall():
            if KEY_CRYPT_CONTEXT.verify(key, current_key):
                self.sudo().browse(id).unlink()
                return user_id

    def _check_credentials_for_uid(self, *, scope, key, uid):
        """Return True when the key is this user's, as `auth_totp.device` does."""
        assert uid, "uid is required"
        return self._check_credentials(scope=scope, key=key) == uid
