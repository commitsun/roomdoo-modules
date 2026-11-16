import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import str2bool

from odoo.addons.base.models.res_users import INDEX_SIZE, KEY_CRYPT_CONTEXT

_logger = logging.getLogger(__name__)

ONE_TIME_KEY_MAX_AGE = 2 * 60

# While this parameter is off, a refused user is only logged and still let in.
API_ACCESS_ENFORCE_PARAM = "pms_api_rest.api_access_enforce"


class ResUsers(models.Model):
    _inherit = "res.users"

    # No default on purpose: a portal user needs a role to use the PMS APIs, so
    # anyone who signs up on their own must not be handed one.
    pms_api_user_role = fields.Selection(
        help="Role of the user in the PMS. A portal user without a role cannot "
        "use the PMS APIs, unless it is a PMS API client.",
        selection=[
            ("receptionist", "Receptionist"),
            ("manager", "Manager"),
            ("revenue", "Revenue"),
            ("administration", "Administration"),
        ],
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

    def _pms_api_access_allowed(self):
        """Whether this user is meant to use the PMS APIs at all.

        Internal users always are. A portal user only when it works at a
        property, which until there are proper roles is told by having a PMS
        role, or when it is the account of an API client (channel managers,
        pre-check-in apps, booking engines...). Any other portal user, such as
        a customer who signed up on a website, has no business there.
        """
        self.ensure_one()
        return not self.share or bool(self.pms_api_user_role) or self.pms_api_client

    def _pms_api_check_access(self):
        """Return False when this user has to be turned away from the PMS APIs.

        The refusal is logged either way. Until the enforce parameter is on it
        is only logged, so that the accounts that still need a role can be
        found before anybody is locked out.
        """
        self.ensure_one()
        user = self.sudo()
        if user._pms_api_access_allowed():
            return True
        enforce = str2bool(
            user.env["ir.config_parameter"].get_param(API_ACCESS_ENFORCE_PARAM, "0"),
            default=False,
        )
        _logger.warning(
            "PMS API access %s for portal user %s (id %s): no PMS role and not "
            "an API client",
            "refused" if enforce else "would be refused",
            user.login,
            user.id,
        )
        return not enforce

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
    factors, so the shorter they are alive the less there is to lose. The link
    they travel in is asked for at the moment it is followed, so seconds are
    enough and this is already generous.
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
            AND k.create_date > (NOW() AT TIME ZONE 'UTC'
                                 - INTERVAL '%s SECONDS')
        """,
            [index, scope, ONE_TIME_KEY_MAX_AGE],
        )
        for id, user_id, current_key in self.env.cr.fetchall():
            if KEY_CRYPT_CONTEXT.verify(key, current_key):
                self.sudo().browse(id).unlink()
                return user_id

    def _check_credentials_for_uid(self, *, scope, key, uid):
        """Return True when the key is this user's, as `auth_totp.device` does."""
        assert uid, "uid is required"
        return self._check_credentials(scope=scope, key=key) == uid

    @api.autovacuum
    def _gc_spent_keys(self):
        """What the check above already refuses, dropped so it stops piling up."""
        expiry = fields.Datetime.now() - timedelta(seconds=ONE_TIME_KEY_MAX_AGE)
        self.sudo().search([("create_date", "<", expiry)]).unlink()
