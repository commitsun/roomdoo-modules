from datetime import date, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.pms_ai_tool.tools import PARAM_ENVIRONMENT

GUEST_EMAIL = "jane.guest@example.com"
GUEST_MOBILE = "+34 600 111 222"


@tagged("post_install", "-at_install")
class SupportToolsCase(TransactionCase):
    """A folio with reservations, payments, an invoice and notification logs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env["ir.config_parameter"].sudo().set_param(PARAM_ENVIRONMENT, "test")
        # The rules of the notification module would log the folios we create.
        cls.env["pms.property.notification.rule"].search([]).write({"active": False})
        cls._setup_company_data()
        cls._setup_users()
        with freeze_time("2026-09-01"):
            cls._setup_folio()
        cls._setup_payments_and_invoices()
        cls._setup_notifications()
        cls.env.invalidate_all()

    @classmethod
    def _setup_company_data(cls):
        plan = cls.env["pms.availability.plan"].create({"name": "AI plan"})
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "AI pricelist",
                "availability_plan_id": plan.id,
                "is_pms_available": True,
            }
        )
        for other in cls.env["product.pricelist"].search([]):
            if not other.availability_plan_id:
                other.write({"availability_plan_id": plan.id, "is_pms_available": True})
        cls.prop, cls.other_prop = cls.env["pms.property"].create(
            [
                {
                    "name": name,
                    "company_id": cls.env.company.id,
                    "default_pricelist_id": pricelist.id,
                }
                for name in ("AI support hotel", "AI other hotel")
            ]
        )
        room_class = cls.env["pms.room.type.class"].create(
            {"name": "AI class", "default_code": "AIC"}
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [(6, 0, (cls.prop | cls.other_prop).ids)],
                "name": "AI double",
                "default_code": "AID",
                "class_id": room_class.id,
            }
        )
        cls.env["pms.room"].create(
            [
                {
                    "pms_property_id": prop.id,
                    "name": f"Room {n}",
                    "short_name": f"R{n}",
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
                for prop in (cls.prop, cls.other_prop)
                for n in range(4)
            ]
        )
        cls.channel = cls.env["pms.sale.channel"].create(
            {"name": "AI online", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "AI agency",
                "is_company": True,
                "is_agency": True,
                "sale_channel_id": cls.channel.id,
            }
        )
        cls.guest = cls.env["res.partner"].create(
            {"name": "Jane Guest", "email": GUEST_EMAIL, "mobile": GUEST_MOBILE}
        )

    @classmethod
    def _setup_users(cls):
        common = {"pms_property_ids": [(6, 0, cls.prop.ids)]}
        cls.user = new_test_user(
            cls.env,
            login="ai_support",
            groups="base.group_user,pms.group_pms_user,account.group_account_invoice",
            pms_property_id=cls.prop.id,
            tz="Europe/Madrid",
            **common,
        )
        # Reads folios but has no accounting rights.
        cls.limited = new_test_user(
            cls.env,
            login="ai_support_limited",
            groups="base.group_user,pms.group_pms_user",
            pms_property_id=cls.prop.id,
            **common,
        )
        cls.both = new_test_user(
            cls.env,
            login="ai_support_both",
            groups="base.group_user,pms.group_pms_user,account.group_account_invoice",
            pms_property_ids=[(6, 0, (cls.prop | cls.other_prop).ids)],
            pms_property_id=cls.prop.id,
        )
        cls.stranger = new_test_user(
            cls.env,
            login="ai_support_stranger",
            groups="base.group_user,pms.group_pms_user,account.group_account_invoice",
            pms_property_ids=[(6, 0, cls.other_prop.ids)],
            pms_property_id=cls.other_prop.id,
        )

    @classmethod
    def _reservation(cls, prop, nights, **vals):
        reservation = cls.env["pms.reservation"].create(
            dict(
                {
                    "pms_property_id": prop.id,
                    "room_type_id": cls.room_type.id,
                    "partner_id": cls.guest.id,
                    "sale_channel_origin_id": cls.channel.id,
                    "reservation_line_ids": [
                        (0, 0, {"date": date(2026, 9, day)}) for day, _price in nights
                    ],
                },
                **vals,
            )
        )
        for line, (_day, price) in zip(
            reservation.reservation_line_ids.sorted("date"), nights, strict=True
        ):
            line.write({"price": price, "discount": 0})
        return reservation

    @classmethod
    def _setup_folio(cls):
        cls.res1 = cls._reservation(
            cls.prop, [(22, 100), (23, 100)], agency_id=cls.agency.id
        )
        cls.folio = cls.res1.folio_id
        cls.res2 = cls._reservation(cls.prop, [(22, 80)], folio_id=cls.folio.id)
        cls.res3 = cls._reservation(cls.prop, [(25, 50)], folio_id=cls.folio.id)
        cls.res3.action_cancel()
        cls.res1.action_confirm()
        cls.res2.action_confirm()
        board_service = cls.env["pms.board.service"].create(
            {
                "name": "AI breakfast",
                "default_code": "AIB",
                "pms_property_ids": [(6, 0, cls.prop.ids)],
            }
        )
        cls.res2.board_service_room_id = cls.env["pms.board.service.room.type"].create(
            {
                "pms_room_type_id": cls.room_type.id,
                "pms_board_service_id": board_service.id,
                "pms_property_id": cls.prop.id,
            }
        )
        cls.other_folio = cls._reservation(cls.other_prop, [(22, 90)]).folio_id
        # Each property numbers its folios on its own: tell the two apart.
        cls.other_folio.name = "OTHER/26/000001"

    @classmethod
    def _setup_payments_and_invoices(cls):
        Payment = cls.env["account.payment"]
        company = cls.env.company
        bank = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
        )
        cash = cls.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", company.id)], limit=1
        )
        cls.bank_journal, cls.cash_journal = bank, cash

        def payment(amount, payment_type="inbound", post=True):
            pay = Payment.create(
                {
                    "payment_type": payment_type,
                    "partner_type": "customer",
                    "partner_id": cls.guest.id,
                    "amount": amount,
                    "journal_id": bank.id,
                    "date": "2026-09-22",
                    "folio_ids": [(6, 0, cls.folio.ids)],
                }
            )
            if post:
                pay.action_post()
            return pay

        payment(150.0)
        payment(20.0, "outbound")
        payment(999.0, post=False)
        statement_line = cls.env["account.bank.statement.line"].create(
            {
                "journal_id": cash.id,
                "date": "2026-09-22",
                "payment_ref": "AI cash",
                "amount": 40.0,
                "folio_ids": [(6, 0, cls.folio.ids)],
            }
        )
        if statement_line.move_id.state != "posted":
            statement_line.move_id.action_post()
        cls.invoice = cls._invoice("out_invoice", 100.0)
        cls.refund = cls._invoice("out_refund", 30.0)
        cls.env.flush_all()
        for move in cls.invoice | cls.refund:
            cls.env.cr.execute(
                """INSERT INTO account_move_folio_ids_rel
                       (account_move_id, folio_ids_id) VALUES (%s, %s)
                   ON CONFLICT DO NOTHING""",
                [move.id, cls.folio.id],
            )

    @classmethod
    def _invoice(cls, move_type, amount):
        move = cls.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": cls.guest.id,
                "invoice_date": "2026-09-23",
                "invoice_payment_term_id": False,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "AI line",
                            "quantity": 1,
                            "price_unit": amount,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _setup_notifications(cls):
        folio_model = cls.env["ir.model"]._get("pms.folio")
        mail_template = cls.env["mail.template"].create(
            {
                "name": "AI support mail",
                "model_id": folio_model.id,
                "subject": "AI",
                "body_html": "<p>AI</p>",
            }
        )
        cls.template = cls.env["pms.notification.template"].create(
            {
                "name": "AI confirmation",
                "code": "ai_support_confirmation",
                "model_id": folio_model.id,
                "mail_template_id": mail_template.id,
            }
        )
        cls.rule = cls.env["pms.property.notification.rule"].create(
            {
                "name": "AI confirmation rule",
                "active": False,
                "template_id": cls.template.id,
                "target_model_id": folio_model.id,
                "rule_type": "event",
                "event_type": "on_create",
                "event_domain": "[]",
                "channel": "email",
            }
        )
        now = fields.Datetime.now()

        def log(state, folio=None, origin="pms.folio", origin_id=None, **vals):
            record = folio or cls.folio
            return cls.env["pms.notification.log"].create(
                dict(
                    {
                        "name": f"AI log {state}",
                        "state": state,
                        "template_id": cls.template.id,
                        "rule_id": cls.rule.id,
                        "property_id": record.pms_property_id.id,
                        "origin_model": origin,
                        "origin_res_id": origin_id or record.id,
                    },
                    **vals,
                )
            )

        cls.log_sent = log(
            "sent",
            sent_date=now - timedelta(hours=30),
            recipient_mode="emails",
            recipient_emails=GUEST_EMAIL,
        )
        cls.log_error = log(
            "error",
            error_message="BookAI API request failed with status 422: "
            f"{{'detail': 'Invalid phone +34600111222 for {GUEST_EMAIL}'}}",
        )
        cls.log_disabled = log(
            "skipped", error_message="BookAI disabled for property 'AI support hotel'."
        )
        cls.log_manual = log(
            "skipped",
            error_message="BookAI is in MANUAL mode for property 'AI support hotel'; "
            "rule sending is disabled.",
        )
        cls.log_late = log("skipped")
        cls.log_waiting = log("pending", scheduled_date=now + timedelta(days=2))
        cls.log_overdue = log("pending", scheduled_date=now - timedelta(hours=5))
        cls.log_cancelled = log("cancelled", rule_id=False)
        cls.log_reservation = log(
            "sent", origin="pms.reservation", origin_id=cls.res1.id
        )
        cls.log_other = log("sent", folio=cls.other_folio)

    def _run(self, tool, user=None, **kwargs):
        return (
            self.env.ref(f"pms_ai_tool_support.ai_tool_{tool}")
            .with_user(user or self.user)
            ._execute_tool(**kwargs)
        )
