import base64
import logging
import os
import tempfile
from datetime import datetime, timedelta

import werkzeug.exceptions

from odoo import _
from odoo.exceptions import AccessDenied, AccessError, MissingError

from odoo.addons.base_rest import restapi
from odoo.addons.base_rest_datamodel.restapi import Datamodel
from odoo.addons.component.core import Component

from ..pms_api_rest_utils import url_image_pms_api_rest

_logger = logging.getLogger(__name__)


class PmsRoomTypeClassService(Component):
    _inherit = "base.rest.service"
    _name = "pms.user.service"
    _usage = "users"
    _collection = "pms.services"

    @restapi.method(
        [
            (
                [
                    "/current",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.api.rest.user.output", is_list=False),
        auth="jwt_api_pms",
    )
    def get_current_user(self):
        """Profile of the authenticated user, without any credential.

        Same payload as the login response minus the token and its expiry, so a
        client that authenticates elsewhere can still obtain the data the login
        used to carry along with it.
        """
        user = self.env.user.sudo()
        PmsApiRestUserOutput = self.env.datamodels["pms.api.rest.user.output"]
        return PmsApiRestUserOutput(
            userId=user.id,
            userName=user.name,
            userFirstName=user.firstname or None,
            userEmail=user.email or None,
            userPhone=user.phone or None,
            defaultPropertyId=user.pms_property_id.id,
            defaultPropertyName=user.pms_property_id.name,
            userImageBase64=user.partner_id.image_1024
            if user.partner_id.image_1024
            else None,
            userImageUrl=url_image_pms_api_rest(
                "res.partner", user.partner_id.id, "image_1024"
            ),
            availabilityRuleFields=user.availability_rule_field_ids.mapped("name"),
            userRole=user.pms_api_user_role or None,
        )

    @restapi.method(
        [
            (
                [
                    "/<int:user_id>",
                ],
                "GET",
            )
        ],
        output_param=Datamodel("pms.api.rest.user.output", is_list=False),
        auth="jwt_api_pms",
    )
    def get_user(self, user_id):
        # Share users (portal, public) only read their own user; internal users
        # read any user, as the front lists colleagues. Checked before the
        # lookup, so a missing id gets the same answer.
        if user_id != self.env.uid and not self.env.user._is_internal():
            raise AccessError(_("You can only view your own user."))
        user = self.env["res.users"].sudo().browse(user_id)
        if not user.exists():
            raise MissingError(_("User not found"))
        PmsUserInfo = self.env.datamodels["pms.api.rest.user.output"]
        return PmsUserInfo(
            userId=user.id,
            userName=user.name,
            userFirstName=user.firstname if user.firstname else "",
            userEmail=user.email if user.email else "",
            userPhone=user.phone if user.phone else "",
            userImageBase64=user.image_1920 if user.image_1920 else "",
            userImageUrl=url_image_pms_api_rest(
                "res.partner", user.partner_id.id, "image_1024"
            ),
        )

    @restapi.method(
        [
            (
                [
                    "/p/<int:user_id>",
                ],
                "PATCH",
            )
        ],
        input_param=Datamodel("pms.api.rest.user.output", is_list=False),
        auth="jwt_api_pms",
    )
    def write_user(self, user_id, input_data):
        # Only the user itself or an access rights manager edits a user.
        # Checked before the lookup, so a missing id gets the same answer.
        if user_id != self.env.uid and not self.env.user.has_group(
            "base.group_erp_manager"
        ):
            raise AccessError(_("You can only edit your own user."))
        user = self.env["res.users"].sudo().browse(user_id)
        if not user.exists():
            raise MissingError(_("User not found"))
        user.write(
            {
                "name": input_data.userName,
                "email": input_data.userEmail,
                "phone": input_data.userPhone,
            }
        )
        if input_data.userImageBase64 is not None:
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(base64.b64decode(input_data.userImageBase64))
                temp_path = f.name

            with open(temp_path, "rb") as f:
                user_image = f.read()
            os.unlink(temp_path)

            user.write(
                {
                    "image_1024": base64.b64encode(user_image),
                }
            )
        else:
            user.write(
                {
                    "image_1024": "",
                }
            )
        return True

    @restapi.method(
        [
            (
                [
                    "/p/<int:user_id>/change-password",
                ],
                "PATCH",
            )
        ],
        output_param=Datamodel("pms.api.rest.user.login.output", is_list=False),
        input_param=Datamodel("pms.api.rest.user.input", is_list=False),
        auth="jwt_api_pms",
    )
    def change_password(self, user_id, input_data):
        # Only the user itself changes its password: res.users.change_password
        # always applies to the current user anyway.
        if user_id != self.env.uid:
            raise AccessError(_("You can only change your own password."))
        user = self.env["res.users"].sudo().browse(user_id)
        if not user.exists():
            raise MissingError(_("User not found"))
        try:
            user.with_user(user)._check_credentials(input_data.password, None)
        except AccessDenied as e:
            raise werkzeug.exceptions.Unauthorized(_("Wrong password")) from e

        user.change_password(input_data.password, input_data.newPassword)

        PmsUserInfo = self.env.datamodels["pms.api.rest.user.login.output"]
        return PmsUserInfo(
            login=user.login,
        )

    @restapi.method(
        [
            (
                [
                    "/p/reset-password",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.api.rest.user.input", is_list=False),
        auth="public",
        cors="*",
    )
    def reset_password(self, input_data):
        values = {
            "password": input_data.password,
        }
        self.env["res.users"].sudo().signup(values, input_data.resetToken)
        return True

    @restapi.method(
        [
            (
                [
                    "/send-mail-reset-password",
                ],
                "POST",
            )
        ],
        input_param=Datamodel("pms.api.rest.user.input", is_list=False),
        auth="public",
        cors="*",
    )
    def send_mail_to_reset_password(self, input_data):
        user = (
            self.env["res.users"].sudo().search([("email", "=", input_data.userEmail)])
        )
        if user:
            template_id = self.env.ref("pms_api_rest.pms_reset_password_email").id
            template = self.env["mail.template"].sudo().browse(template_id)
            if not template:
                return False
            app_url = self._get_app_url()
            if not app_url:
                return False
            expiration_datetime = datetime.now() + timedelta(minutes=15)
            user.partner_id.sudo().signup_prepare(expiration=expiration_datetime)
            template.with_context(app_url=app_url).send_mail(user.id, force_send=True)
            return True
        return False

    def _get_app_url(self):
        """Origin of this instance's front, where the reset link points.

        The link is built from ``roomdoo_app_url`` only; the ``url`` field of
        the request is not used. The parameter ships as the literal ``False``
        until the install hook fills it in, and the backend serves no page at
        that path, so without an http(s) URL no mail is sent at all.
        """
        app_url = self.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
        app_url = (app_url or "").strip().rstrip("/")
        if app_url.lower().startswith(("http://", "https://")):
            return app_url
        _logger.warning(
            "Password reset mail not sent: roomdoo_app_url is not an http(s) URL"
        )
        return None

    @restapi.method(
        [
            (
                [
                    "/check-reset-password-token/<string:reset_token>",
                ],
                "GET",
            )
        ],
        auth="public",
        cors="*",
    )
    def check_reset_password_token(self, reset_token):
        user = (
            self.env["res.partner"].sudo().search([("signup_token", "=", reset_token)])
        )
        is_token_expired = False
        if not user:
            return True
        if user.sudo().signup_expiration < datetime.now():
            is_token_expired = True
        return is_token_expired
