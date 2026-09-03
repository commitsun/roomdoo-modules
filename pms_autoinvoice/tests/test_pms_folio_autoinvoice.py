import datetime

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pms.tests.common import TestPms


@tagged("post_install", "-at_install")
class TestPmsFolioInvoice(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.ref("base.main_company")
        if not company.chart_template_id:
            coa = cls.env.ref("l10n_generic_coa.configurable_chart_template", False)
            if not coa:
                coa = cls.env["account.chart.template"].search(
                    [("visible", "=", True)], limit=1
                )
            if not coa:
                cls.skipTest(cls, "No chart of accounts available.")
            coa.try_loading(company=company, install_demo=False)
        user = cls.env["res.users"].browse(1)
        cls.env = cls.env(user=user)
        # create a room type availability
        cls.room_type_availability = cls.env["pms.availability.plan"].create(
            {"name": "Availability plan for TEST"}
        )

        # journal to simplified invoices
        cls.simplified_journal = cls.env["account.journal"].create(
            {
                "name": "Simplified journal",
                "code": "SMP",
                "type": "sale",
                "company_id": cls.env.ref("base.main_company").id,
            }
        )

        # create a property
        cls.property = cls.env["pms.property"].create(
            {
                "name": "MY PMS TEST",
                "company_id": cls.env.ref("base.main_company").id,
                "default_pricelist_id": cls.pricelist1.id,
                "journal_simplified_invoice_id": cls.simplified_journal.id,
            }
        )

        # create room type
        cls.room_type_double = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.property.id],
                "name": "Double Test",
                "default_code": "DBL_Test",
                "class_id": cls.room_type_class1.id,
                "list_price": 25,
            }
        )

        # create rooms
        cls.room1 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Double 101",
                "room_type_id": cls.room_type_double.id,
                "capacity": 2,
            }
        )

        cls.room2 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Double 102",
                "room_type_id": cls.room_type_double.id,
                "capacity": 2,
            }
        )

        cls.room3 = cls.env["pms.room"].create(
            {
                "pms_property_id": cls.property.id,
                "name": "Double 103",
                "room_type_id": cls.room_type_double.id,
                "capacity": 2,
            }
        )

        # res.partner
        cls.partner_id = cls.env["res.partner"].create(
            {
                "name": "Miguel",
                "vat": "45224522J",
                "country_id": cls.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Calle de la calle",
            }
        )

        # create a sale channel
        cls.sale_channel_direct1 = cls.env["pms.sale.channel"].create(
            {
                "name": "Door",
                "channel_type": "direct",
            }
        )

    def create_configuration_accounting_scenario(self):
        """
        Method to simplified scenario to payments and accounting:
        # REVIEW:
        - Use new property with odoo demo data company to avoid account configuration
        - Emule SetUp with new property:
            - create demo_room_type_double
            - Create 2 rooms room_type_double
        """
        self.pms_property_demo = self.env["pms.property"].create(
            {
                "name": "Property Based on Comapany Demo",
                "company_id": self.env.ref("base.main_company").id,
                "default_pricelist_id": self.env.ref("product.list0").id,
            }
        )
        # create room type
        self.demo_room_type_double = self.env["pms.room.type"].create(
            {
                "pms_property_ids": [self.pms_property_demo.id],
                "name": "Double Test",
                "default_code": "Demo_DBL_Test",
                "class_id": self.room_type_class1.id,
                "list_price": 25,
            }
        )
        # create rooms
        self.double1 = self.env["pms.room"].create(
            {
                "pms_property_id": self.pms_property_demo.id,
                "name": "Double 101",
                "room_type_id": self.demo_room_type_double.id,
                "capacity": 2,
            }
        )
        self.double2 = self.env["pms.room"].create(
            {
                "pms_property_id": self.pms_property_demo.id,
                "name": "Double 102",
                "room_type_id": self.demo_room_type_double.id,
                "capacity": 2,
            }
        )
        # make current journals payable
        journals = self.env["account.journal"].search(
            [
                ("type", "in", ["bank", "cash"]),
            ]
        )
        journals.mapped("inbound_payment_method_line_ids").allowed_on_pms = True

    def test_autoinvoice_folio_checkout_property_policy(self):
        """
        Test create and invoice the cron by property preconfig automation
        --------------------------------------
        Set property default_invoicing_policy to checkout with 0 days with
        margin, and check that the folio autoinvoice date is set to last checkout
        folio date
        """
        # ARRANGE
        self.property.default_invoicing_policy = "checkout"
        self.property.margin_days_autoinvoice = 0

        # ACT
        self.reservation1 = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today(),
                "checkout": datetime.date.today() + datetime.timedelta(days=3),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        # ASSERT
        self.assertIn(
            datetime.date.today() + datetime.timedelta(days=3),
            self.reservation1.folio_id.mapped("sale_line_ids.autoinvoice_date"),
            "The autoinvoice date in folio with property checkout policy is wrong",
        )

    def test_autoinvoice_folio_checkout_partner_policy(self):
        """
        Test create and invoice the cron by partner preconfig automation
        --------------------------------------
        Set partner invoicing_policy to checkout with 2 days with
        margin, and check that the folio autoinvoice date is set to last checkout
        folio date + 2 days
        """
        # ARRANGE
        self.partner_id.invoicing_policy = "checkout"
        self.partner_id.margin_days_autoinvoice = 2

        # ACT
        self.reservation1 = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today(),
                "checkout": datetime.date.today() + datetime.timedelta(days=3),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        self.reservation1.reservation_line_ids.default_invoice_to = self.partner_id

        # ASSERT
        self.assertEqual(
            datetime.date.today() + datetime.timedelta(days=5),
            self.reservation1.folio_id.sale_line_ids.filtered(
                lambda r: r.invoice_status == "to_invoice"
            )[0].autoinvoice_date,
            "The autoinvoice date in folio with property checkout policy is wrong",
        )

    def test_autoinvoice_paid_folio_overnights_partner_policy(self):
        """
        Test create and invoice the cron by partner preconfig automation
        with partner setted as default invoiced to in reservation lines
        --------------------------------------
        Set partner invoicing_policy to checkout, create a reservation
        with room, board service and normal service, run autoinvoicing
        method and check that only room and board service was invoiced
        in partner1, the folio must be paid

        """
        # ARRANGE
        self.create_configuration_accounting_scenario()
        self.partner_id2 = self.env["res.partner"].create(
            {
                "name": "Sara",
                "vat": "54235544A",
                "country_id": self.env.ref("base.es").id,
                "city": "Madrid",
                "zip": "28013",
                "street": "Street 321",
            }
        )
        self.partner_id.invoicing_policy = "checkout"
        self.partner_id.margin_days_autoinvoice = 0
        self.product1 = self.env["product.product"].create(
            {
                "name": "Test Product 1",
            }
        )

        self.product2 = self.env["product.product"].create(
            {
                "name": "Test Product 2",
                "lst_price": 100,
            }
        )

        self.board_service1 = self.env["pms.board.service"].create(
            {
                "name": "Test Board Service 1",
                "default_code": "CB1",
                "amount": 10,
            }
        )

        self.board_service_line1 = self.env["pms.board.service.line"].create(
            {
                "product_id": self.product1.id,
                "pms_board_service_id": self.board_service1.id,
                "amount": 10,
                "adults": True,
            }
        )

        self.board_service_room_type1 = self.env["pms.board.service.room.type"].create(
            {
                "pms_room_type_id": self.demo_room_type_double.id,
                "pms_board_service_id": self.board_service1.id,
                "pms_property_id": self.pms_property_demo.id,
            }
        )
        # ACT
        self.reservation1 = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.pms_property_demo.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=3),
                "checkout": datetime.date.today(),
                "adults": 2,
                "room_type_id": self.demo_room_type_double.id,
                "partner_id": self.partner_id2.id,
                "board_service_room_id": self.board_service_room_type1.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        self.service = self.env["pms.service"].create(
            {
                "is_board_service": False,
                "product_id": self.product2.id,
                "reservation_id": self.reservation1.id,
            }
        )
        folio = self.reservation1.folio_id
        reservation1 = self.reservation1
        reservation1.reservation_line_ids.default_invoice_to = self.partner_id
        reservation1.service_ids.filtered(
            "is_board_service"
        ).default_invoice_to = self.partner_id

        payment_method_line = (
            reservation1.folio_id.pms_property_id._get_payment_methods()[:1]
        )
        folio.do_payment(
            payment_method_line=payment_method_line,
            user=self.env.user,
            amount=reservation1.folio_id.pending_amount,
            folio=folio,
            partner=reservation1.partner_id,
            date=fields.date.today(),
        )
        self.pms_property_demo.autoinvoicing()

        # ASSERT
        overnight_sale_lines = self.reservation1.folio_id.sale_line_ids.filtered(
            lambda line: line.reservation_line_ids or line.is_board_service
        )
        partner_invoice = self.reservation1.folio_id.move_ids.filtered(
            lambda inv: inv.partner_id == self.partner_id
        )
        self.assertEqual(
            partner_invoice.mapped("line_ids.folio_line_ids.id"),
            overnight_sale_lines.ids,
            "Billed services and overnights invoicing wrong compute",
        )

    def test_autoinvoice_folio_keeps_reservation_sections(self):
        """
        Test that the automatic invoice keeps the reservation sections
        --------------------------------------
        Set property default_invoicing_policy to checkout with 0 days of
        margin, create a reservation already checked out and run the
        autoinvoicing. The created invoice must include the section line of
        the reservation, whose name carries the room that the client checks
        the invoice against.
        """
        # ARRANGE
        self.property.default_invoicing_policy = "checkout"
        self.property.margin_days_autoinvoice = 0
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=3),
                "checkout": datetime.date.today(),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        # ACT
        self.property.autoinvoicing()

        # ASSERT
        invoice_sections = reservation.folio_id.move_ids.line_ids.filtered(
            lambda line: line.display_type == "line_section"
        )
        self.assertEqual(
            invoice_sections.folio_line_ids,
            reservation.sale_line_ids.filtered(
                lambda line: line.display_type == "line_section"
            ),
            "The automatic invoice must include the reservation section",
        )
        self.assertIn(
            reservation.rooms,
            invoice_sections.name,
            "The invoice section must show the rooms of the reservation",
        )

    def test_manual_invoice_ignores_checkout_invoicing_policy(self):
        """
        Test that manual invoicing does not date the invoice on the checkout
        --------------------------------------
        Set property default_invoicing_policy to checkout with 0 days of
        margin, create a reservation checked out a week ago and invoice its
        folio manually (no autoinvoice context, like the backend wizard and
        the app do). The draft invoice must not take the checkout as its
        date: it stays empty so that the invoice is dated the day it is
        posted, instead of being booked in a past period.
        """
        # ARRANGE
        self.property.default_invoicing_policy = "checkout"
        self.property.margin_days_autoinvoice = 0
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=10),
                "checkout": datetime.date.today() - datetime.timedelta(days=7),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        # ACT
        invoices = reservation.folio_id._create_invoices()

        # ASSERT
        self.assertFalse(
            invoices.invoice_date,
            "A manually created invoice must not be dated on the checkout",
        )
        self.assertNotEqual(
            invoices.invoice_date_due,
            reservation.checkout,
            "A manually created invoice must not be due on the checkout",
        )

    def test_manual_invoice_keeps_the_requested_date(self):
        """
        Test that manual invoicing honours the date asked for by the caller
        --------------------------------------
        Same property policy as above, but the caller (the app sends the date
        chosen by the user) asks for an explicit invoice date: that date must
        be the one used, not the checkout of the reservation.
        """
        # ARRANGE
        self.property.default_invoicing_policy = "checkout"
        self.property.margin_days_autoinvoice = 0
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=10),
                "checkout": datetime.date.today() - datetime.timedelta(days=7),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        invoice_date = datetime.date.today() - datetime.timedelta(days=1)

        # ACT
        invoices = reservation.folio_id._create_invoices(date=invoice_date)

        # ASSERT
        self.assertEqual(
            invoices.invoice_date,
            invoice_date,
            "The manually created invoice must keep the requested date",
        )

    def test_autoinvoice_dates_the_invoice_on_the_checkout(self):
        """
        Test that the automatic invoicing keeps applying the policy date
        --------------------------------------
        The invoicing policy of the property (checkout + margin days) is the
        one that dates the invoices issued by the autoinvoicing cron, so that
        every stay is invoiced in the period it was consumed.
        """
        # ARRANGE
        self.property.default_invoicing_policy = "checkout"
        self.property.margin_days_autoinvoice = 0
        reservation = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=10),
                "checkout": datetime.date.today() - datetime.timedelta(days=7),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )

        # ACT
        invoices = reservation.folio_id.with_context(autoinvoice=True)._create_invoices(
            grouped=True, final=False
        )

        # ASSERT
        self.assertEqual(
            invoices.invoice_date,
            reservation.checkout,
            "The automatic invoice must be dated on the checkout of the stay",
        )

    def test_not_autoinvoice_unpaid_cancel_folio_partner_policy(self):
        """
        Test create and invoice the cron by partner preconfig automation
        --------------------------------------
        Set partner invoicing_policy to checkout, create a reservation
        with room, board service and normal service, run autoinvoicing
        method and check that not invoice was created becouse
        the folio is cancel and not paid
        """
        # ARRANGE
        self.partner_id.invoicing_policy = "checkout"
        self.partner_id.margin_days_autoinvoice = 0
        self.product1 = self.env["product.product"].create(
            {
                "name": "Test Product 1",
            }
        )

        self.product2 = self.env["product.product"].create(
            {
                "name": "Test Product 2",
                "lst_price": 100,
            }
        )

        self.board_service1 = self.env["pms.board.service"].create(
            {
                "name": "Test Board Service 1",
                "default_code": "CB1",
                "amount": 10,
            }
        )

        self.board_service_line1 = self.env["pms.board.service.line"].create(
            {
                "product_id": self.product1.id,
                "pms_board_service_id": self.board_service1.id,
                "amount": 10,
                "adults": True,
            }
        )

        self.board_service_room_type1 = self.env["pms.board.service.room.type"].create(
            {
                "pms_room_type_id": self.room_type_double.id,
                "pms_board_service_id": self.board_service1.id,
                "pms_property_id": self.property.id,
            }
        )
        # ACT
        self.reservation1 = self.env["pms.reservation"].create(
            {
                "pms_property_id": self.property.id,
                "checkin": datetime.date.today() - datetime.timedelta(days=3),
                "checkout": datetime.date.today(),
                "adults": 2,
                "room_type_id": self.room_type_double.id,
                "partner_id": self.partner_id.id,
                "board_service_room_id": self.board_service_room_type1.id,
                "sale_channel_origin_id": self.sale_channel_direct1.id,
            }
        )
        self.service = self.env["pms.service"].create(
            {
                "is_board_service": False,
                "product_id": self.product2.id,
                "reservation_id": self.reservation1.id,
            }
        )
        self.reservation1.action_cancel()
        self.property.autoinvoicing()

        # ASSERT
        partner_invoice = self.reservation1.folio_id.move_ids.filtered(
            lambda inv: inv.partner_id == self.partner_id
        )
        self.assertEqual(
            partner_invoice.mapped("line_ids.folio_line_ids.id"),
            [],
            "Billed services and overnights invoicing wrong compute",
        )
