# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.http import request

from odoo.addons.base_rest import restapi
from odoo.addons.component.core import Component

from ..exceptions import PaymentCardError
from ..models.ir_http import CARD_PASSWORD_HEADER


class PmsFolioService(Component):
    """The payment card of a folio, under ``/api/folios/<id>/payment-card``.

    The answers are built here instead of going through base_rest, for two
    reasons. Errors carry a ``code`` the front tells apart, and they are
    answered rather than raised, so that the request commits the failed
    attempt and the access log it leaves behind. And every answer goes out
    with ``Cache-Control: no-store``, card data included.
    """

    _inherit = "pms.folio.service"

    @restapi.method(
        [(["/<int:folio_id>/payment-card"], "GET")],
        auth="jwt_api_pms",
    )
    def get_folio_payment_card(self, folio_id):
        return self._payment_card_answer(lambda cards: cards._api_summary(folio_id))

    @restapi.method(
        [(["/<int:folio_id>/payment-card/details"], "GET")],
        auth="jwt_api_pms",
    )
    def get_folio_payment_card_details(self, folio_id):
        password = self._payment_card_password()
        return self._payment_card_answer(
            lambda cards: cards._api_details(folio_id, password)
        )

    @restapi.method(
        [(["/<int:folio_id>/payment-card"], "POST")],
        auth="jwt_api_pms",
    )
    def create_folio_payment_card(self, folio_id):
        values = self._payment_card_body()
        return self._payment_card_answer(
            lambda cards: cards._api_create(folio_id, values), status=201
        )

    @restapi.method(
        [(["/<int:folio_id>/payment-card"], "PATCH")],
        auth="jwt_api_pms",
    )
    def update_folio_payment_card(self, folio_id):
        password = self._payment_card_password()
        values = self._payment_card_body()
        return self._payment_card_answer(
            lambda cards: cards._api_update(folio_id, password, values)
        )

    @restapi.method(
        [(["/<int:folio_id>/payment-card"], "DELETE")],
        auth="jwt_api_pms",
    )
    def delete_folio_payment_card(self, folio_id):
        password = self._payment_card_password()
        return self._payment_card_answer(
            lambda cards: cards._api_delete(folio_id, password)
        )

    def _payment_card_password(self):
        return request.httprequest.headers.get(CARD_PASSWORD_HEADER) or ""

    def _payment_card_body(self):
        # A body that is not JSON is rejected later, as invalid card data,
        # so that the attempt is logged like any other
        try:
            return request.get_json_data()
        except ValueError:
            return None

    def _payment_card_answer(self, operation, status=200):
        cards = self.env["pms.folio.payment.card"].with_context(
            payment_card_remote_addr=request.httprequest.remote_addr
        )
        try:
            body = operation(cards)
        except PaymentCardError as error:
            body, status = error.to_json(), error.status
        return request.make_json_response(
            body, headers=[("Cache-Control", "no-store")], status=status
        )
