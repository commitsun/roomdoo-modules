from datetime import date

from fastapi import status

from odoo.tests import tagged

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApiPayment


@tagged("post_install", "-at_install")
class TestInternalTransferEndpoints(CommonTestPmsApiPayment):
    """POST/GET/PATCH /internal-transfers."""

    def _transfer_body(self, **overrides):
        body = {
            "amount": 30000.0,
            "date": "2026-03-04",
            "originPaymentMethodId": self.bank_outbound.id,
            "destinationPaymentMethodId": self.bank2_inbound.id,
            "reference": "",
        }
        body.update(overrides)
        return body

    def _create_transfer_via_api(self, test_client, **overrides):
        response = test_client.post(
            "/internal-transfers", json=self._transfer_body(**overrides)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.text)
        return response.json()

    def _transfer_lines_reconciled(self, transfer, counterpart):
        lines = (transfer.move_id.line_ids + counterpart.move_id.line_ids).filtered(
            lambda line: line.account_id == transfer.destination_account_id
        )
        return bool(lines) and all(lines.mapped("reconciled"))

    # -- POST /internal-transfers --

    def test_create_transfer(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            body = self._create_transfer_via_api(
                test_client, reference="Traspaso cierre de caja"
            )
        self.assertEqual(body["paymentType"], "internalTransfer")
        self.assertEqual(body["amount"], 30000.0)
        self.assertEqual(body["reference"], "Traspaso cierre de caja")
        # Both ends of the movement are reported, and nothing that only applies
        # to a payment with a contact.
        self.assertEqual(body["originPaymentMethod"]["id"], self.bank_outbound.id)
        self.assertEqual(body["destinationPaymentMethod"]["id"], self.bank2_inbound.id)
        self.assertNotIn("partner", body)
        self.assertNotIn("folio", body)
        self.assertNotIn("availableRefundAmount", body)
        transfer = self.env["account.payment"].browse(body["id"])
        self.assertTrue(transfer.is_internal_transfer)
        self.assertEqual(transfer.journal_id, self.journal_bank)
        self.assertEqual(transfer.payment_method_line_id, self.bank_outbound)
        self.assertEqual(
            transfer.paired_internal_transfer_payment_id.journal_id, self.journal_bank2
        )

    def test_create_honors_destination_method_line(self):
        """When the destination journal exposes more than one inbound method
        line, the counterpart ends up with the one sent in the payload (not the
        default Odoo would pick)."""
        # Add a second inbound line to the destination journal and select it.
        manual = self.bank2_inbound.payment_method_id
        extra_inbound = self.env["account.payment.method.line"].create(
            {
                "name": "Bank PMS 2 inbound alt",
                "payment_method_id": manual.id,
                "journal_id": self.journal_bank2.id,
            }
        )
        self.assertNotEqual(extra_inbound, self.bank2_inbound)
        with self._create_test_client() as test_client:
            self._login(test_client)
            body = self._create_transfer_via_api(
                test_client, amount=500.0, destinationPaymentMethodId=extra_inbound.id
            )
        self.assertEqual(body["destinationPaymentMethod"]["id"], extra_inbound.id)
        transfer = self.env["account.payment"].browse(body["id"])
        counterpart = transfer.paired_internal_transfer_payment_id
        self.assertEqual(counterpart.payment_method_line_id, extra_inbound)
        self.assertEqual(counterpart.state, "posted")
        self.assertTrue(self._transfer_lines_reconciled(transfer, counterpart))

    def test_create_same_journal_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json=self._transfer_body(
                    amount=100.0, destinationPaymentMethodId=self.bank_inbound.id
                ),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_create_unknown_method_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json=self._transfer_body(destinationPaymentMethodId=999999),
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/record-not-found")

    def test_create_origin_not_outbound_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json=self._transfer_body(originPaymentMethodId=self.bank_inbound.id),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    def test_create_destination_not_inbound_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.post(
                "/internal-transfers",
                json=self._transfer_body(
                    destinationPaymentMethodId=self.bank2_outbound.id
                ),
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["type"], "/errors/validation-error")

    # -- GET /internal-transfers/{id} --

    def test_get_detail(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(test_client, reference="Traspaso")
            response = test_client.get(f"/internal-transfers/{created['id']}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], created["id"])
        self.assertEqual(body["paymentType"], "internalTransfer")
        self.assertEqual(body["reference"], "Traspaso")
        self.assertEqual(body["originPaymentMethod"]["id"], self.bank_outbound.id)
        self.assertEqual(body["destinationPaymentMethod"]["id"], self.bank2_inbound.id)

    def test_get_by_destination_leg_resolves_to_origin(self):
        """A transfer is two paired payments; either id addresses the same
        operation and both answer with the origin leg, so origin and destination
        are unambiguous."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(test_client)
            transfer = self.env["account.payment"].browse(created["id"])
            counterpart = transfer.paired_internal_transfer_payment_id
            self.assertTrue(counterpart)
            response = test_client.get(f"/internal-transfers/{counterpart.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        self.assertEqual(body["id"], transfer.id)
        self.assertEqual(body["originPaymentMethod"]["id"], self.bank_outbound.id)
        self.assertEqual(body["destinationPaymentMethod"]["id"], self.bank2_inbound.id)

    def test_get_customer_payment_returns_404(self):
        """The entity only addresses internal transfers."""
        customer_pay = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get(f"/internal-transfers/{customer_pay.id}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_get_unknown_transfer_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.get("/internal-transfers/999999")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    # -- PATCH /internal-transfers/{id} --

    def _assert_update_syncs_both_legs(self, through_destination_leg, **create):
        """Edit every editable field through one of the two legs and check the
        whole operation ends consistent, whichever leg was addressed.

        The listing exposes both legs with different ids, so the front may hold
        either one; both must drive the same edit.
        """
        with self._create_test_client() as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(
                test_client, reference="old", **create
            )
            transfer = self.env["account.payment"].browse(created["id"])
            counterpart = transfer.paired_internal_transfer_payment_id
            self.assertTrue(counterpart)
            addressed = counterpart if through_destination_leg else transfer
            response = test_client.patch(
                f"/internal-transfers/{addressed.id}",
                json={
                    "amount": 25000.0,
                    "date": "2026-04-10",
                    "reference": "cierre turno",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        body = response.json()
        # Whichever id was addressed, the resource answered is the origin leg.
        self.assertEqual(body["id"], transfer.id)
        self.assertEqual(body["amount"], 25000.0)
        self.assertEqual(body["date"], "2026-04-10")
        self.assertEqual(body["reference"], "cierre turno")
        # Both ends carry the edit and stay posted.
        for leg in (transfer, counterpart):
            self.assertEqual(leg.amount, 25000.0)
            self.assertEqual(leg.date, date(2026, 4, 10))
            self.assertEqual(leg.ref, "cierre turno")
            self.assertEqual(leg.state, "posted")
        # Posting an already-paired transfer does not re-reconcile it, so the
        # helper must do it: check the pair really ended up reconciled again.
        self.assertTrue(self._transfer_lines_reconciled(transfer, counterpart))
        # And the pairing survives the draft/re-post round trip, in both
        # directions, so a later edit through either leg still works.
        self.assertEqual(transfer.paired_internal_transfer_payment_id, counterpart)
        self.assertEqual(counterpart.paired_internal_transfer_payment_id, transfer)

    def test_update_through_origin_leg_syncs_both(self):
        self._assert_update_syncs_both_legs(through_destination_leg=False)

    def test_update_through_destination_leg_syncs_both(self):
        self._assert_update_syncs_both_legs(through_destination_leg=True)

    def test_update_through_destination_leg_of_cash_transfer_syncs_both(self):
        """Same, with cash on the origin end: the edit reopens the cash session
        of the leg that was not addressed."""
        self._assert_update_syncs_both_legs(
            through_destination_leg=True,
            originPaymentMethodId=self.cash_outbound.id,
        )

    def test_second_update_through_the_other_leg_still_syncs(self):
        """Two consecutive edits, each through a different leg."""
        with self._create_test_client() as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(test_client)
            transfer = self.env["account.payment"].browse(created["id"])
            counterpart = transfer.paired_internal_transfer_payment_id
            first = test_client.patch(
                f"/internal-transfers/{transfer.id}", json={"amount": 25000.0}
            )
            self.assertEqual(first.status_code, status.HTTP_200_OK, first.text)
            second = test_client.patch(
                f"/internal-transfers/{counterpart.id}", json={"amount": 15000.0}
            )
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.text)
        self.assertEqual(second.json()["id"], transfer.id)
        self.assertEqual(second.json()["amount"], 15000.0)
        for leg in (transfer, counterpart):
            self.assertEqual(leg.amount, 15000.0)
            self.assertEqual(leg.state, "posted")
        self.assertTrue(self._transfer_lines_reconciled(transfer, counterpart))

    def test_update_payment_method_is_not_expressible(self):
        """The payment methods are not editable: the schema rejects them outright
        instead of the endpoint answering a runtime 422 as the unified PATCH did."""
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(test_client, amount=100.0)
            response = test_client.patch(
                f"/internal-transfers/{created['id']}",
                json={"originPaymentMethodId": self.bank2_outbound.id},
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
        transfer = self.env["account.payment"].browse(created["id"])
        self.assertEqual(transfer.payment_method_line_id, self.bank_outbound)

    def test_update_unknown_transfer_returns_404(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                "/internal-transfers/999999", json={"amount": 10.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(response.json()["type"], "/errors/payment-not-found")

    def test_update_customer_payment_returns_404(self):
        customer_pay = self._create_payment(amount=100.0)
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            response = test_client.patch(
                f"/internal-transfers/{customer_pay.id}", json={"amount": 90.0}
            )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.text)
        self.assertEqual(customer_pay.amount, 100.0)

    def test_update_amount_not_positive_returns_422(self):
        with self._create_test_client(raise_server_exceptions=False) as test_client:
            self._login(test_client)
            created = self._create_transfer_via_api(test_client, amount=100.0)
            response = test_client.patch(
                f"/internal-transfers/{created['id']}", json={"amount": 0.0}
            )
        # 422 literal: 422 is deprecated in
        # newer starlette and the test runner turns the warning into an error.
        self.assertEqual(response.status_code, 422, response.text)
