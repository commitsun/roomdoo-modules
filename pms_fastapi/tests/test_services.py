from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestServicesEndpoints(CommonTestPmsApi):
    def test_services_flag_board_services(self):
        """Every variant of a board service is flagged, other services aren't."""
        board_service = self.env["product.template"].create(
            {
                "name": "Test Breakfast",
                "type": "service",
                "is_board_service": True,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": self.env.ref(
                                "pms.product_attribute_board_guest"
                            ).id,
                            "value_ids": [
                                (
                                    6,
                                    0,
                                    [
                                        self.env.ref(
                                            "pms.product_attribute_value_board_guest_adult"
                                        ).id,
                                        self.env.ref(
                                            "pms.product_attribute_value_board_guest_child"
                                        ).id,
                                    ],
                                )
                            ],
                        },
                    )
                ],
            }
        )
        extra = self.env["product.product"].create(
            {"name": "Test Parking", "type": "service"}
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/services")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        flags = {item["id"]: item["isBoardService"] for item in response.json()}
        self.assertEqual(
            {
                product_id: flags[product_id]
                for product_id in board_service.product_variant_ids.ids
            },
            dict.fromkeys(board_service.product_variant_ids.ids, True),
        )
        self.assertFalse(flags[extra.id])
