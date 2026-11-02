# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _get_specific_rendering_values(self, processing_values):
        """Send the guest of the booking engine back to it, and show the
        holder of the booking as the holder of the payment."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "redsys" or not self.booking_engine_booking_id:
            return res
        provider = self.provider_id
        booking = self.booking_engine_booking_id
        parameters = provider._url_decode64(res["Ds_MerchantParameters"])
        parameters.update(
            {
                "Ds_Merchant_UrlOk": booking._get_payment_return_url("ok")[:250],
                "Ds_Merchant_UrlKo": booking._get_payment_return_url("ko")[:250],
                "Ds_Merchant_Titular": booking._get_holder_name()[:60],
            }
        )
        encoded = provider._url_encode64(json.dumps(parameters)).decode("utf-8")
        res.update(
            {
                "Ds_MerchantParameters": encoded,
                "Ds_Signature": provider.sign_parameters(
                    provider.redsys_secret_key, encoded
                ),
            }
        )
        return res

    def _process_notification_data(self, data):
        """Keep a booking engine payment done: the Redsys module writes the
        state of any notification, even after a done one. And fail a denied
        one: the module leaves the denials (0101 to 0203) pending, so the
        engine would wait for them forever and never release their hold."""
        if self.provider_code != "redsys" or not self.booking_engine_booking_id:
            return super()._process_notification_data(data)
        if self.state == "done":
            _logger.info(
                "Booking engine: notification of %s ignored, already done",
                self.reference,
            )
            return
        res = super()._process_notification_data(data)
        if self.state == "pending":
            self._set_error(self.state_message)
        return res
