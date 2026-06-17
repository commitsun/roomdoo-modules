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
