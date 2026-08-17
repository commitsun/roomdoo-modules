from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.tools import consteq

from odoo.addons.portal.controllers.portal import CustomerPortal

SHARE_TEMPLATE = "roomdoo_precheckin_share.precheckin_share_document"

# Instance branding, borrowed from roomdoo_fastapi: ``instance_name`` is
# admin-editable free text and ``instance_image`` stores an ir.attachment id.
# Read by key rather than through a dependency, because ir.config_parameter is
# a plain key-value store and this module has no other reason to pull the whole
# FastAPI stack in. Without roomdoo_fastapi installed nobody can fill them, so
# the card simply stays generic.
DEFAULT_INSTANCE_NAME = "Roomdoo"
TITLE_SEPARATOR = "·"  # MIDDLE DOT, the separator the SPA ships
TITLE_SUFFIX = "Check-in online"

# Generic card the SPA itself ships in roomdoo-vite/index.html. Reused verbatim
# so a link that does not resolve previews exactly like a direct SPA visit.
FALLBACK_TITLE = f"{DEFAULT_INSTANCE_NAME} {TITLE_SEPARATOR} {TITLE_SUFFIX}"
FALLBACK_IMAGE = "https://docs.roomdoo.com/images/og/og-checkin-online.jpg"
FALLBACK_IMAGE_WIDTH = "1200"
FALLBACK_IMAGE_HEIGHT = "630"

# Why a shared link can no longer be used. A shared link outlives the stay it
# points at: it sits in a WhatsApp thread and gets opened months later.
SHARE_STATE_OK = "ok"
SHARE_STATE_CANCELLED = "cancelled"
SHARE_STATE_PAST = "past"

# Share path segment -> shared record. The share path mirrors the SPA path
# behind a /share prefix, so the redirect target is the very same path with the
# prefix stripped and the optional language segment kept.
SHARE_MODELS = {
    "precheckin": {"model": "pms.folio", "checkout_field": "last_checkout"},
    "precheckin-reservation": {
        "model": "pms.reservation",
        "checkout_field": "checkout",
    },
}


