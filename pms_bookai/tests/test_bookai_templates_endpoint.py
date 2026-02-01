import asyncio

from odoo.exceptions import ValidationError

from odoo.addons.pms.tests.common import TestPms
from odoo.addons.pms_bookai.routers.bookai_template import (
    list_available_bookai_templates,
)


class TestBookaiTemplatesEndpoint(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pms_property1.user_ids = [(6, 0, [cls.env.user.id])]
        cls.property_2 = cls.env["pms.property"].create(
            {
                "name": "Property 2",
                "company_id": cls.company1.id,
                "default_pricelist_id": cls.pricelist1.id,
                "user_ids": [(6, 0, [cls.env.user.id])],
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
        cls.template_all_properties = cls._create_bookai_template(
            code="test_bookai_all_properties",
            property_ids=[],
            apply_domain="[]",
            body="Hello {{ buyer_name }}",
            buyer_name="Dario",
        )
        cls.template_property_1_only = cls._create_bookai_template(
            code="test_bookai_property_1_only",
            property_ids=[cls.pms_property1.id],
            apply_domain="[]",
            body="Welcome {{ buyer_name }}",
            buyer_name="Ana",
            description="Buyer full name",
        )
        cls.template_property_2_only = cls._create_bookai_template(
            code="test_bookai_property_2_only",
            property_ids=[cls.property_2.id],
            apply_domain="[]",
            body="Hi {{ buyer_name }}",
            buyer_name="Paul",
        )
        cls.template_confirm_only = cls._create_bookai_template(
            code="test_bookai_confirm_only",
            property_ids=[],
            apply_domain="[('state','=','confirm')]",
            body="Confirmed {{ buyer_name }}",
            buyer_name="Maria",
        )

    @classmethod
    def _create_bookai_template(
        cls,
        code,
        property_ids,
        apply_domain,
        body,
        buyer_name,
        description="",
    ):
        template = cls.env["pms.notification.template"].create(
            {
                "name": f"Template {code}",
                "code": code,
                "model_id": cls.folio_model.id,
                "bookai_template_code": f"{code}_bookai",
                "apply_domain": apply_domain,
                "pms_property_ids": [(6, 0, property_ids)],
            }
        )
        cls.env["pms.notification.template.bookai.param"].create(
            {
                "template_id": template.id,
                "key": "buyer_name",
                "description": description,
                "value_type": "literal",
                "value_literal": buyer_name,
            }
        )
        template.write({"body": body})
        return template

    def test_bookai_body_constraint_validates_unknown_keys(self):
        template = self._create_bookai_template(
            code="test_bookai_body_constraint",
            property_ids=[],
            apply_domain="[]",
            body="Hello {{ buyer_name }}",
            buyer_name="Guest",
        )
        with self.assertRaises(ValidationError):
            template.write({"body": "Hello {{ unknown_key }}"})

    def _call_available_endpoint(self, property_id, folio_id=None):
        return asyncio.run(
            list_available_bookai_templates(
                env=self.env,
                property_id=property_id,
                folio_id=folio_id,
            )
        )

    def test_templates_available_without_folio(self):
        payload = self._call_available_endpoint(self.pms_property1.id)
        templates_by_code = {item.code: item for item in payload}

        self.assertIn("test_bookai_all_properties", templates_by_code)
        self.assertIn("test_bookai_property_1_only", templates_by_code)
        self.assertIn("test_bookai_confirm_only", templates_by_code)
        self.assertNotIn("test_bookai_property_2_only", templates_by_code)

        template_all = templates_by_code["test_bookai_all_properties"]
        self.assertEqual(template_all.body, "Hello {{ buyer_name }}")
        self.assertEqual(template_all.params[0].key, "buyer_name")
        self.assertIsNone(template_all.params[0].value)
        self.assertEqual(template_all.body_rendered, "")

        template_property_1 = templates_by_code["test_bookai_property_1_only"]
        self.assertEqual(template_property_1.params[0].description, "Buyer full name")

    def test_templates_available_with_folio_filters_and_renders(self):
        draft_payload = self._call_available_endpoint(
            self.pms_property1.id,
            self.folio_draft.id,
        )
        draft_templates = {item.code: item for item in draft_payload}
        self.assertIn("test_bookai_all_properties", draft_templates)
        self.assertIn("test_bookai_property_1_only", draft_templates)
        self.assertNotIn("test_bookai_confirm_only", draft_templates)
        self.assertEqual(
            draft_templates["test_bookai_all_properties"].params[0].value,
            "Dario",
        )
        self.assertEqual(
            draft_templates["test_bookai_all_properties"].body_rendered,
            "Hello Dario",
        )

        confirm_payload = self._call_available_endpoint(
            self.pms_property1.id,
            self.folio_confirm.id,
        )
        confirm_templates = {item.code: item for item in confirm_payload}
        self.assertIn("test_bookai_confirm_only", confirm_templates)
