import datetime
from functools import partial

from odoo import fields

from odoo.addons.extendable_fastapi.tests.common import FastAPITransactionCase
from odoo.addons.fastapi.dependencies import fastapi_endpoint


class CommonTestCustomerPortalApi(FastAPITransactionCase):
    """Customer portal app running as its restricted user, plus a property.

    The requests run as ``customer_portal_api_user``, so a read that forgets
    to go through sudo fails here as it would in production.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.portal_app = cls.env["fastapi.endpoint"].create(
            {
                "name": "Customer Portal API",
                "app": "customer_portal",
                "root_path": "/customerPortalApi",
                "user_id": cls.env.ref(
                    "pms_fastapi_customer_portal.customer_portal_api_user"
                ).id,
            }
        )
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.default_fastapi_app = cls.portal_app._get_app()
        cls.default_fastapi_dependency_overrides = {
            fastapi_endpoint: partial(lambda a: a, cls.portal_app)
        }
        cls.default_fastapi_odoo_env = cls.env
        cls.default_fastapi_running_user = cls.portal_app.user_id

        cls.availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Portal availability plan"}
        )
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Portal pricelist",
                "availability_plan_id": cls.availability_plan.id,
            }
        )
        cls.company = cls.env["res.company"].create(
            {"name": "Portal company", "vat": "ES11111111H"}
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Hotel Portal",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Portal class", "default_code": "PCL"}
        )
        cls.double = cls._create_room_type("Double Portal", "DBP", 50.0)
        cls.single = cls._create_room_type("Single Portal", "SGP", 30.0)
        cls.partner = cls.env["res.partner"].create(
            {
                "firstname": "Laura",
                "lastname": "García",
                "lastname2": "Pérez",
                "gender": "female",
                "email": "laura@example.com",
                "mobile": "+34600000000",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Portal direct", "channel_type": "direct"}
        )

    @classmethod
    def _create_room_type(cls, name, code, price):
        room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": name,
                "default_code": code,
                "class_id": cls.room_type_class.id,
                "list_price": price,
            }
        )
        for number in range(1, 4):
            cls.env["pms.room"].create(
                {
                    "pms_property_id": cls.property.id,
                    "name": f"{code}{number}",
                    "room_type_id": room_type.id,
                    "capacity": 4,
                }
            )
        return room_type

    def _create_folio(self, stays, partner=None, folio_values=None):
        """Confirmed folio with one reservation per stay.

        ``stays`` holds ``(room_type, first night offset from today, nights,
        adults, children)`` tuples.
        """
        partner = self.partner if partner is None else partner
        values = {"pms_property_id": self.property.id}
        if partner:
            values.update(partner_id=partner.id, partner_name=partner.name)
        values.update(folio_values or {})
        folio = self.env["pms.folio"].create(values)
        today = fields.Date.today()
        for room_type, offset, nights, adults, children in stays:
            checkin = today + datetime.timedelta(days=offset)
            self.env["pms.reservation"].create(
                {
                    "folio_id": folio.id,
                    "room_type_id": room_type.id,
                    "partner_id": partner.id if partner else False,
                    "adults": adults,
                    "children": children,
                    "sale_channel_origin_id": self.sale_channel.id,
                    "reservation_line_ids": [
                        (0, 0, {"date": checkin + datetime.timedelta(days=night)})
                        for night in range(nights)
                    ],
                }
            )
        folio.with_context(confirm_all_reservations=True).action_confirm()
        return folio

    def _cold_cache(self):
        """Start a request with an empty cache, as in production: a cached
        value is read without checking the access rights of the portal user."""
        self.env.flush_all()
        self.env.invalidate_all()

    def _get(self, path):
        self._cold_cache()
        with self._create_test_client() as test_client:
            return test_client.get(path)

    def _enable_test_ocr_supplier(self):
        """Give the property a document scanning supplier named "test".

        The suppliers are selection options added by their own modules, which
        are not dependencies here, so one is registered for the test only."""
        field = self.env["pms.property"]._fields["ocr_checkin_supplier"]
        option = ("test", "Test")
        field.selection.append(option)
        self.addCleanup(field.selection.remove, option)
        self.property.ocr_checkin_supplier = "test"

    def _request(self, method, path, **kwargs):
        self._cold_cache()
        with self._create_test_client() as test_client:
            return test_client.request(method, path, **kwargs)

    def _assert_problem(self, response, status_code, type_):
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        body = response.json()
        self.assertEqual(body["type"], type_)
        self.assertEqual(body["status"], status_code)
        return body
