import json
from datetime import date
from unittest import mock

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.pms_ai_tool.tools import get_guard

FINDERS = "odoo.addons.pms_ai_tool_common.models.pms_ai_finders"
TOOL_NAMES = [
    "pms_my_scope",
    "pms_property_find",
    "pms_folio_find",
    "pms_partner_find",
    "pms_user_find",
    "pms_product_find",
]


@tagged("post_install", "-at_install")
class TestFinders(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        plan = cls.env["pms.availability.plan"].create({"name": "AI find plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI find pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        # As the pms tests do: every pricelist usable by the PMS (agencies get
        # the default one).
        for other in cls.env["product.pricelist"].search([]):
            if not other.availability_plan_id:
                other.write({"availability_plan_id": plan.id, "is_pms_available": True})
        Property = cls.env["pms.property"]

        def prop(name, code, city):
            return Property.create(
                {
                    "name": name,
                    "pms_property_code": code,
                    "city": city,
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
            )

        cls.prop_a = prop("AI Find Hotel Ponferrada", "AIP", "Ponferrada")
        cls.prop_b = prop("AI Find Hotel Santiago", "AIS", "Santiago")
        cls.prop_c = prop("AI Find Hotel Hidden", "AIH", "Vigo")
        cls.user = new_test_user(
            cls.env,
            login="ai_find_user",
            name="AI Find User",
            groups="base.group_user,pms.group_pms_user",
            pms_property_ids=[(6, 0, (cls.prop_a | cls.prop_b).ids)],
            pms_property_id=cls.prop_a.id,
        )
        cls.tools = {
            name: cls.env.ref(f"pms_ai_tool_common.ai_tool_{name}")
            for name in TOOL_NAMES
        }

    def _run(self, name, user=None, **kwargs):
        tool = self.tools[name].with_user(user or self.user)
        return tool._execute_tool(**kwargs)

    # Declaration

    def test_finders_are_read_tools_and_hide_personal_data(self):
        tools = self.env["pms.ai.tools"]
        for name in TOOL_NAMES:
            guard = get_guard(getattr(tools, f"_ai_{name}"))
            self.assertFalse(guard["write"], name)
        # The folio finder returns guests: keep them out of the MCP log.
        self.assertFalse(get_guard(tools._ai_pms_folio_find)["log_result"])

    def test_arguments_are_validated(self):
        bad_calls = [
            ("pms_property_find", {"text": "x", "domain": "[]"}),
            ("pms_property_find", {}),
            ("pms_property_find", {"text": "x" * 101}),
            ("pms_property_find", {"text": 5}),
            ("pms_product_find", {"text": "x", "kind": "everything"}),
            ("pms_folio_find", {"text": "x", "arrival_date": "12/11/2026"}),
        ]
        for name, kwargs in bad_calls:
            with self.subTest(name=name, kwargs=kwargs), self.assertRaises(UserError):
                self._run(name, **kwargs)
        for name in TOOL_NAMES[1:]:
            with self.subTest(name=name), self.assertRaisesRegex(UserError, "empty"):
                self._run(name, text="   ")

    def test_user_without_pms_access_is_refused(self):
        stranger = new_test_user(
            self.env, login="ai_find_stranger", groups="base.group_user"
        )
        with self.assertRaises(AccessError):
            self._run("pms_property_find", stranger, text="Hotel")

    # Scope and properties

    def test_my_scope_lists_only_my_properties_and_companies(self):
        response = self._run("pms_my_scope")
        result = response["result"]
        self.assertEqual(
            {prop["id"] for prop in result["properties"]},
            {self.prop_a.id, self.prop_b.id},
        )
        row = next(p for p in result["properties"] if p["id"] == self.prop_a.id)
        self.assertEqual(row["name"], "AI Find Hotel Ponferrada")
        self.assertEqual(row["code"], "AIP")
        self.assertEqual(row["city"], "Ponferrada")
        self.assertEqual(row["company_id"], self.env.company.id)
        self.assertEqual(
            result["companies"],
            [{"id": self.env.company.id, "name": self.env.company.name}],
        )
        self.assertIn("glossary", " ".join(response["definitions"]))

    def test_property_find_by_name_code_and_city(self):
        for text, expected in (
            ("ponferrada", self.prop_a),
            ("PONFER", self.prop_a),
            ("AIS", self.prop_b),
            ("santiago", self.prop_b),
            ("hotel ponferrada", self.prop_a),
        ):
            with self.subTest(text=text):
                found = self._run("pms_property_find", text=text)["result"]
                self.assertEqual([p["id"] for p in found["properties"]], expected.ids)

    def test_property_find_hides_other_properties_and_ignores_wildcards(self):
        for text in ("Hidden", "AIH", "Vigo", "%", "_", "A%"):
            with self.subTest(text=text):
                found = self._run("pms_property_find", text=text)["result"]
                self.assertEqual(found["properties"], [])

    def test_results_are_cut_and_the_cut_is_reported(self):
        with mock.patch(f"{FINDERS}.MAX_RESULTS", 1):
            response = self._run("pms_property_find", text="AI Find Hotel")
        self.assertEqual(len(response["result"]["properties"]), 1)
        self.assertIn("Only the first 1", response["warnings"][0])

    # Folios

    @classmethod
    def _folio(cls, prop, partner, code, locator, arrival):
        # Every property starts its own sequence at the same number: give each
        # folio a code of its own.
        folio = cls.env["pms.folio"].create(
            {"name": code, "pms_property_id": prop.id, "partner_id": partner.id}
        )
        folio.write(
            {
                "external_reference": locator,
                "first_checkin": arrival,
                "last_checkout": arrival.replace(day=15),
            }
        )
        return folio

    def _folios_setup(self):
        Partner = self.env["res.partner"]
        guest = Partner.create({"firstname": "María", "lastname": "García López"})
        agency = Partner.create({"name": "AI Find Agency SL", "is_company": True})
        stranger = Partner.create({"firstname": "Ana", "lastname": "Oculta"})
        self.folio_a = self._folio(
            self.prop_a, guest, "AIP/26/000101", "BKG-12345", date(2026, 11, 10)
        )
        self.folio_b = self._folio(
            self.prop_b, agency, "AIS/26/000102", "EXP-777", date(2026, 12, 20)
        )
        self.folio_c = self._folio(
            self.prop_c, stranger, "AIH/26/000103", "HID-999", date(2026, 11, 10)
        )

    def _folio_rows(self, **kwargs):
        return self._run("pms_folio_find", **kwargs)["result"]["folios"]

    def test_folio_find_by_code_locator_and_holder(self):
        self._folios_setup()
        for text in ("AIP/26/000101", "000101", "bkg-12345", "Garc"):
            with self.subTest(text=text):
                rows = self._folio_rows(text=text)
                self.assertEqual([row["id"] for row in rows], self.folio_a.ids)
        (row,) = self._folio_rows(text="BKG-12345")
        self.assertEqual(row["code"], "AIP/26/000101")
        self.assertEqual(row["locator"], "BKG-12345")
        self.assertEqual(row["property_id"], self.prop_a.id)
        self.assertEqual(row["property"], "AI Find Hotel Ponferrada")
        self.assertEqual(row["arrival"], "2026-11-10")
        self.assertEqual(row["departure"], "2026-11-15")
        self.assertEqual(row["state"], "draft")
        self.assertTrue(row["state_label"])

    def test_folio_holder_is_masked(self):
        self._folios_setup()
        response = self._run("pms_folio_find", text="BKG-12345")
        (row,) = response["result"]["folios"]
        self.assertEqual(row["holder"], "María G.")
        dump = json.dumps(response, ensure_ascii=False)
        for secret in ("García", "López"):
            self.assertNotIn(secret, dump)
        # Companies are not people: they are shown as they are.
        (agency_row,) = self._folio_rows(text="EXP-777")
        self.assertEqual(agency_row["holder"], "AI Find Agency SL")

    def test_folio_holder_written_on_the_folio_is_masked_too(self):
        self._folios_setup()
        self.folio_a.write({"partner_name": "Lucía Fernández Prieto"})
        (row,) = self._folio_rows(text="BKG-12345")
        self.assertEqual(row["holder"], "Lucía F.")

    def test_folio_find_only_in_my_properties(self):
        self._folios_setup()
        for text in ("HID-999", "AIH/26/000103", "Oculta"):
            with self.subTest(text=text):
                self.assertEqual(self._folio_rows(text=text), [])
        self.assertEqual(
            [r["id"] for r in self._folio_rows(text="-", property_id=self.prop_a.id)],
            self.folio_a.ids,
        )
        self.assertEqual(
            [r["id"] for r in self._folio_rows(text="-", property_id=self.prop_b.id)],
            self.folio_b.ids,
        )
        with self.assertRaises(AccessError):
            self._folio_rows(text="-", property_id=self.prop_c.id)

    def test_folio_find_around_the_arrival_date(self):
        self._folios_setup()
        cases = (
            ("2026-11-10", self.folio_a),
            ("2026-11-13", self.folio_a),
            ("2026-11-14", self.env["pms.folio"]),
            ("2026-12-18", self.folio_b),
            ("2027-01-01", self.env["pms.folio"]),
        )
        for arrival, expected in cases:
            with self.subTest(arrival=arrival):
                rows = self._folio_rows(text="-", arrival_date=arrival)
                self.assertEqual([row["id"] for row in rows], expected.ids)

    # Partners

    def _partners_setup(self):
        Partner = self.env["res.partner"]
        self.salesperson = new_test_user(
            self.env, login="ai_find_sales", name="AI Find Salesperson"
        )
        self.company = Partner.create(
            {
                "name": "AI Find Corp",
                "is_company": True,
                "ref": "AIF1",
                "user_id": self.salesperson.id,
            }
        )
        channel = self.env["pms.sale.channel"].create(
            {"name": "AI Find agencies", "channel_type": "indirect"}
        )
        self.agency = Partner.create(
            {
                "name": "AI Find Travel",
                "is_company": True,
                "is_agency": True,
                "sale_channel_id": channel.id,
            }
        )
        self.person = Partner.create({"name": "AI Find Person"})
        self.hidden = Partner.create(
            {
                "name": "AI Find Hidden Corp",
                "is_company": True,
                "pms_property_ids": [(6, 0, self.prop_c.ids)],
            }
        )
        # Tax ids and contact data as found in real data, without the VAT check.
        for partner, vat in (
            (self.company, "ESB12345674"),
            (self.person, "12345678Z"),
            (self.hidden, "ESB00000000"),
        ):
            self.env.cr.execute(
                "UPDATE res_partner SET vat = %s, email = 'x@example.com', "
                "phone = '600000000' WHERE id = %s",
                [vat, partner.id],
            )
        self.env["res.partner"].invalidate_model()

    def test_partner_find_by_name_vat_and_reference(self):
        self._partners_setup()
        for text in ("find corp", "B12345674", "aif1"):
            with self.subTest(text=text):
                rows = self._run("pms_partner_find", text=text)["result"]["partners"]
                self.assertEqual([row["id"] for row in rows], self.company.ids)
        (row,) = self._run("pms_partner_find", text="AI Find Corp")["result"][
            "partners"
        ]
        self.assertEqual(
            row,
            {
                "id": self.company.id,
                "name": "AI Find Corp",
                "vat": "ESB12345674",
                "is_agency": False,
                "salesperson_id": self.salesperson.id,
                "salesperson": "AI Find Salesperson",
            },
        )

    def test_partner_find_returns_agencies_and_never_people_or_contact_data(self):
        self._partners_setup()
        response = self._run("pms_partner_find", text="AI Find")
        rows = response["result"]["partners"]
        self.assertEqual(
            {row["id"] for row in rows}, set((self.company | self.agency).ids)
        )
        agency = next(row for row in rows if row["id"] == self.agency.id)
        self.assertTrue(agency["is_agency"])
        self.assertIsNone(agency["salesperson"])
        dump = json.dumps(response)
        for secret in ("x@example.com", "600000000", "12345678Z", "Person"):
            self.assertNotIn(secret, dump)
        # Not even by its tax id: a person is not looked up by their DNI.
        found = self._run("pms_partner_find", text="12345678Z")["result"]["partners"]
        self.assertEqual(found, [])

    def test_partner_find_respects_the_properties_of_the_partner(self):
        self._partners_setup()
        for text in ("Hidden", "B00000000"):
            with self.subTest(text=text):
                found = self._run("pms_partner_find", text=text)["result"]["partners"]
                self.assertEqual(found, [])
        self.hidden.pms_property_ids = self.prop_a
        found = self._run("pms_partner_find", text="Hidden")["result"]["partners"]
        self.assertEqual([row["id"] for row in found], self.hidden.ids)

    # Users and employees

    def _users_setup(self):
        common = {"groups": "base.group_user,pms.group_pms_user"}
        self.ann = new_test_user(
            self.env,
            login="ai_find_ann",
            name="AI Find Ann",
            pms_property_ids=[(6, 0, self.prop_a.ids)],
            pms_property_id=self.prop_a.id,
            **common,
        )
        self.bob = new_test_user(
            self.env,
            login="ai_find_bob",
            name="AI Find Bob",
            pms_property_ids=[(6, 0, self.prop_c.ids)],
            pms_property_id=self.prop_c.id,
            **common,
        )
        self.portal = new_test_user(
            self.env,
            login="ai_find_portal",
            name="AI Find Portal",
            groups="base.group_portal",
        )

    def test_user_find_returns_internal_users_of_my_properties(self):
        self._users_setup()
        users = self._run("pms_user_find", text="AI Find")["result"]["users"]
        by_id = {row["id"]: row for row in users}
        self.assertIn(self.ann.id, by_id)
        self.assertIn(self.user.id, by_id)
        self.assertEqual(
            by_id[self.ann.id],
            {"id": self.ann.id, "name": "AI Find Ann", "login": "ai_find_ann"},
        )
        # A portal user is not staff; Bob only works in a property I cannot see.
        self.assertNotIn(self.portal.id, by_id)
        self.assertNotIn(self.bob.id, by_id)
        found = self._run("pms_user_find", text="ai_find_ann")["result"]["users"]
        self.assertEqual([row["id"] for row in found], self.ann.ids)

    def test_user_find_by_property(self):
        self._users_setup()
        rows = self._run("pms_user_find", text="AI Find", property_id=self.prop_a.id)[
            "result"
        ]["users"]
        self.assertIn(self.ann.id, {row["id"] for row in rows})
        self.assertNotIn(self.bob.id, {row["id"] for row in rows})
        with self.assertRaises(AccessError):
            self._run("pms_user_find", text="AI Find", property_id=self.prop_c.id)

    def test_user_find_employees_with_job_and_properties(self):
        if "hr.employee" not in self.env:
            self.skipTest("hr is not installed")
        self._users_setup()
        Employee = self.env["hr.employee"]
        values = {
            "name": "AI Find Employee",
            "job_title": "TAZ",
            "user_id": self.ann.id,
        }
        other = {"name": "AI Find Receptionist", "job_title": "Reception"}
        has_properties = "property_ids" in Employee._fields
        if has_properties:
            values["property_ids"] = [(6, 0, (self.prop_a | self.prop_c).ids)]
            other["property_ids"] = [(6, 0, self.prop_b.ids)]
        employee, receptionist = Employee.create([values, other])
        # The job is searched too: "who is the TAZ".
        result = self._run("pms_user_find", text="taz")["result"]
        self.assertEqual([row["id"] for row in result["employees"]], employee.ids)
        row = result["employees"][0]
        self.assertEqual(row["name"], "AI Find Employee")
        self.assertEqual(row["job"], "TAZ")
        self.assertEqual(row["user_id"], self.ann.id)
        if has_properties:
            # Only the properties of the user are shown.
            self.assertEqual(
                row["properties"], [{"id": self.prop_a.id, "name": self.prop_a.name}]
            )
            for prop, expected in (
                (self.prop_a, employee),
                (self.prop_b, receptionist),
            ):
                result = self._run(
                    "pms_user_find", text="AI Find", property_id=prop.id
                )["result"]
                self.assertEqual(
                    [r["id"] for r in result["employees"]], expected.ids, prop.name
                )
        else:
            self.assertNotIn("properties", row)
            response = self._run(
                "pms_user_find", text="AI Find", property_id=self.prop_a.id
            )
            self.assertEqual(response["result"]["employees"], [])
            self.assertIn("property", " ".join(response["warnings"]))

    def test_user_find_without_access_to_employees(self):
        self._users_setup()
        model_class = type(self.env["pms.ai.tools"])
        with mock.patch.object(model_class, "_ai_employee_model", return_value=None):
            response = self._run("pms_user_find", text="AI Find")
        # The users are still found; the employees are reported as unavailable.
        self.assertIn(self.ann.id, {u["id"] for u in response["result"]["users"]})
        self.assertEqual(response["result"]["employees"], [])
        self.assertIn("Employees are not available", response["warnings"][0])

    # Products, board services and room types

    def _products_setup(self):
        Product = self.env["product.product"]
        self.breakfast = Product.create(
            {
                "name": "AI Find Breakfast",
                "default_code": "AIFB",
                "type": "service",
                "per_day": True,
                "consumed_on": "after",
            }
        )
        Product.create({"name": "AI Find Goods", "type": "consu"})
        Product.create(
            {
                "name": "AI Find Hidden Service",
                "type": "service",
                "pms_property_ids": [(6, 0, self.prop_c.ids)],
            }
        )
        room_class = self.env["pms.room.type.class"].create(
            {"name": "AI Find class", "default_code": "AIFC"}
        )
        RoomType = self.env["pms.room.type"]
        self.room_type = RoomType.create(
            {
                "name": "AI Find Double",
                "default_code": "AIFD",
                "class_id": room_class.id,
                "pms_property_ids": [(6, 0, self.prop_a.ids)],
            }
        )
        RoomType.create(
            {
                "name": "AI Find Hidden Suite",
                "default_code": "AIFH",
                "class_id": room_class.id,
                "pms_property_ids": [(6, 0, self.prop_c.ids)],
            }
        )
        Board = self.env["pms.board.service"]
        self.board = Board.create(
            {"name": "AI Find Half Board", "default_code": "AIHB"}
        )
        Board.create(
            {
                "name": "AI Find Hidden Board",
                "default_code": "AIHH",
                "pms_property_ids": [(6, 0, self.prop_c.ids)],
            }
        )

    def test_product_find_all_kinds(self):
        self._products_setup()
        response = self._run("pms_product_find", text="AI Find")
        result = response["result"]
        # Services only: no consumables, no hidden ones, no room types.
        self.assertEqual(
            result["products"],
            [
                {
                    "id": self.breakfast.id,
                    "name": "AI Find Breakfast",
                    "code": "AIFB",
                    "per_day": True,
                    "consumed_on": "after",
                }
            ],
        )
        self.assertEqual(
            result["board_services"],
            [
                {
                    "id": self.board.id,
                    "name": "AI Find Half Board",
                    "code": "AIHB",
                    "properties": [],
                }
            ],
        )
        self.assertEqual(
            result["room_types"],
            [
                {
                    "id": self.room_type.id,
                    "name": "AI Find Double",
                    "code": "AIFD",
                    "class": "AI Find class",
                    "properties": ["AI Find Hotel Ponferrada"],
                }
            ],
        )

    def test_product_find_by_kind(self):
        self._products_setup()
        for kind, filled in (
            ("service", "products"),
            ("board", "board_services"),
            ("room_type", "room_types"),
        ):
            with self.subTest(kind=kind):
                result = self._run("pms_product_find", text="AI Find", kind=kind)[
                    "result"
                ]
                for key in ("products", "board_services", "room_types"):
                    self.assertEqual(bool(result[key]), key == filled, key)
        # A room type is a product too, but it is only found as a room type.
        result = self._run("pms_product_find", text="AI Find Double", kind="service")
        self.assertEqual(result["result"]["products"], [])
        found = self._run("pms_product_find", text="aifd")["result"]
        self.assertEqual([r["id"] for r in found["room_types"]], self.room_type.ids)
