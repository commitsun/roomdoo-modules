from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_ENVIRONMENT, PARAM_WRITE_ENABLED


@tagged("post_install", "-at_install")
class TestPartnerAssignSalesperson(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param(PARAM_ENVIRONMENT, "test")
        params.set_param(PARAM_WRITE_ENABLED, "True")
        cls.manager = new_test_user(
            cls.env, login="ai_assign_manager", groups="base.group_user"
        )
        cls.stranger = new_test_user(
            cls.env, login="ai_assign_stranger", groups="base.group_user"
        )
        cls.sp_old = new_test_user(cls.env, login="ai_sp_old", name="Old salesperson")
        cls.sp_new = new_test_user(cls.env, login="ai_sp_new", name="New salesperson")
        cls.portal = new_test_user(
            cls.env, login="ai_assign_portal", groups="base.group_portal"
        )
        cls.gone = new_test_user(cls.env, login="ai_assign_gone")
        cls.gone.active = False
        # As the pms tests do: every pricelist usable by the PMS (agencies get
        # the default one).
        plan = cls.env["pms.availability.plan"].create({"name": "AI plan"})
        for pricelist in cls.env["product.pricelist"].search([]):
            if not pricelist.availability_plan_id:
                pricelist.write({"availability_plan_id": plan.id})
            pricelist.is_pms_available = True
        channel = cls.env["pms.sale.channel"].create(
            {"name": "AI agencies", "channel_type": "indirect"}
        )
        Partner = cls.env["res.partner"]
        cls.company1 = Partner.create(
            {"name": "AI Company One", "is_company": True, "user_id": cls.sp_old.id}
        )
        cls.company2 = Partner.create({"name": "AI Company Two", "is_company": True})
        cls.agency = Partner.create(
            {
                "name": "AI Agency",
                "is_company": True,
                "is_agency": True,
                "sale_channel_id": channel.id,
                "user_id": cls.sp_old.id,
            }
        )
        cls.person = Partner.create({"name": "AI Guest Person"})
        cls.archived = Partner.create(
            {"name": "AI Archived Company", "is_company": True, "active": False}
        )
        cls.other_company = cls.env["res.company"].create({"name": "AI other company"})
        cls.foreign = Partner.create(
            {
                "name": "AI Foreign Company",
                "is_company": True,
                "company_id": cls.other_company.id,
            }
        )
        cls.tool = cls.env.ref(
            "pms_ai_tool_revenue.ai_tool_pms_partner_assign_salesperson"
        )
        cls.tool.write(
            {"allowed_user_ids": [(6, 0, cls.manager.ids)], "write_enabled": True}
        )
        cls.env.invalidate_all()

    def _run(self, partners, salesperson, user=None, **kwargs):
        return self.tool.with_user(user or self.manager)._execute_tool(
            partner_ids=partners.ids, salesperson_user_id=salesperson, **kwargs
        )

    def _preview(self, partners, salesperson, user=None):
        return self._run(partners, salesperson, user)

    def _apply(self, partners, salesperson, user=None):
        token = self._preview(partners, salesperson, user)["confirmation"][
            "confirmation_token"
        ]
        return self._run(
            partners, salesperson, user, confirm=True, confirmation_token=token
        )

    def _salesperson(self, partner):
        partner.invalidate_recordset(["user_id"])
        return partner.user_id

    def test_preview_lists_the_current_salesperson(self):
        partners = self.company1 | self.company2 | self.agency
        response = self._preview(partners, self.sp_new.id)
        result = response["result"]
        self.assertEqual(
            result["salesperson"], {"id": self.sp_new.id, "name": "New salesperson"}
        )
        self.assertEqual((result["to_change"], result["unchanged"]), (3, 0))
        rows = {row["partner_id"]: row for row in result["partners"]}
        self.assertEqual(
            rows[self.company1.id]["current_salesperson_id"], self.sp_old.id
        )
        self.assertEqual(
            rows[self.company1.id]["current_salesperson"], "Old salesperson"
        )
        self.assertIsNone(rows[self.company2.id]["current_salesperson_id"])
        self.assertEqual(rows[self.company1.id]["kind"], "company")
        self.assertEqual(rows[self.agency.id]["kind"], "agency")
        self.assertTrue(all(row["will_change"] for row in rows.values()))
        self.assertTrue(response["confirmation"]["required"])
        self.assertTrue(response["confirmation"]["confirmation_token"])
        self.assertIn("3 partners will get New salesperson", response["summary"])
        self.assertEqual(response["environment"], "test")
        self.assertEqual(response["tool"], "pms_partner_assign_salesperson")
        self.assertTrue(response["definitions"])
        # A preview changes nothing.
        self.assertEqual(self._salesperson(self.company1), self.sp_old)
        self.assertFalse(self._salesperson(self.company2))

    def test_confirmed_change_is_applied_and_can_be_undone(self):
        partners = self.company1 | self.company2 | self.agency
        response = self._apply(partners, self.sp_new.id)
        self.assertEqual(
            sorted(response["result"]["changed_partner_ids"]), sorted(partners.ids)
        )
        for partner in partners:
            self.assertEqual(self._salesperson(partner), self.sp_new)
        # Undo with the previous salesperson shown in the preview.
        self._apply(self.company1 | self.agency, self.sp_old.id)
        self.assertEqual(self._salesperson(self.company1), self.sp_old)
        self.assertEqual(self._salesperson(self.agency), self.sp_old)
        self.assertEqual(self._salesperson(self.company2), self.sp_new)
        # And 0 removes the salesperson.
        preview = self._preview(self.company2, 0)
        self.assertIsNone(preview["result"]["salesperson"])
        self._apply(self.company2, 0)
        self.assertFalse(self._salesperson(self.company2))

    def test_a_wrong_token_aborts_the_change(self):
        token = self._preview(self.company1, self.sp_new.id)["confirmation"][
            "confirmation_token"
        ]
        attempts = [
            (self.company1 | self.company2, self.sp_new.id, token),
            (self.company1, self.sp_old.id, token),
            (self.company1, self.sp_new.id, "garbage"),
            (self.company1, self.sp_new.id, None),
        ]
        for partners, salesperson, bad_token in attempts:
            with self.subTest(partners=partners.ids, token=bad_token):
                with self.assertRaises(UserError):
                    self._run(
                        partners,
                        salesperson,
                        confirm=True,
                        confirmation_token=bad_token,
                    )
        self.assertEqual(self._salesperson(self.company1), self.sp_old)
        self.assertFalse(self._salesperson(self.company2))
        # The change is bound to what the preview saw.
        self.company1.sudo().user_id = self.stranger
        self.env.flush_all()
        with self.assertRaisesRegex(UserError, "does not match"):
            self._run(
                self.company1,
                self.sp_new.id,
                confirm=True,
                confirmation_token=token,
            )

    def test_individuals_and_archived_are_refused(self):
        with self.assertRaises(UserError) as error:
            self._preview(self.company1 | self.person | self.archived, self.sp_new.id)
        message = str(error.exception)
        self.assertIn(f"{self.person.id} (individual)", message)
        self.assertIn(f"{self.archived.id} (archived)", message)
        # No names of people in the message.
        self.assertNotIn("Guest Person", message)
        self.assertEqual(self._salesperson(self.company1), self.sp_old)

    def test_partners_the_user_cannot_read_or_that_do_not_exist(self):
        with self.assertRaises(AccessError):
            self._preview(self.company1 | self.foreign, self.sp_new.id)
        with self.assertRaisesRegex(UserError, "Unknown partners"):
            self._run(self.company1.browse([999999999]), self.sp_new.id)

    def test_the_salesperson_must_be_an_active_internal_user(self):
        for salesperson in (self.portal.id, self.gone.id, 999999999):
            with self.subTest(salesperson=salesperson), self.assertRaises(UserError):
                self._preview(self.company2, salesperson)

    def test_partners_already_assigned_are_left_alone(self):
        response = self._preview(self.company1 | self.company2, self.sp_old.id)
        self.assertEqual(response["result"]["to_change"], 1)
        self.assertEqual(response["result"]["unchanged"], 1)
        self.assertTrue(
            any(
                "1 partners already have this salesperson" in w
                for w in response["warnings"]
            )
        )
        self._apply(self.company1 | self.company2, self.sp_old.id)
        self.assertEqual(self._salesperson(self.company2), self.sp_old)
        with self.assertRaisesRegex(UserError, "Nothing to change"):
            self._preview(self.company1 | self.company2, self.sp_old.id)

    def test_only_the_allowed_users_can_use_it(self):
        with self.assertRaises(AccessError):
            self._preview(self.company1, self.sp_new.id, user=self.stranger)
        self.tool.allowed_user_ids = False
        with self.assertRaises(AccessError):
            self._preview(self.company1, self.sp_new.id)

    def test_arguments_are_validated(self):
        tool = self.tool.with_user(self.manager)
        many = self.env["res.partner"].browse(range(1, 52))
        bad_calls = [
            {"partner_ids": [], "salesperson_user_id": self.sp_new.id},
            {"partner_ids": many.ids, "salesperson_user_id": self.sp_new.id},
            {"partner_ids": ["1"], "salesperson_user_id": self.sp_new.id},
            {"partner_ids": self.company1.ids, "salesperson_user_id": -1},
            {"partner_ids": self.company1.ids},
            {
                "partner_ids": self.company1.ids,
                "salesperson_user_id": 1,
                "domain": "[]",
            },
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError):
                tool._execute_tool(**kwargs)
