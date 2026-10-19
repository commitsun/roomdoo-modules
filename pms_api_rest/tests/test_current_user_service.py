# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""``/users/current`` exists so a client that authenticates elsewhere can still
obtain the payload the login used to carry.

It answers for whoever is authenticated, never for a user id taken from the
request, and it hands out no credential: that is the whole reason it is a
separate endpoint from ``/users/<id>``.
"""
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms

IMAGE_URL_BUILDER = (
    "odoo.addons.pms_api_rest.services.pms_user_service.url_image_pms_api_rest"
)


@tagged("post_install", "-at_install")
class TestCurrentUserService(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api_user = cls.env["res.users"].create(
            {
                "name": "Api Caller",
                "login": "api.caller@example.org",
                "company_id": cls.company1.id,
                "company_ids": [(6, 0, [cls.company1.id])],
                "pms_property_ids": [(6, 0, [cls.pms_property1.id])],
                "pms_property_id": cls.pms_property1.id,
                "pms_api_user_role": "manager",
            }
        )

    def _current_user(self, user):
        collection = _PseudoCollection("pms.services", self.env(user=user))
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        # The image URL is built from the live HTTP request, which a unit test
        # has no way to provide. It is shared with the login and out of scope
        # here.
        with patch(IMAGE_URL_BUILDER, return_value=""):
            return work.component(usage="users").get_current_user()

    def test_answers_for_the_authenticated_user(self):
        result = self._current_user(self.api_user)

        self.assertEqual(result.userId, self.api_user.id)
        self.assertEqual(result.userName, self.api_user.name)

    def test_hands_out_no_credential(self):
        result = self._current_user(self.api_user)

        self.assertFalse(getattr(result, "token", None))
        self.assertFalse(getattr(result, "expirationDate", None))

    def test_carries_what_only_the_login_used_to_carry(self):
        result = self._current_user(self.api_user)

        self.assertEqual(result.defaultPropertyId, self.pms_property1.id)
        self.assertEqual(result.defaultPropertyName, self.pms_property1.name)
        self.assertEqual(result.userRole, "manager")
