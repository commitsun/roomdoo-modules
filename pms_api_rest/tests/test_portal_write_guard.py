# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""The pricing and restriction endpoints write ``product.pricelist.item`` and
``pms.availability.plan.rule`` with ``sudo()``, so neither ``ir.model.access``
nor the record rules ever run: any account holding a ``jwt_api_pms`` token
could change real selling prices and restrictions, portal accounts included.

Writes must stay with internal users, except for the integration clients,
which are portal accounts by construction.
"""

from odoo.exceptions import AccessDenied
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPortalWriteGuard(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_user = cls.env["res.users"].create(
            {
                "name": "Guard Internal",
                "login": "guard_internal",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            }
        )
        cls.portal_user = cls.env["res.users"].create(
            {
                "name": "Guard Portal",
                "login": "guard_portal",
                "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
            }
        )
        # The channel manager and the other integrations authenticate as
        # portal accounts, so the guard must let them through.
        cls.api_client_user = cls.env["res.users"].create(
            {
                "name": "Guard Api Client",
                "login": "guard_api_client",
                "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
                "pms_api_client": True,
            }
        )

    def _service(self, usage, user):
        collection = _PseudoCollection("pms.services", self.env(user=user))
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage=usage)

    def _write_prices(self, user):
        """Run the pricing write path with an empty payload.

        The guard runs before anything else, so an empty payload tells the
        two cases apart: refused accounts raise, allowed ones no-op.
        """
        service = self._service("pricelists", user)
        service._create_or_update_pricelist_items(
            self.env.datamodels["pms.pricelist.items.info"](pricelistItems=[])
        )

    def _write_rules(self, user):
        service = self._service("availability-plans", user)
        service._create_or_update_avail_plan_rules(
            self.env.datamodels["pms.availability.plan.rules.info"](
                availabilityPlanRules=[]
            )
        )

    def test_portal_user_cannot_write_prices(self):
        with self.assertRaises(AccessDenied):
            self._write_prices(self.portal_user)

    def test_portal_user_cannot_write_restrictions(self):
        with self.assertRaises(AccessDenied):
            self._write_rules(self.portal_user)

    def test_internal_user_can_write_prices(self):
        self._write_prices(self.internal_user)

    def test_internal_user_can_write_restrictions(self):
        self._write_rules(self.internal_user)

    def test_api_client_can_write_prices(self):
        self._write_prices(self.api_client_user)

    def test_api_client_can_write_restrictions(self):
        self._write_rules(self.api_client_user)
