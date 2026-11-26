from fastapi import status

from .common import CommonTestCustomerPortalApi

# A 1x1 transparent PNG.
PNG = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8"
    b"AAAAASUVORK5CYII="
)
PROPERTY_KEYS = {
    "id",
    "name",
    "address",
    "latitude",
    "longitude",
    "phone",
    "email",
    "logo",
    "images",
    "defaultArrivalHour",
    "defaultDepartureHour",
    "timezone",
}


class TestPortalProperty(CommonTestCustomerPortalApi):
    def _get_property(self, token):
        return self._get(f"/portal/{token}/property")

    def _assert_image_url(self, url):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertTrue(url.startswith(f"{base_url}/web/image/"), url)
        self.assertIn("?access_token=", url)

    def test_property_by_token(self):
        state = self.env.ref("base.state_es_c")
        self.property.write(
            {
                "street": "Rúa Real 1",
                "zip": "15003",
                "city": "A Coruña",
                "state_id": state.id,
                "country_id": state.country_id.id,
                "partner_latitude": 43.3713,
                "partner_longitude": -8.396,
                "phone": "+34981000000",
                "email": "coruna@example.com",
                "logo": PNG,
                "hotel_image_pms_api_rest": PNG,
                "default_arrival_hour": "15:00",
                "default_departure_hour": "11:00",
                "tz": "Europe/Madrid",
            }
        )
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])

        response = self._get_property(folio.access_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(set(body), PROPERTY_KEYS)
        self.assertEqual(body["id"], self.property.id)
        self.assertEqual(body["name"], "Hotel Portal")
        self.assertEqual(
            body["address"],
            {
                "street": "Rúa Real 1",
                "postalCode": "15003",
                "city": "A Coruña",
                "province": state.name,
                "country": state.country_id.name,
            },
        )
        self.assertEqual(body["latitude"], 43.3713)
        self.assertEqual(body["longitude"], -8.396)
        self.assertEqual(body["phone"], "+34981000000")
        self.assertEqual(body["email"], "coruna@example.com")
        self._assert_image_url(body["logo"])
        self.assertEqual(len(body["images"]), 1)
        self._assert_image_url(body["images"][0])
        self.assertNotEqual(body["images"][0], body["logo"])
        self.assertEqual(body["defaultArrivalHour"], "15:00")
        self.assertEqual(body["defaultDepartureHour"], "11:00")
        self.assertEqual(body["timezone"], "Europe/Madrid")

    def test_property_without_location_nor_images(self):
        self.property.write(
            {
                "street": False,
                "zip": False,
                "city": False,
                "state_id": False,
                "country_id": False,
                "partner_latitude": 0.0,
                "partner_longitude": 0.0,
                "phone": False,
                "email": False,
                "logo": False,
                "hotel_image_pms_api_rest": False,
            }
        )
        folio = self._create_folio([(self.double, 3, 2, 2, 0)])

        body = self._get_property(folio.access_token).json()

        self.assertEqual(
            body["address"],
            {"street": "", "postalCode": "", "city": "", "province": "", "country": ""},
        )
        self.assertIsNone(body["latitude"])
        self.assertIsNone(body["longitude"])
        self.assertEqual(body["phone"], "")
        self.assertEqual(body["email"], "")
        self.assertIsNone(body["logo"])
        self.assertEqual(body["images"], [])

    def test_unknown_token_is_not_found(self):
        response = self._get_property("not-a-real-token")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["type"], "/errors/portal-link-not-found")
