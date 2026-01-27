from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestLinksEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_property.write(
            {
                "ine_tourism_number": "111",
                "ine_category_id": cls.env.ref("pms_l10n_es.turism_category_2"),
                "street": "Main Street 1",
                "zip": "28001",
                "city": "Madrid",
                "state_id": cls.env.ref("base.state_es_m").id,
                "country_id": cls.env.ref("base.es").id,
                "phone": "+34123456789",
            }
        )

    def test_kelly_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/kelly-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/vnd.ms-excel",
            )

    def test_ine_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/ine-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01&dateTo=2024-01-31"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/xml",
            )

    def test_transaction_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/transactions-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01&dateTo=2024-01-31"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/vnd.ms-excel",
            )

    def test_services_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/services-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01&dateTo=2024-01-31"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/vnd.ms-excel",
            )

    def test_departures_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/departures-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/vnd.ms-excel",
            )

    def test_arrivals_report(self):
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get(
                f"/reports/arrivals-report?pmsPropertyId={self.test_property.id}&dateFrom=2024-01-01"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertTrue(
                response.headers["content-disposition"].startswith("attachment;"),
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-type"],
                "application/vnd.ms-excel",
            )
