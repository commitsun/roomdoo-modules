from datetime import date

from odoo.tests.common import TransactionCase


class TestAutoinvoiceDates(TransactionCase):
    """Verify that ``folio.sale.line._get_to_invoice_date`` produces the
    residence-specific schedule for long-stay reservations and falls back
    to standard ``pms_autoinvoice`` behaviour otherwise.

    These tests use the existing PMS fixtures from ``setUpClass`` instead of
    a full ``pms_long_stay`` reservation flow (which exercises
    ``_split_long_stay_into_periods`` and the auto-created services). The
    bridge logic only reads three things from each line — reservation_type,
    product is_long_stay_product flag and service_line.date — so an
    in-memory line built from controlled fixtures is enough to exercise
    every branch.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Test Mendel"})
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "TLS", "is_pms_available": True}
        )
        cls.property = cls.env["pms.property"].create(
            {
                "name": "Test Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
                "default_invoicing_policy": "month_day",
                "invoicing_month_day": 1,
                "margin_days_autoinvoice": 0,
            }
        )
        cls.room_type_class = cls.env["pms.room.type.class"].create(
            {"name": "Habitación", "default_code": "HAB"}
        )
        cls.long_stay_room_type = cls.env["pms.room.type"].create(
            {
                "name": "Individual long stay",
                "default_code": "ILS",
                "class_id": cls.room_type_class.id,
                "pms_property_ids": [(6, 0, [cls.property.id])],
                "list_price": 1200.0,
                "long_stay_period": "monthly",
                "long_stay_price": 1200.0,
            }
        )
        cls.standard_room_type = cls.env["pms.room.type"].create(
            {
                "name": "Standard",
                "default_code": "STD",
                "class_id": cls.room_type_class.id,
                "pms_property_ids": [(6, 0, [cls.property.id])],
                "list_price": 80.0,
            }
        )
        cls.long_stay_room = cls.env["pms.room"].create(
            {
                "name": "L01",
                "pms_property_id": cls.property.id,
                "room_type_id": cls.long_stay_room_type.id,
            }
        )
        cls.standard_room = cls.env["pms.room"].create(
            {
                "name": "S01",
                "pms_property_id": cls.property.id,
                "room_type_id": cls.standard_room_type.id,
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "Residente Test"})
        cls.extra_product = cls.env["product.product"].create(
            {"name": "Lavandería", "type": "service"}
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_long_stay_reservation(self, checkin, checkout):
        """Create a minimal long_stay reservation bypassing the auto-split
        of ``pms_long_stay`` (which would create children we don't need).
        """
        return self.env["pms.reservation"].create(
            {
                "checkin": checkin,
                "checkout": checkout,
                "room_type_id": self.long_stay_room_type.id,
                "preferred_room_id": self.long_stay_room.id,
                "pms_property_id": self.property.id,
                "partner_id": self.partner.id,
                "adults": 1,
                "reservation_type": "long_stay",
                "pricelist_id": self.pricelist.id,
            }
        )

    def _make_standard_reservation(self, checkin, checkout):
        return self.env["pms.reservation"].create(
            {
                "checkin": checkin,
                "checkout": checkout,
                "room_type_id": self.standard_room_type.id,
                "preferred_room_id": self.standard_room.id,
                "pms_property_id": self.property.id,
                "partner_id": self.partner.id,
                "adults": 1,
                "pricelist_id": self.pricelist.id,
            }
        )

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_pernocta_long_stay_returns_false(self):
        """Pernocta line of a long-stay reservation (priced 0 €) must never
        be autoinvoiced.
        """
        reservation = self._make_long_stay_reservation(
            date(2026, 3, 15), date(2026, 4, 15)
        )
        pernocta = reservation.folio_id.sale_line_ids.filtered(
            lambda l: l.reservation_line_ids and not l.service_id
        )
        self.assertTrue(pernocta, "pernocta sale line not found")
        self.assertFalse(
            pernocta[:1]._get_to_invoice_date(),
            "long-stay pernocta should never autoinvoice",
        )

    def test_long_stay_service_uses_first_of_checkin_month(self):
        """Sale line for the long_stay service (the monthly 'pernocta as
        a service') autoinvoices on day 1 of the check-in month.
        """
        reservation = self._make_long_stay_reservation(
            date(2026, 3, 15), date(2026, 4, 15)
        )
        product_tmpl = self.long_stay_room_type.long_stay_product_id
        ls_line = reservation.folio_id.sale_line_ids.filtered(
            lambda l: l.product_id.product_tmpl_id == product_tmpl
        )
        self.assertTrue(ls_line, "long_stay service line not found")
        self.assertEqual(
            ls_line[:1]._get_to_invoice_date(), date(2026, 3, 1)
        )

    def test_extra_service_defers_to_next_month(self):
        """A non-long-stay service consumed inside a long-stay reservation
        autoinvoices on day 1 of the month *after* the service line date.
        """
        reservation = self._make_long_stay_reservation(
            date(2026, 3, 15), date(2026, 4, 15)
        )
        extra_service = self.env["pms.service"].create(
            {
                "product_id": self.extra_product.id,
                "folio_id": reservation.folio_id.id,
                "reservation_id": reservation.id,
                "service_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.extra_product.id,
                            "day_qty": 1,
                            "price_unit": 15.0,
                            "date": date(2026, 3, 20),
                        },
                    )
                ],
            }
        )
        reservation.folio_id._compute_sale_line_ids()
        extra_line = reservation.folio_id.sale_line_ids.filtered(
            lambda l: l.service_id == extra_service
        )
        self.assertTrue(extra_line, "extra service sale line not found")
        self.assertEqual(
            extra_line[:1]._get_to_invoice_date(), date(2026, 4, 1)
        )

    def test_non_long_stay_falls_back_to_super(self):
        """A standard (non long_stay) reservation must keep the standard
        ``pms_autoinvoice`` schedule (month_day -> day 1 of next or same
        month depending on checkout).
        """
        reservation = self._make_standard_reservation(
            date(2026, 3, 5), date(2026, 3, 10)
        )
        line = reservation.folio_id.sale_line_ids.filtered(
            lambda l: l.reservation_line_ids
        )[:1]
        self.assertTrue(line, "pernocta line of standard reservation missing")
        # month_day=1, checkout=Mar 10 -> next-month boundary = Apr 1
        self.assertEqual(line._get_to_invoice_date(), date(2026, 4, 1))
