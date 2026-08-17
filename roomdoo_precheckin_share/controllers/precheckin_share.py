from odoo import http
from odoo.http import request

SHARE_HELPER_MODEL = "roomdoo.precheckin.share"

FOLIO_SHARE_ROUTES = [
    "/share/<int:folio_id>/precheckin/<string:token>",
    "/share/<int:folio_id>/precheckin/<string:token>/<string:lang>",
]
RESERVATION_SHARE_ROUTES = [
    "/share/<int:reservation_id>/precheckin-reservation/<string:token>",
    "/share/<int:reservation_id>/precheckin-reservation/<string:token>/<string:lang>",
]


class PrecheckinShare(http.Controller):
    """Serve Open Graph documents for shared pre-check-in links.

    A plain ``http.Controller`` on purpose, and nothing here is FastAPI: this
    serves an HTML document to a crawler, not JSON to the SPA.

    It also must not hang off ``/pmsApi``. That prefix is not a constant but
    the ``root_path`` field of a ``fastapi.endpoint`` record, edited per
    instance from the Odoo UI. These URLs are pasted into WhatsApp threads and
    opened months later, so an admin editing a settings field must not be able
    to break every link ever shared. A route declared in code breaks in a
    deploy, where it is visible.

    The share path mirrors the SPA path behind a ``/share`` prefix, so the
    redirect target is the same path with the prefix stripped and the optional
    language segment preserved for free.
    """

    @http.route(
        FOLIO_SHARE_ROUTES,
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def share_folio_precheckin(self, folio_id, token, lang=None, **kwargs):
        """Open Graph document of a folio pre-check-in link."""
        return self._share_response("precheckin", folio_id, token, lang)

    @http.route(
        RESERVATION_SHARE_ROUTES,
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def share_reservation_precheckin(self, reservation_id, token, lang=None, **kwargs):
        """Open Graph document of a reservation pre-check-in link."""
        return self._share_response(
            "precheckin-reservation", reservation_id, token, lang
        )

    def _share_response(self, kind, record_id, token, lang):
        """Delegate to the helper model and wrap its output in a response.

        Deliberately thin: everything else lives in the
        ``roomdoo.precheckin.share`` AbstractModel so that it stays
        inheritable and unit-testable. Nothing is logged here, because the
        token must never reach a log line.
        """
        document = request.env[SHARE_HELPER_MODEL].get_share_document(
            kind, record_id, token, lang_segment=lang
        )
        return request.make_response(document["html"], document["headers"])
