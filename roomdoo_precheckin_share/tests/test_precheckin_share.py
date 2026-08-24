import base64
import os
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tools.translate import code_translations

from odoo.addons.pms.tests.common import TestPms

dir_path = os.path.dirname(os.path.realpath(__file__))

APP_URL = "https://tenant.roomdoo.com"
BASE_URL = "https://tenant.odoo.example"
# The group brands og:site_name; the hotel brands the title.
INSTANCE_NAME = "Hotel Test Group"
HOTEL_NAME = "Hotel Share Test"
HOTEL_TITLE = f"{HOTEL_NAME} · Check-in online"
CANCELLED_TITLE = f"{HOTEL_NAME} · Booking cancelled"
PAST_TITLE = f"{HOTEL_NAME} · Stay completed"
GENERIC_TITLE = "Roomdoo · Check-in online"
FALLBACK_IMAGE = "https://docs.roomdoo.com/images/og/og-checkin-online.jpg"
SPANISH_DESCRIPTION = "Completa tu check-in online antes de llegar."
SPANISH_PAST_DESCRIPTION = (
    "Esta estancia ya ha terminado, así que su check-in online ya no está disponible."
)


@tagged("post_install", "-at_install")
class TestPrecheckinShare(TestPms):
    """Behaviour of the pre-check-in share document helper.

    The helper is exercised directly instead of through HTTP: it holds every
    decision the controller delegates, and this repo has no HttpCase suite.

    ``post_install`` because this module only depends on pms and portal, so it
    loads early: at install time ``pms.property`` is still missing the required
    columns that modules loaded later add to it, and the fixtures of TestPms
    cannot be created against a half-built registry.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.share = cls.env["roomdoo.precheckin.share"]
        cls.config_parameter = cls.env["ir.config_parameter"].sudo()
        cls.config_parameter.set_param("web.base.url", BASE_URL)
        cls.config_parameter.set_param("roomdoo_app_url", APP_URL)
        cls.config_parameter.set_param("roomdoo_fastapi.instance_name", INSTANCE_NAME)
        cls.config_parameter.set_param("roomdoo_fastapi.instance_image", False)
        # The share links Roomdoo builds only ever carry an active language,
        # and the helper validates the segment against the active languages.
        cls.lang_es = cls.env["res.lang"]._activate_lang("es_ES")
        # The title names the hotel, so give it something distinguishable from
        # the instance name.
        cls.pms_property1.partner_id.name = HOTEL_NAME
        # Pinned so the checkout boundary is exercised against a real offset
        # rather than whatever timezone the test runner happens to have.
        cls.pms_property1.tz = "Europe/Madrid"
        cls.pms_property1.user_ids = [(4, cls.env.user.id)]
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Share",
                "default_code": "DBL_SHR",
                "class_id": cls.room_type_class1.id,
            }
        )
        cls.env["pms.room"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "name": "101",
                "room_type_id": cls.room_type.id,
                "capacity": 2,
            }
        )
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct Share", "channel_type": "direct"}
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "firstname": "Share",
                "lastname": "Guest",
                "email": "share.guest@example.com",
            }
        )
        cls.folio = cls.env["pms.folio"].create(
            {
                "pms_property_id": cls.pms_property1.id,
                "partner_id": cls.partner.id,
            }
        )
        cls.reservation = cls.env["pms.reservation"].create(
            {
                "folio_id": cls.folio.id,
                "room_type_id": cls.room_type.id,
                "partner_id": cls.partner.id,
                "adults": 1,
                "sale_channel_origin_id": cls.sale_channel.id,
                "reservation_line_ids": [(0, False, {"date": fields.date.today()})],
            }
        )
        cls.folio_token = cls.folio.access_token

    def _document(self, kind, record_id, token, lang=None):
        return self.share.get_share_document(kind, record_id, token, lang_segment=lang)

    def _folio_html(self, token=None, lang=None):
        document = self._document(
            "precheckin", self.folio.id, token or self.folio_token, lang=lang
        )
        return document["html"]

    def _fixture_image(self):
        with open(os.path.join(dir_path, "roomdoo.jpg"), "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def _set_instance_image(self):
        attachment = self.env["ir.attachment"].create(
            {"name": "roomdoo_fastapi_image", "datas": self._fixture_image()}
        )
        self.config_parameter.set_param(
            "roomdoo_fastapi.instance_image", str(attachment.id)
        )
        return attachment

    def _property_today(self):
        """Today in the timezone of the property, which is what the code uses.

        ``fields.Date.today()`` is UTC, so a test built on it disagrees with the
        code under test for the hours the two dates differ — passing all day and
        failing after midnight in a positive offset. Found exactly that way.
        """
        return fields.Date.context_today(
            self.pms_property1.with_context(tz=self.pms_property1.tz)
        )

    def _shift_checkout(self, days):
        """Move the whole stay so that its checkout lands ``days`` from today.

        The dates are written on the reservation, which is what recomputes the
        stored ``last_checkout`` of the folio.
        """
        checkout = self._property_today() + timedelta(days=days)
        self.reservation.write(
            {"checkin": checkout - timedelta(days=1), "checkout": checkout}
        )
        return checkout

    def test_folio_card_names_the_hotel_and_the_group(self):
        """The hotel titles the card and the group brands it from above.

        The guest booked a hotel, not a group. And a forwarded link arrives
        with no message text to name the hotel, so the card has to.
        """
        html = self._folio_html()
        self.assertIn(f'<meta property="og:title" content="{HOTEL_TITLE}"/>', html)
        self.assertIn(
            f'<meta property="og:site_name" content="{INSTANCE_NAME}"/>', html
        )

    def test_hotel_image_wins_over_the_instance_one(self):
        """A picture of their own hotel beats a group logo."""
        self._set_instance_image()
        hotel_image = self.env["ir.attachment"].create(
            {
                "name": "hotel_image",
                "datas": self._fixture_image(),
                "res_model": self.pms_property1._name,
                "res_id": self.pms_property1.id,
                "res_field": self.share._get_property_image_field(),
            }
        )
        html = self._folio_html()
        self.assertIn(f'content="{BASE_URL}/web/image/{hotel_image.id}?', html)
        self.assertNotIn(FALLBACK_IMAGE, html)

    def test_folio_link_redirects_to_the_spa_with_the_language(self):
        """The optional language segment survives into the redirect target."""
        expected = f"{APP_URL}/{self.folio.id}/precheckin/{self.folio_token}/es"
        html = self._folio_html(lang="es")
        self.assertIn(f'data-redirect-url="{expected}"', html)
        self.assertIn(f'<a href="{expected}">', html)

    def test_folio_link_without_language_keeps_the_bare_path(self):
        expected = f"{APP_URL}/{self.folio.id}/precheckin/{self.folio_token}"
        html = self._folio_html()
        self.assertIn(f'data-redirect-url="{expected}"', html)

    def test_the_bounce_is_javascript_only(self):
        """Nothing a crawler follows may leave this document.

        A meta refresh or a 302 is honoured by crawlers too: they would land on
        the SPA and preview its generic card, so the whole endpoint would
        silently do nothing. Observed for real — with a meta refresh in place
        neither WhatsApp nor Telegram rendered any preview at all.
        """
        html = self._folio_html()
        self.assertNotIn("http-equiv", html)
        self.assertIn("window.location.replace", html)
        self.assertIn(f'<a href="{APP_URL}/', html)

    def test_public_share_url_is_what_callers_hand_to_the_spa(self):
        """The API answers with this instead of making the SPA build the path."""
        expected = f"{BASE_URL}/share/{self.folio.id}/precheckin/{self.folio_token}/es"
        self.assertEqual(
            self.share.get_public_share_url(
                "precheckin", self.folio.id, self.folio_token, lang_segment="es"
            ),
            expected,
        )

    def test_public_share_url_is_empty_when_it_cannot_be_built(self):
        """An empty string, never a broken URL: the caller falls back to the SPA."""
        for kind, record_id, token in [
            ("nonsense", self.folio.id, self.folio_token),
            ("precheckin", 0, self.folio_token),
            ("precheckin", self.folio.id, ""),
            ("precheckin", self.folio.id, None),
            ("precheckin", "not-an-id", self.folio_token),
        ]:
            self.assertEqual(
                self.share.get_public_share_url(kind, record_id, token),
                "",
                f"{kind}/{record_id}/{token} should not produce a URL",
            )

    def test_share_url_is_the_share_link_itself(self):
        """og:url points at the share document so re-crawls stay stable."""
        expected = f"{BASE_URL}/share/{self.folio.id}/precheckin/{self.folio_token}"
        self.assertIn(
            f'<meta property="og:url" content="{expected}"/>', self._folio_html()
        )

    def test_reservation_card_names_the_hotel(self):
        token = self.reservation._portal_ensure_token()
        expected = f"{APP_URL}/{self.reservation.id}/precheckin-reservation/{token}"
        document = self._document("precheckin-reservation", self.reservation.id, token)
        html = document["html"]
        self.assertIn(f'<meta property="og:title" content="{HOTEL_TITLE}"/>', html)
        self.assertIn(f'data-redirect-url="{expected}"', html)

    def test_unknown_record_falls_back_to_the_generic_card(self):
        """An unknown id serves the generic card, it never raises."""
        document = self._document(
            "precheckin", self.folio.id + 1000000, self.folio_token
        )
        html = document["html"]
        self.assertIn(f'<meta property="og:title" content="{GENERIC_TITLE}"/>', html)
        self.assertNotIn(INSTANCE_NAME, html)
        self.assertIn(FALLBACK_IMAGE, html)
        # No status is returned: the controller always answers 200 with this
        # document, because a broken preview is worse than a generic one.
        self.assertEqual(
            document["headers"],
            [
                ("Content-Type", "text/html; charset=utf-8"),
                # private: the URL carries an access token, so it has no
                # business sitting in a shared proxy.
                ("Cache-Control", "private, max-age=300"),
            ],
        )

    def test_wrong_token_falls_back_to_the_generic_card(self):
        html = self._folio_html(token="not-the-token")
        self.assertIn(f'<meta property="og:title" content="{GENERIC_TITLE}"/>', html)
        self.assertNotIn(INSTANCE_NAME, html)

    def test_unknown_record_never_claims_the_booking_is_gone(self):
        """The generic card makes no claim it cannot back."""
        html = self._folio_html(token="not-the-token")
        self.assertNotIn("Booking cancelled", html)
        self.assertNotIn("Stay completed", html)
        self.assertIn("Complete your online check-in before you arrive.", html)

    def test_token_less_record_is_unknown_and_stays_token_less(self):
        """A public GET reports it as unknown rather than minting a token.

        ``pms_api_rest`` mints the token in ``create``, so the precondition is
        forced here; what is under test is that this document never writes one.
        """
        self.reservation.access_token = False
        document = self._document(
            "precheckin-reservation", self.reservation.id, "any-token"
        )
        self.assertFalse(self.reservation.access_token)
        self.assertIn(
            f'<meta property="og:title" content="{GENERIC_TITLE}"/>', document["html"]
        )

    def test_cancelled_folio_says_so_and_is_not_forwarded(self):
        """A cancelled booking must not preview as an invitation to check in."""
        self.folio.action_cancel()
        self.assertEqual(self.folio.state, "cancel")
        html = self._folio_html()
        self.assertIn(f'<meta property="og:title" content="{CANCELLED_TITLE}"/>', html)
        self.assertIn("This booking was cancelled", html)
        # The public API applies no expiry, so the SPA would happily render the
        # pre-check-in form of a cancelled booking. The guest stops here.
        self.assertNotIn("data-redirect-url", html)
        self.assertNotIn(APP_URL, html)

    def test_past_folio_says_the_stay_is_over_and_is_not_forwarded(self):
        self._shift_checkout(-1)
        html = self._folio_html()
        self.assertIn(f'<meta property="og:title" content="{PAST_TITLE}"/>', html)
        self.assertIn("This stay has already ended", html)
        self.assertNotIn("data-redirect-url", html)
        self.assertNotIn(APP_URL, html)

    def test_checkout_today_is_not_past_yet(self):
        """The boundary is exclusive: a guest checking out today still checks in."""
        self._shift_checkout(0)
        html = self._folio_html()
        self.assertIn(f'<meta property="og:title" content="{HOTEL_TITLE}"/>', html)
        self.assertIn("data-redirect-url", html)

    def test_past_reservation_says_the_stay_is_over(self):
        """The reservation link uses its own checkout, not the folio one."""
        self._shift_checkout(-1)
        token = self.reservation._portal_ensure_token()
        document = self._document("precheckin-reservation", self.reservation.id, token)
        self.assertIn(
            f'<meta property="og:title" content="{PAST_TITLE}"/>', document["html"]
        )

    def test_expired_card_keeps_the_branding(self):
        """The guest still sees which hotel, and which group, the link is from."""
        self._shift_checkout(-1)
        self._set_instance_image()
        html = self._folio_html()
        self.assertIn(HOTEL_NAME, html)
        self.assertIn(INSTANCE_NAME, html)
        self.assertNotIn(FALLBACK_IMAGE, html)

    def test_expired_description_is_localised(self):
        self._shift_checkout(-1)
        html = self._folio_html(lang="es")
        self.assertIn(
            f'<meta property="og:description" content="{SPANISH_PAST_DESCRIPTION}"/>',
            html,
        )

    def test_instance_without_image_uses_the_sized_fallback_image(self):
        html = self._folio_html()
        self.assertIn(f'<meta property="og:image" content="{FALLBACK_IMAGE}"/>', html)
        self.assertIn('<meta property="og:image:width" content="1200"/>', html)
        self.assertIn('<meta property="og:image:height" content="630"/>', html)

    def test_instance_image_is_absolute_and_declares_no_size(self):
        attachment = self._set_instance_image()
        html = self._folio_html()
        self.assertIn(f'content="{BASE_URL}/web/image/{attachment.id}?', html)
        self.assertNotIn(FALLBACK_IMAGE, html)
        self.assertNotIn("og:image:width", html)
        self.assertNotIn("og:image:height", html)

    def test_twitter_tags_mirror_the_open_graph_ones(self):
        html = self._folio_html()
        self.assertIn('<meta name="twitter:card" content="summary_large_image"/>', html)
        self.assertIn(f'<meta name="twitter:title" content="{HOTEL_TITLE}"/>', html)
        self.assertIn(f'<meta name="twitter:image" content="{FALLBACK_IMAGE}"/>', html)
        self.assertIn('<meta name="twitter:description" content="Complete', html)

    def test_placeholder_app_url_renders_the_card_without_redirect(self):
        """``roomdoo_app_url`` ships as the literal string ``False``."""
        self.config_parameter.set_param("roomdoo_app_url", "False")
        html = self._folio_html()
        self.assertNotIn("False", html)
        self.assertNotIn("data-redirect-url", html)
        self.assertIn(f'<meta property="og:title" content="{HOTEL_TITLE}"/>', html)

    def test_language_absent_from_this_odoo_still_survives_the_redirect(self):
        """The SPA ships seven locales; a tenant activates whichever it needs.

        Gating the segment on ``res.lang`` would land a guest who was sent a
        Catalan link in the default language — the regression this endpoint
        exists to avoid.
        """
        self.assertFalse(self.env["res.lang"].search([("code", "=", "ca_ES")]).active)
        expected = f"{APP_URL}/{self.folio.id}/precheckin/{self.folio_token}/ca"
        html = self._folio_html(lang="ca")
        self.assertIn(f'data-redirect-url="{expected}"', html)
        self.assertIn('<html lang="ca"', html)
        # Untranslatable here, so the text stays in the default language. A
        # cosmetic mismatch, unlike landing in the wrong language.
        self.assertIn("Complete your online check-in before you arrive.", html)

    def test_malformed_language_segment_is_dropped(self):
        """Anything the SPA route would not match never reaches the URL."""
        expected = f"{APP_URL}/{self.folio.id}/precheckin/{self.folio_token}"
        for segment in ("zzz", "1a", "e", "../x"):
            html = self._folio_html(lang=segment)
            self.assertIn(
                f'data-redirect-url="{expected}"',
                html,
                f"segment {segment!r} leaked into the redirect",
            )
            self.assertNotIn("<html lang=", html)

    def test_every_state_suffix_is_in_the_catalog(self):
        """The valid-link suffix is translatable like the other two states.

        It titles the card a guest almost always sees, and it shipped as a bare
        constant while a cancelled link localised: an English title over a
        translated description. Only the catalog side is asserted, because the
        Spanish msgstr is identical to the msgid, so no rendered string can
        tell a marked-up literal from the constant it replaced.
        """
        catalog = code_translations.get_python_translations(
            "roomdoo_precheckin_share", "es_ES"
        )
        for state in ("ok", "cancelled", "past"):
            suffix = self.share._get_share_state_labels()[state]["suffix"]
            self.assertIn(
                suffix,
                catalog,
                f"the {state} suffix is not offered for translation",
            )

    def test_description_is_localised_from_the_language_segment(self):
        """The description comes from the i18n catalogs, not a fixed string."""
        html = self._folio_html(lang="es")
        self.assertIn(
            f'<meta property="og:description" content="{SPANISH_DESCRIPTION}"/>', html
        )

    def test_document_language_follows_the_url_segment(self):
        """``lang`` is a reserved QWeb value that silently wins over ours."""
        self.assertIn('<html lang="es"', self._folio_html(lang="es"))

    def test_document_without_language_declares_none(self):
        self.assertNotIn("<html lang=", self._folio_html())

    def test_instance_name_is_html_escaped(self):
        self.config_parameter.set_param(
            "roomdoo_fastapi.instance_name", '"><script>alert(1)</script>'
        )
        html = self._folio_html()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)", html)

    def test_hostile_token_cannot_escape_the_document(self):
        html = self._folio_html(token="</script><script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("%3C%2Fscript%3E", html)
