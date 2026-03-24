import datetime

from fastapi import status

from odoo import fields

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestFoliosEndpoints(CommonTestPmsApi):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {
                "name": "Standard",
                "default_code": "STD",
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.test_property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class.id,
            }
        )
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.test_property.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.room2 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.test_property.id,
                "name": "102",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "firstname": "John",
                "lastname": "Doe",
                "email": "john@example.com",
                "birthdate_date": "1990-01-01",
                "gender": "male",
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Test", "channel_type": "direct"}
        )

    def _create_folio_with_reservation(
        self, checkin=None, checkout=None, adults=1, partner=None
    ):
        today = fields.date.today()
        checkin = checkin or today
        checkout = checkout or (today + datetime.timedelta(days=2))
        partner = partner or self.partner
        nights = (checkout - checkin).days
        folio = self.env["pms.folio"].create(
            {
                "pms_property_id": self.test_property.id,
                "partner_name": partner.name,
                "partner_id": partner.id,
            }
        )
        self.env["pms.reservation"].create(
            {
                "folio_id": folio.id,
                "room_type_id": self.room_type.id,
                "partner_id": partner.id,
                "adults": adults,
                "sale_channel_origin_id": self.sale_channel.id,
                "reservation_line_ids": [
                    (
                        0,
                        False,
                        {"date": checkin + datetime.timedelta(days=i)},
                    )
                    for i in range(nights)
                ],
            }
        )
        return folio

    def _get_folio_ids(self, test_client, extra_params=""):
        response = test_client.get(
            f"/folios?pmsPropertyId={self.test_property.id}{extra_params}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        return [f["id"] for f in response.json()["items"]]

    # ---------------------------------------------------------------
    # Basic endpoint
    # ---------------------------------------------------------------
    def test_folios_get(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/folios")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
            self.assertIn("count", response.json())
            self.assertIn("items", response.json())

    # ---------------------------------------------------------------
    # Filter: checkinFrom / checkinTo
    # ---------------------------------------------------------------
    def test_filter_checkin_same_date(self):
        """checkinFrom == checkinTo should match folios checking in that exact day"""
        today = fields.date.today()
        target = today + datetime.timedelta(days=40)
        folio = self._create_folio_with_reservation(
            checkin=target, checkout=target + datetime.timedelta(days=1)
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(
                test_client,
                f"&checkinFrom={target}&checkinTo={target}",
            )
            self.assertIn(folio.id, folio_ids)

    def test_filter_checkin_only_from_does_not_filter(self):
        """checkinFrom without checkinTo should not apply the filter"""
        self._create_folio_with_reservation()
        with self._create_test_client() as test_client:
            self._login(test_client)
            all_ids = self._get_folio_ids(test_client)
            partial_ids = self._get_folio_ids(
                test_client,
                f"&checkinFrom={fields.date.today()}",
            )
            self.assertEqual(len(all_ids), len(partial_ids))

    # ---------------------------------------------------------------
    # Filter: checkoutFrom / checkoutTo
    # ---------------------------------------------------------------
    def test_filter_checkout_same_date(self):
        today = fields.date.today()
        checkin = today + datetime.timedelta(days=45)
        checkout = checkin + datetime.timedelta(days=1)
        folio = self._create_folio_with_reservation(checkin=checkin, checkout=checkout)
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(
                test_client,
                f"&checkoutFrom={checkout}&checkoutTo={checkout}",
            )
            self.assertIn(folio.id, folio_ids)

    def test_filter_checkout_only_from_does_not_filter(self):
        self._create_folio_with_reservation()
        with self._create_test_client() as test_client:
            self._login(test_client)
            all_ids = self._get_folio_ids(test_client)
            partial_ids = self._get_folio_ids(
                test_client,
                f"&checkoutFrom={fields.date.today()}",
            )
            self.assertEqual(len(all_ids), len(partial_ids))

    # ---------------------------------------------------------------
    # Filter: preCheckinState
    # ---------------------------------------------------------------
    def test_filter_precheckin_pending_matches_dummy(self):
        """pending = dummy (no data at all)"""
        folio = self._create_folio_with_reservation(adults=1)
        cp = folio.reservation_ids[0].checkin_partner_ids[0]
        self.assertEqual(cp.state, "dummy")
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&preCheckinState=pending")
            self.assertIn(folio.id, folio_ids)

    def test_filter_precheckin_partial_matches_draft(self):
        """partial = draft (some data filled)"""
        folio = self._create_folio_with_reservation(adults=1)
        cp = folio.reservation_ids[0].checkin_partner_ids[0]
        cp.write({"firstname": "Jane"})
        self.assertEqual(cp.state, "draft")
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&preCheckinState=partial")
            self.assertIn(folio.id, folio_ids)

    def test_filter_precheckin_pending_excludes_draft(self):
        """pending should NOT match folios whose only checkins are draft"""
        folio = self._create_folio_with_reservation(adults=1)
        cp = folio.reservation_ids[0].checkin_partner_ids[0]
        cp.write({"firstname": "Jane"})
        self.assertEqual(cp.state, "draft")
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&preCheckinState=pending")
            self.assertNotIn(folio.id, folio_ids)

    def test_filter_precheckin_partial_excludes_dummy(self):
        """partial should NOT match folios whose only checkins are dummy"""
        folio = self._create_folio_with_reservation(adults=1)
        cp = folio.reservation_ids[0].checkin_partner_ids[0]
        self.assertEqual(cp.state, "dummy")
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&preCheckinState=partial")
            self.assertNotIn(folio.id, folio_ids)

    # ---------------------------------------------------------------
    # Filter: invoiceState
    # ---------------------------------------------------------------
    def test_filter_invoice_state_to_invoice(self):
        """A confirmed folio (to_invoice) should appear with toInvoice"""
        folio = self._create_folio_with_reservation()
        self.assertIn(folio.invoice_status, ("to_invoice", "to_confirm"))
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&invoiceState=toInvoice")
            self.assertIn(folio.id, folio_ids)

    def test_filter_invoice_state_invoiced_excludes_to_invoice(self):
        """A to_invoice folio should NOT appear with invoiceState=invoiced"""
        folio = self._create_folio_with_reservation()
        self.assertIn(folio.invoice_status, ("to_invoice", "to_confirm"))
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(test_client, "&invoiceState=invoiced")
            self.assertNotIn(folio.id, folio_ids)

    # ---------------------------------------------------------------
    # Filters combined
    # ---------------------------------------------------------------
    def test_filter_combined_checkin_and_precheckin(self):
        today = fields.date.today()
        target = today + datetime.timedelta(days=30)
        folio = self._create_folio_with_reservation(
            checkin=target,
            checkout=target + datetime.timedelta(days=1),
            adults=1,
        )
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(
                test_client,
                f"&checkinFrom={target}&checkinTo={target}" f"&preCheckinState=pending",
            )
            self.assertIn(folio.id, folio_ids)

    def test_filter_combined_checkin_and_precheckin_mismatch(self):
        """Checkin matches but preCheckinState doesn't → excluded"""
        today = fields.date.today()
        target = today + datetime.timedelta(days=31)
        folio = self._create_folio_with_reservation(
            checkin=target,
            checkout=target + datetime.timedelta(days=1),
            adults=1,
        )
        cp = folio.reservation_ids[0].checkin_partner_ids[0]
        cp.write({"firstname": "Jane"})
        self.assertEqual(cp.state, "draft")
        with self._create_test_client() as test_client:
            self._login(test_client)
            folio_ids = self._get_folio_ids(
                test_client,
                f"&checkinFrom={target}&checkinTo={target}" f"&preCheckinState=pending",
            )
            self.assertNotIn(folio.id, folio_ids)
