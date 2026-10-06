from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestJournalsEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.cash_journal = cls.env["account.journal"].create(
            {
                "name": "PMS Cash",
                "type": "cash",
                "code": "PCSH",
                "company_id": cls.test_company.id,
                "allowed_on_pms": True,
                "pms_property_ids": [(6, 0, [cls.test_property.id])],
            }
        )
        cls.bank_journal = cls.env["account.journal"].create(
            {
                "name": "PMS Bank",
                "type": "bank",
                "code": "PBNK",
                "company_id": cls.test_company.id,
                "allowed_on_pms": True,
                "pms_property_ids": [(6, 0, [cls.test_property.id])],
            }
        )
        cls.sale_journal = cls.env["account.journal"].create(
            {
                "name": "PMS Sale",
                "type": "sale",
                "code": "PSAL",
                "company_id": cls.test_company.id,
                "allowed_on_pms": True,
                "pms_property_ids": [(6, 0, [cls.test_property.id])],
            }
        )
        # Allowed on PMS but tied to no property: a company-level account that
        # reception must never be offered. It used to come back from every
        # endpoint because the domain asked for it explicitly.
        cls.generic_journal = cls.env["account.journal"].create(
            {
                "name": "Generic Company Bank",
                "type": "bank",
                "code": "GNRC",
                "company_id": cls.test_company.id,
                "allowed_on_pms": True,
                "pms_property_ids": [(6, 0, [])],
            }
        )

    def test_journals_get(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/journals")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIsInstance(response.json(), list)

    def test_journals_filter_single_type(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/journals", params={"journalType": "cash"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            ids = {row["id"] for row in response.json()}
            self.assertIn(self.cash_journal.id, ids)
            self.assertNotIn(self.bank_journal.id, ids)
            self.assertNotIn(self.sale_journal.id, ids)

    def test_journals_filter_multiple_types(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                "/journals", params={"journalType": ["cash", "bank"]}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            ids = {row["id"] for row in response.json()}
            self.assertIn(self.cash_journal.id, ids)
            self.assertIn(self.bank_journal.id, ids)
            self.assertNotIn(self.sale_journal.id, ids)

    def test_journals_filter_invalid_type(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/journals", params={"journalType": "wrong"})
            self.assertEqual(
                response.status_code,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                response.text,
            )

    def test_generic_journal_is_not_listed(self):
        """A journal with no property is a company-level account, not a hotel
        one: it must not reach the selector."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/journals")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            ids = {row["id"] for row in response.json()}
            self.assertIn(self.bank_journal.id, ids)
            self.assertNotIn(self.generic_journal.id, ids)

    def test_generic_journal_is_not_listed_for_a_given_property(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get(
                "/journals", params={"pmsPropertyId": self.test_property.id}
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            ids = {row["id"] for row in response.json()}
            self.assertIn(self.bank_journal.id, ids)
            self.assertNotIn(self.generic_journal.id, ids)

    def test_payment_methods_of_a_generic_journal_are_not_offered(self):
        """The symptom users reported: a method offered in the selector that
        then never showed up in the payments listing, because the listing
        already filtered these journals out on its own."""
        generic_line = self.generic_journal.inbound_payment_method_line_ids[:1]
        self.assertTrue(generic_line, "precondition: the journal has no method line")
        generic_line.allowed_on_pms = True
        allowed_line = self.bank_journal.inbound_payment_method_line_ids[:1]
        allowed_line.allowed_on_pms = True
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/payment-methods")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            ids = {row["id"] for row in response.json()}
            self.assertIn(allowed_line.id, ids)
            self.assertNotIn(generic_line.id, ids)