class PrecheckinShare(models.AbstractModel):
    """Open Graph documents for shared pre-check-in links.

    Social crawlers (WhatsApp, Facebook, ...) do not run JavaScript, so the SPA
    cannot set per-link Open Graph tags and every shared pre-check-in URL
    previews as one generic Roomdoo card. This helper builds the values of a
    small HTML document that carries the per-instance tags for crawlers and
    bounces real browsers to the SPA.

    All the logic lives here instead of in the controller so that it stays
    inheritable and unit-testable, and the document itself is a QWeb template
    so that it stays escaped, translatable and inheritable too.
    """

    _name = "roomdoo.precheckin.share"
    _description = "Pre-check-in Share Document"

    @api.model
    def get_share_document(self, kind, record_id, token, lang_segment=None):
        """Build the share document of one pre-check-in link.

        :param str kind: share path segment, see ``_get_share_models``.
        :param record_id: id of the shared folio/reservation.
        :param str token: portal access token, taken from the URL.
        :param lang_segment: optional language segment, taken from the URL.
        :return: ``{"html": <document>, "headers": [(name, value), ...]}``

        Never raises for a bad link: an unknown id, a wrong token or a missing
        instance image all degrade to the generic card, because a broken
        preview is worse than a generic one.
        """
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            record_id = 0
        token = token if isinstance(token, str) else ""
        record = self._resolve_share_target(kind, record_id, token)
        state = self._get_share_state(kind, record)
        url_lang = self._clean_lang_segment(lang_segment)
        lang = self._resolve_lang(url_lang)
        values = self._get_share_card(record, state, url_lang=url_lang, lang=lang)
        values.update(
            redirect_url=self._get_redirect_url(
                kind, record_id, token, url_lang, state=state
            ),
            share_url=self._get_share_url(kind, record_id, token, url_lang),
        )
        html = self.env["ir.qweb"]._render(SHARE_TEMPLATE, values)
        return {"html": str(html), "headers": self._get_share_headers()}

    @api.model
    def get_public_share_url(self, kind, record_id, token, lang_segment=None):
        """Share URL of one pre-check-in link, for whoever hands links to the SPA.

        The share path belongs to this module, so this is the one place that
        knows its shape. A caller building it by hand would be duplicating a
        route it does not own, across the three different origins the SPA runs
        on, and would silently rot the day the route changes.

        No token check here: this only builds a URL, and the endpoint it points
        at validates the token when it is actually visited.

        :return: the absolute URL, or ``""`` when it cannot be built, so that a
            caller can fall back to the plain SPA link.
        """
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return ""
        if not record_id or kind not in self._get_share_models():
            return ""
        if not token or not isinstance(token, str):
            return ""
        url_lang = self._clean_lang_segment(lang_segment)
        return self._get_share_url(kind, record_id, token, url_lang) or ""

    @api.model
    def _get_share_models(self):
        """Share path segment -> shared model and its checkout field.

        A method rather than a bare constant so that another module can
        register an extra share kind without touching the controller. The
        nested dicts are copied so that an override cannot mutate the module
        level default in place.
        """
        return {key: dict(value) for key, value in SHARE_MODELS.items()}

    @api.model
    def _resolve_share_target(self, kind, record_id, token):
        """Return the shared record when ``token`` grants access to it.

        Returns ``None`` on every failure (unknown kind, deleted record,
        missing or wrong token) so the caller falls back to the generic card.
        """
        share_model = self._get_share_models().get(kind)
        if not share_model or not record_id or not token:
            return None
        model_name = share_model["model"]
        record = self.env[model_name].sudo().browse(record_id)
        if not record.exists():
            return None
        # A record with a NULL access_token cannot be matched, and minting one
        # with _portal_ensure_token() would write during a public GET and hand
        # a brand new token to an unauthenticated caller, so it is reported as
        # unknown instead. A guard rather than an everyday path: pms_api_rest
        # mints the token of a reservation in ``create``.
        if not record.access_token:
            return None
        if not self._token_matches(record.access_token, token):
            return None
        if request:
            # _document_check_access re-checks the token in constant time and
            # also honours the model ACLs and record rules. It reads through
            # ``request.env``, so it only works inside an HTTP request; outside
            # one (unit tests, cron) the comparison above is the whole check.
            try:
                CustomerPortal._document_check_access(
                    self, model_name, record.id, access_token=token
                )
            except (AccessError, MissingError):
                return None
        return record

    @api.model
    def _token_matches(self, expected, received):
        """Compare a portal token with a URL segment in constant time.

        ``consteq`` raises TypeError on non-ASCII str and the received value
        comes straight from the request path, so it is filtered out first.
        """
        if not expected or not isinstance(received, str):
            return False
        if not received.isascii():
            return False
        return consteq(expected, received)

    @api.model
    def _get_share_state(self, kind, record):
        """Why the shared link can no longer be used, or ``ok``.

        A link lives in a chat thread far longer than the stay it points at, so
        a months-old link must say what happened instead of previewing as an
        ordinary invitation to check in.

        An unresolved record is reported as ``ok``: the generic card makes no
        claim about any booking, so it must never announce a cancellation it
        cannot back.
        """
        if not record:
            return SHARE_STATE_OK
        if record.state == "cancel":
            return SHARE_STATE_CANCELLED
        if self._is_past(kind, record):
            return SHARE_STATE_PAST
        return SHARE_STATE_OK

    @api.model
    def _is_past(self, kind, record):
        """Whether the stay is over, in the timezone of its own property.

        ``fields.Date.today()`` is UTC, which moves the checkout boundary by a
        whole day for a property far enough from it, and being a day early
        would lock a guest out on their own checkout day. A folio with no
        reservation has no checkout and is never past.
        """
        checkout = record[self._get_share_models()[kind]["checkout_field"]]
        if not checkout:
            return False
        localized = record.with_context(tz=record.pms_property_id.tz or "UTC")
        return checkout < fields.Date.context_today(localized)

    @api.model
    def _get_instance_name(self):
        """Name of the instance, the group running this Odoo.

        It brands the card through ``og:site_name``, above the title. The title
        itself names the hotel, which is what the guest actually booked and
        recognises.
        """
        instance_name = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("roomdoo_fastapi.instance_name", default=DEFAULT_INSTANCE_NAME)
        )
        return (instance_name or "").strip() or DEFAULT_INSTANCE_NAME

    @api.model
    def _get_card_image_url(self, record):
        """Public absolute URL of the image the card shows, or ``None``.

        The hotel the guest booked comes first: a picture of their own hotel is
        worth more than a group logo. The instance image is the fallback, and
        the caller falls back again to the bundled illustration.
        """
        return self._get_property_image_url(record) or self._get_instance_image_url()

    @api.model
    def _get_property_image_field(self):
        """Field of ``pms.property`` holding the picture of the hotel.

        The same one the SPA shows on the pre-check-in page, so the preview and
        the page agree.
        """
        return "hotel_image_pms_api_rest"

    @api.model
    def _get_property_image_url(self, record):
        """Public absolute URL of the image of the shared hotel, or ``None``.

        The field belongs to pms_api_rest, which this module deliberately does
        not depend on: that module is on its way out and dragging it in for one
        image would undo the point of keeping this endpoint standalone. So it
        is read only when present, and its absence is just a missing image.
        """
        pms_property = record.pms_property_id if record else None
        field_name = self._get_property_image_field()
        if not pms_property or field_name not in pms_property._fields:
            return None
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", pms_property._name),
                    ("res_id", "=", pms_property.id),
                    ("res_field", "=", field_name),
                ],
                limit=1,
            )
        )
        return self._get_attachment_url(attachment)

    @api.model
    def _get_instance_image_url(self):
        """Public absolute URL of the configured instance image, or ``None``.

        The config parameter holds an ir.attachment id, and the attachment may
        already be gone while the parameter still points at it.
        """
        image_param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("roomdoo_fastapi.instance_image")
        )
        if not image_param:
            return None
        try:
            attachment_id = int(image_param)
        except (TypeError, ValueError):
            return None
        return self._get_attachment_url(
            self.env["ir.attachment"].sudo().browse(attachment_id)
        )

    @api.model
    def _get_attachment_url(self, attachment):
        """Absolute URL an anonymous crawler can fetch ``attachment`` at."""
        if not attachment:
            return None
        try:
            if not attachment.datas:
                return None
        except MissingError:
            return None
        base_url = self._get_base_url()
        if not base_url:
            return None
        # Generating the access token is what makes the image fetchable by an
        # anonymous crawler, and it is the one write this public GET performs.
        if not attachment.access_token:
            attachment.generate_access_token()
        return (
            f"{base_url}/web/image/{attachment.id}"
            f"?access_token={attachment.access_token}"
        )

    @api.model
    def _get_base_url(self):
        """Absolute origin of this Odoo, or ``None`` when it is not usable.

        Both the instance image and ``og:url`` are built on it, and it comes
        from admin-editable configuration, so it is validated once here.
        """
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        base_url = (base_url or "").strip().rstrip("/")
        return base_url if self._is_absolute_http_url(base_url) else None

    @api.model
    def _is_absolute_http_url(self, url):
        """Whether ``url`` is an absolute http(s) URL.

        Both the image URL and the redirect target come from admin-editable
        configuration and end up inside the document, so anything that is not
        a plain absolute http(s) URL (a relative path, a ``javascript:``
        scheme) is rejected.
        """
        return isinstance(url, str) and url.lower().startswith(("http://", "https://"))

    @api.model
    def _get_app_url(self):
        """Origin of the SPA, or ``None`` when it is not usable.

        ``roomdoo_app_url`` ships with the literal value ``False`` (see
        pms_api_rest/data/roomdoo_data.xml) and is only replaced by the install
        hook, so that placeholder must never reach the document as a URL.
        """
        app_url = self.env["ir.config_parameter"].sudo().get_param("roomdoo_app_url")
        app_url = (app_url or "").strip().rstrip("/")
        if app_url.lower() in ("", "false", "none"):
            return None
        return app_url if self._is_absolute_http_url(app_url) else None

    @api.model
    def _get_share_path(self, kind, record_id, token, url_lang=None):
        """Return ``<id>/<kind>/<token>[/<lang>]`` with every segment quoted.

        Quoting keeps a hostile token inside its own path segment; a real
        portal token is a uuid, so it comes out unchanged.
        """
        segments = [str(record_id), quote(kind or "", safe=""), quote(token, safe="")]
        if url_lang:
            segments.append(quote(url_lang, safe=""))
        return "/".join(segments)

    @api.model
    def _get_redirect_url(self, kind, record_id, token, url_lang=None, state=None):
        """SPA URL the browser is bounced to, or ``None`` when unavailable.

        A link that is no longer usable is never forwarded. The public API
        applies no expiry of its own, so the SPA would happily render the
        pre-check-in form of a cancelled booking or of a stay that is over; the
        guest is told here instead.

        Built only from the ``roomdoo_app_url`` parameter and the validated
        path components, never from a request header, a query string or the
        referrer, so the document cannot turn into an open redirect.
        """
        if state not in (None, SHARE_STATE_OK):
            return None
        app_url = self._get_app_url()
        if not app_url:
            return None
        return f"{app_url}/{self._get_share_path(kind, record_id, token, url_lang)}"

    @api.model
    def _get_share_url(self, kind, record_id, token, url_lang=None):
        """Absolute URL of this very document, used as ``og:url``.

        Pointing ``og:url`` at the share URL keeps the preview stable when a
        crawler re-fetches it. It does embed the access token, which is
        acceptable: the share URL carries the same token as the SPA link the
        guest already received, so it exposes nothing new.
        """
        base_url = self._get_base_url()
        if not base_url:
            return None
        path = self._get_share_path(kind, record_id, token, url_lang)
        return f"{base_url}/share/{path}"

    @api.model
    def _clean_lang_segment(self, lang_segment):
        """Return the language segment to keep in the URLs, or ``None``.

        Validated against what the SPA route accepts (``/:lang([a-z]{2})?``)
        and never against the languages installed in this Odoo. The SPA ships
        seven locales and a tenant activates whichever subset it needs, so
        gating on ``res.lang`` would drop the segment of a link shared in, say,
        Catalan and land the guest in the default language instead — the very
        regression this endpoint exists to avoid.

        A segment this Odoo does not know still travels: the SPA owns its own
        locale fallback, exactly as it owns the 404 of an unknown id.
        """
        candidate = (lang_segment or "").strip().lower()
        candidate = candidate.replace("-", "_").split("_")[0]
        if len(candidate) != 2 or not candidate.isascii() or not candidate.isalpha():
            return None
        return candidate

    @api.model
    def _resolve_lang(self, url_lang):
        """Return the active ``res.lang`` of a segment, possibly empty.

        Only used to translate the card text. A language this Odoo does not
        have leaves the card in the default language, which is a cosmetic
        mismatch; the redirect keeps the segment either way, which is what
        actually decides where the guest lands.
        """
        lang_model = self.env["res.lang"].sudo()
        if not url_lang:
            return lang_model.browse()
        return lang_model.search(
            [
                ("active", "=", True),
                "|",
                ("code", "=", url_lang),
                ("iso_code", "=", url_lang),
            ],
            limit=1,
        )

    @api.model
    def _get_share_state_labels(self):
        """Title suffix and description of every share state.

        The instance brand stays in the title and only the suffix changes, so a
        guest reading a dead link still sees who it came from.

        Call it on a recordset carrying the wanted language in the context:
        ``_()`` resolves the language from ``self.env.lang``.
        """
        return {
            SHARE_STATE_OK: {
                "suffix": TITLE_SUFFIX,
                "description": _("Complete your online check-in before you arrive."),
            },
            SHARE_STATE_CANCELLED: {
                "suffix": _("Booking cancelled"),
                "description": _(
                    "This booking was cancelled, so its online check-in is no "
                    "longer available."
                ),
            },
            SHARE_STATE_PAST: {
                "suffix": _("Stay completed"),
                "description": _(
                    "This stay has already ended, so its online check-in is no "
                    "longer available."
                ),
            },
        }

    @api.model
    def _get_share_link_label(self):
        """Label of the visible link, for browsers that do not bounce."""
        return _("Continue to your online check-in")

    @api.model
    def _get_share_card(self, record, state, url_lang=None, lang=None):
        """Return every already-translated value the document renders.

        :param record: the resolved shared record, or a falsy value when the
            link did not resolve.
        :param str state: share state, see ``_get_share_state``.
        :param url_lang: two-letter segment of the URL, may be ``None``.
        :param lang: ``res.lang`` the card text is rendered in, may be empty
            even when ``url_lang`` is set.
        """
        localized = self.with_context(lang=lang.code) if lang else self
        labels = localized._get_share_state_labels()[state]
        instance_name = self._get_instance_name()
        if record:
            # The hotel goes in the title and the group above it, in
            # og:site_name. The guest booked a hotel, not a group, and a
            # forwarded link arrives with no accompanying message to name it.
            hotel_name = (record.pms_property_id.name or "").strip()
            title = f"{hotel_name or instance_name} {TITLE_SEPARATOR} "
            title += labels["suffix"]
            image = self._get_card_image_url(record) or FALLBACK_IMAGE
            site_name = instance_name
        else:
            title = FALLBACK_TITLE
            image = FALLBACK_IMAGE
            # Nothing resolved, so there is no group to claim the card for.
            site_name = None
        card = {
            # Not ``lang``: ir.qweb overwrites that key with the environment
            # language on every render (see its values.update), so the document
            # would silently declare en_US no matter what the URL asked for.
            "html_lang": url_lang,
            "title": title,
            "site_name": site_name,
            "description": labels["description"],
            "link_label": localized._get_share_link_label(),
            "image": image,
            "image_width": None,
            "image_height": None,
        }
        # Only the bundled fallback image has a known ratio. Declaring a size
        # for a tenant logo of unknown dimensions makes crawlers crop the card.
        if image == FALLBACK_IMAGE:
            card["image_width"] = FALLBACK_IMAGE_WIDTH
            card["image_height"] = FALLBACK_IMAGE_HEIGHT
        return card

    @api.model
    def _get_share_headers(self):
        """Headers of the share response.

        Always answered with a 200, including for a cancelled or finished
        stay: crawlers render no preview at all for an error status, and the
        card that says the link is dead is the whole point of this document.

        Crawlers re-fetch the URL every time the link is shared, and the
        document is cheap but not free, hence the short cache window.
        """
        return [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "public, max-age=300"),
        ]
