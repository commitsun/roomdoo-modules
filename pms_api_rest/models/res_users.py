from odoo import fields, models
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT, INDEX_SIZE
from datetime import timedelta
from odoo.exceptions import AccessDenied
import secrets

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
    portal_login_token = fields.Char(
        help="Portal Login Token",
    )
    portal_login_token_expiration = fields.Datetime(
        help="Portal Login Token Expiration",
    )

    def _generate_portal_login_token(self, expiration=None):
        """Generate a new portal login token."""
        self.portal_login_token = secrets.token_hex()
        if expiration:
            self.portal_login_token_expiration = expiration
        else:
            self.portal_login_token_expiration = fields.Datetime.now() + timedelta(
                days=1
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

    def _check_credentials(self, password, env):
        try:
            res = super()._check_credentials(password, env)
            return res
        except AccessDenied as e:
            if not self.env['one.time.res.users.apikeys']._check_credentials(scope='ots', key=password) == self.env.uid:
                raise AccessDenied() from e

class OneTimeAPIKeys(models.Model):
    _name = 'one.time.res.users.apikeys'
    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        assert scope, "scope is required"
        index = key[:INDEX_SIZE]
        self.env.cr.execute('''
            SELECT k.id as id, user_id, key
            FROM {} k INNER JOIN res_users u ON (u.id = user_id)
            WHERE u.active and index = %s AND (scope IS NULL OR scope = %s)
        '''.format(self._table),
        [index, scope])
        for id, user_id, current_key in self.env.cr.fetchall():
            if KEY_CRYPT_CONTEXT.verify(key, current_key):
                self.sudo().browse(id).unlink()
                return user_id
