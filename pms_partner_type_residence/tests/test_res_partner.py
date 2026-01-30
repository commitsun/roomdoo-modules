from psycopg2 import IntegrityError

from odoo.tests.common import TransactionCase


class TestResPartnerResidence(TransactionCase):
    def _create_partner(self, vals=None):
        default_vals = {"name": "Test Partner"}
        if vals:
            default_vals.update(vals)
        return self.env["res.partner"].create(default_vals)

    def _create_residence(self, parent, vals=None):
        default_vals = {
            "name": parent.name,
            "parent_id": parent.id,
            "type": "residence",
        }
        if vals:
            default_vals.update(vals)
        return self.env["res.partner"].create(default_vals)

    # =====================
    # residence_partner_id compute
    # =====================

    def test_residence_partner_id_returns_self_when_no_residence(self):
        partner = self._create_partner()
        self.assertEqual(partner.residence_partner_id, partner)

    def test_residence_partner_id_returns_residence_child(self):
        partner = self._create_partner()
        residence = self._create_residence(partner)
        partner.invalidate_recordset()

        self.assertEqual(partner.residence_partner_id, residence)

    def test_residence_partner_id_ignores_other_child_types(self):
        partner = self._create_partner()
        self.env["res.partner"].create(
            {
                "name": "Contact Child",
                "parent_id": partner.id,
                "type": "contact",
            }
        )
        partner.invalidate_recordset()

        self.assertEqual(partner.residence_partner_id, partner)

    def test_residence_partner_id_with_multiple_child_types(self):
        partner = self._create_partner()
        self.env["res.partner"].create(
            {
                "name": "Invoice Child",
                "parent_id": partner.id,
                "type": "invoice",
            }
        )
        residence = self._create_residence(partner)
        self.env["res.partner"].create(
            {
                "name": "Delivery Child",
                "parent_id": partner.id,
                "type": "delivery",
            }
        )
        partner.invalidate_recordset()

        self.assertEqual(partner.residence_partner_id, residence)

    # =====================
    # type selection
    # =====================

    def test_type_residence_available(self):
        type_field = self.env["res.partner"]._fields["type"]
        selection_keys = [key for key, _ in type_field.selection]
        self.assertIn("residence", selection_keys)

    # =====================
    # unique index constraint
    # =====================

    def test_unique_residence_per_parent(self):
        partner = self._create_partner()
        self._create_residence(partner)

        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self._create_residence(partner)

    def test_multiple_residences_different_parents_allowed(self):
        partner1 = self._create_partner({"name": "Partner 1"})
        partner2 = self._create_partner({"name": "Partner 2"})

        residence1 = self._create_residence(partner1)
        residence2 = self._create_residence(partner2)

        self.assertTrue(residence1)
        self.assertTrue(residence2)
        self.assertNotEqual(residence1, residence2)
