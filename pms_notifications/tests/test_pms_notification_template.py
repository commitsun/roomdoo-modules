from odoo.exceptions import ValidationError

from odoo.addons.pms.tests.common import TestPms


class TestPmsNotificationTemplate(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.availability_plan2 = cls.env["pms.availability.plan"].create(
            {"name": "Availability Plan 2"}
        )
        cls.pricelist2 = cls.env["product.pricelist"].create(
            {
                "name": "Pricelist 2",
                "availability_plan_id": cls.availability_plan2.id,
            }
        )
        cls.company2 = cls.env["res.company"].create({"name": "Company 2"})
        cls.pms_property2 = cls.env["pms.property"].create(
            {
                "name": "Property 2",
                "company_id": cls.company2.id,
                "default_pricelist_id": cls.pricelist2.id,
            }
        )
        cls.folio_draft = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "partner_name": "Test Draft Customer",
            }
        )
        cls.folio_confirm = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "partner_name": "Test Confirm Customer",
            }
        )
        # Avoid triggering notification rules during test bootstrap.
        cls.env.cr.execute(
            "UPDATE pms_folio SET state = %s WHERE id = %s",
            ("confirm", cls.folio_confirm.id),
        )
        cls.folio_confirm.invalidate_recordset(["state"])
        cls.folio_model = cls.env.ref("pms.model_pms_folio")

    def _create_template(self, code_suffix, **extra_vals):
        values = {
            "name": f"Template {code_suffix}",
            "code": f"test_template_{code_suffix}",
            "model_id": self.folio_model.id,
        }
        values.update(extra_vals)
        return self.env["pms.notification.template"].create(values)

    def test_property_availability_domain(self):
        template_all_properties = self._create_template("all_properties")
        template_property_1_only = self._create_template(
            "property_1_only",
            pms_property_ids=[(6, 0, [self.pms_property1.id])],
        )

        Template = self.env["pms.notification.template"]
        templates = template_all_properties | template_property_1_only

        available_for_property_1 = Template.search(
            Template._property_availability_domain(self.pms_property1.id)
            + [("id", "in", templates.ids)]
        )
        self.assertIn(template_all_properties, available_for_property_1)
        self.assertIn(template_property_1_only, available_for_property_1)

        available_for_property_2 = Template.search(
            Template._property_availability_domain(self.pms_property2.id)
            + [("id", "in", templates.ids)]
        )
        self.assertIn(template_all_properties, available_for_property_2)
        self.assertNotIn(template_property_1_only, available_for_property_2)

    def test_apply_domain_matches_only_confirmed_folio(self):
        template = self._create_template(
            "confirm_only",
            apply_domain="[('state','=','confirm')]",
        )

        self.assertFalse(template._is_applicable_to_folio(self.folio_draft))
        self.assertTrue(template._is_applicable_to_folio(self.folio_confirm))

    def test_invalid_apply_domain_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            self._create_template(
                "invalid_apply_domain",
                apply_domain="not_a_domain",
            )
