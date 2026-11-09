# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Tests for the Mirai lines that the folio import keeps on top of the folio
internal notes (loyalty club membership, upselling to collect at the hotel).

The bookings are synthetic but keep the shape of what Mirai sends through
Wubook (``ancillary`` texts, ``channel_data.split_reservation``). The mapping
is first exercised without a Wubook backend: it only reads the incoming
record, the property and the type of the backend and the binding given in
the mapper options. The last tests import the bookings through the real folio
importer, without reaching Wubook.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import psycopg2.errors

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.component.tests.common import TransactionComponentCase
from odoo.addons.connector.components.mapper import MapOptions
from odoo.addons.connector_pms_wubook.models.pms_folio.adapter import (
    ChannelWubookPmsFolioAdapter,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.channel_booking import (
    MIRAI_CHANNEL_ID,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.channel_notes import (
    MIRAI_NOTE_PREFIX,
    merge_tagged_lines,
    normalize_line,
    parse_mirai_club,
    parse_mirai_upselling,
)
from odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import import (
    ChannelWubookPmsFolioMapperImport,
)
from odoo.addons.connector_pms_wubook.tests.test_master_sync import (
    _make_backend_environment,
)
from odoo.addons.queue_job.exception import RetryableJobError

MAPPER_LOGGER = "odoo.addons.connector_pms_wubook.models.pms_folio.mapper_import"

MEMBER_ID = "1000000000000000001"
CLUB_NO = "member: NO"
CLUB_NEW = f"joinsclub: NEW - source: RESERVATION - idMember: {MEMBER_ID}"
CLUB_EXISTING = f"joinsclub: EXISTING - source: RESERVATION - idMember: {MEMBER_ID}"
CLUB_WEBSITE = f"joinsclub: EXISTING - source: WEBSITE - idMember: {MEMBER_ID}"
UPSELLING = (
    "Upselling status: UPSELLING_APPLIED, Upselling price: 29.07. "
    "The guest must pay it to the hotel directly."
)

CLUB_LINE = "Loyalty club member"
CLUB_LINE_ES = "Socio del club de fidelización"
# formatLang glues the amount and the currency symbol with a no-break space
UPSELLING_LINE = (
    "Upselling applied: 29.07\xa0€ (according to Mirai, the guest pays it at the"
    " hotel)"
)
UPSELLING_LINE_ES = (
    "Upselling aplicado: 29,07\xa0€ (según Mirai, lo paga el huésped en el hotel)"
)
WARNING_ES = "No se han podido actualizar las líneas [Mirai] de las notas internas"

# Wubook ids of the room type and the rate of the imported bookings
ROOM_TYPE_EXTERNAL_ID = 501
RATE_EXTERNAL_ID = 601


def mirai_booking(club=CLUB_NO, upselling=None, split=None, **values):
    """Return a synthetic Mirai booking as the folio adapter hands it over."""
    ancillary = {
        "ClubStatus": club,
        "Paid amount": "0 EUR",
        "Due amount": "243.68 EUR",
        "Detected Board": "nb",
    }
    if upselling is not None:
        ancillary["Upselling"] = upselling
    channel_data = {"addons_embedded": True}
    if split:
        channel_data["split_reservation"] = {
            "n_stream": split[0],
            "tot_stream": split[1],
        }
    booking = {
        "id_channel": MIRAI_CHANNEL_ID,
        "reservation_code": 1700000001,
        "status": 1,
        "was_modified": 0,
        "modified_reservations": [],
        "currency": "EUR",
        "ancillary": ancillary,
        "channel_data": channel_data,
    }
    booking.update(values)
    return booking


def note(*lines):
    """Return the note text made of ``lines``, as the import stores it.

    The generated lines are normalized to a single line, which also turns
    the no-break space of the amounts into a plain one.
    """
    return "\n".join(" ".join(line.split()) for line in lines)


def mirai_line(line):
    return MIRAI_NOTE_PREFIX + line


class _Binding:
    """What the mapping reads from a ``channel.wubook.pms.folio`` binding."""

    def __init__(self, folio):
        self.odoo_id = folio

    @property
    def internal_comment(self):
        return self.odoo_id.internal_comment


class _FolioMapper(SimpleNamespace):
    """What the mapping uses of the folio import mapper component."""

    internal_comment = ChannelWubookPmsFolioMapperImport.internal_comment
    _channel_notes_enabled = ChannelWubookPmsFolioMapperImport._channel_notes_enabled


class TestChannelNotesMerge(TransactionCase):
    """``merge_tagged_lines`` owns the prefixed lines and nothing else."""

    def test_adds_lines_on_an_empty_note(self):
        for text in ("", False, None):
            with self.subTest(text=text):
                self.assertEqual(
                    merge_tagged_lines(text, MIRAI_NOTE_PREFIX, ["one", "two"]),
                    "[Mirai] one\n[Mirai] two",
                )

    def test_keeps_staff_text_and_its_order_below_the_block(self):
        staff = "Parking paid\n\n  Late arrival, keys at the bar\nCall before"
        self.assertEqual(
            merge_tagged_lines(staff, MIRAI_NOTE_PREFIX, ["one"]),
            "[Mirai] one\n" + staff,
        )

    def test_is_idempotent(self):
        lines = ["one", "two"]
        for text in (
            "",
            "staff",
            "staff 1\n\nstaff 2\n",
            "[Mirai] old\nstaff",
            "staff\n[Mirai] one\nmore staff\n[Mirai] two",
            "\n\n[Mirai] one\r\nstaff\r\n\r\n",
        ):
            with self.subTest(text=text):
                once = merge_tagged_lines(text, MIRAI_NOTE_PREFIX, lines)
                twice = merge_tagged_lines(once, MIRAI_NOTE_PREFIX, lines)
                self.assertEqual(twice, once)
                self.assertIs(twice, once)

    def test_replaces_changed_lines_without_duplicates(self):
        text = "[Mirai] old one\n[Mirai] old two\nstaff\n[Mirai] old three"
        merged = merge_tagged_lines(text, MIRAI_NOTE_PREFIX, ["new", "other"])
        self.assertEqual(merged, "[Mirai] new\n[Mirai] other\nstaff")
        # A duplicated block is collapsed to the expected lines
        merged = merge_tagged_lines(
            "[Mirai] new\n[Mirai] new\nstaff", MIRAI_NOTE_PREFIX, ["new"]
        )
        self.assertEqual(merged, "[Mirai] new\nstaff")

    def test_removes_tagged_lines_when_there_are_no_lines(self):
        text = "[Mirai] one\n\nstaff 1\n\nstaff 2\n[Mirai] two\n"
        self.assertEqual(
            merge_tagged_lines(text, MIRAI_NOTE_PREFIX, []), "staff 1\n\nstaff 2"
        )
        self.assertEqual(merge_tagged_lines("[Mirai] one", MIRAI_NOTE_PREFIX, []), "")

    def test_returns_the_text_itself_when_there_is_nothing_to_do(self):
        for text, lines in (
            ("staff 1\r\nstaff 2\r\n  ", []),
            ("", []),
            (None, []),
            (False, []),
            ("[Mirai] one\r\nstaff\r\n", ["one"]),
            # The staff moved the block below its own text: it stays there
            ("staff\n[Mirai] one\n[Mirai] two", ["one", "two"]),
        ):
            with self.subTest(text=text, lines=lines):
                self.assertIs(merge_tagged_lines(text, MIRAI_NOTE_PREFIX, lines), text)

    def test_only_lines_starting_with_the_exact_prefix_are_managed(self):
        staff = "MIRAI paid at the desk\n[mirai] lower case\n[Mirai]no space\n x"
        self.assertEqual(
            merge_tagged_lines(staff, MIRAI_NOTE_PREFIX, ["one"]),
            "[Mirai] one\n" + staff,
        )
        self.assertIs(merge_tagged_lines(staff, MIRAI_NOTE_PREFIX, []), staff)

    def test_generated_lines_with_line_breaks_stay_single_lines(self):
        staff = "staff 1\nstaff 2"
        for separator in ("\n", "\r\n", "\r", "\x85", "\u2028", "\u2029", "\x0b"):
            with self.subTest(separator=repr(separator)):
                lines = [f"first{separator}second", "last"]
                text = staff
                rounds = []
                for _round in range(3):
                    text = merge_tagged_lines(text, MIRAI_NOTE_PREFIX, lines)
                    rounds.append(text)
                self.assertEqual(rounds[0], rounds[1])
                self.assertEqual(rounds[1], rounds[2])
                self.assertEqual(
                    rounds[0].splitlines(),
                    ["[Mirai] first second", "[Mirai] last", "staff 1", "staff 2"],
                )

    def test_blank_generated_lines_are_skipped(self):
        self.assertEqual(
            merge_tagged_lines("staff", MIRAI_NOTE_PREFIX, ["", " \n ", "one"]),
            "[Mirai] one\nstaff",
        )

    def test_normalize_line(self):
        self.assertEqual(normalize_line(" a\tb \u2028c\x85d\r\ne\xa0f "), "a b c d e f")
        self.assertEqual(normalize_line(None), "")
        self.assertEqual(len(normalize_line("x" * 300)), 200)
        # Truncation never leaves a trailing space and is stable
        self.assertEqual(normalize_line("word " * 10, max_len=10), "word word")
        self.assertEqual(normalize_line("word word", max_len=10), "word word")


class TestMiraiParsers(TransactionCase):
    def test_club_members(self):
        for value in (CLUB_NEW, CLUB_EXISTING, CLUB_WEBSITE, "joinsclub: VIP"):
            with self.subTest(value=value):
                self.assertTrue(parse_mirai_club(value))

    def test_club_non_members(self):
        for value in (CLUB_NO, "Member:no", "  MEMBER : NO ", "", "  ", None, 0):
            with self.subTest(value=value):
                self.assertFalse(parse_mirai_club(value))

    def test_upselling_applied(self):
        self.assertEqual(parse_mirai_upselling(UPSELLING), 29.07)
        self.assertEqual(
            parse_mirai_upselling(
                "Upselling status: UPSELLING_APPLIED, Upselling price: 57.00."
            ),
            57.0,
        )
        self.assertEqual(
            parse_mirai_upselling(
                "Upselling status: UPSELLING_APPLIED, Upselling price: 29,07"
            ),
            29.07,
        )

    def test_upselling_not_applied_or_unreadable(self):
        for value in (
            UPSELLING.replace("UPSELLING_APPLIED", "UPSELLING_REJECTED"),
            UPSELLING.replace("UPSELLING_APPLIED", "UPSELLING_APPLIED_PARTIALLY"),
            UPSELLING.replace("29.07", "N/A"),
            # A thousands separator is not trusted: no amount rather than 1.03
            UPSELLING.replace("29.07", "1,029.07"),
            UPSELLING.replace("29.07", "29.0712"),
            "Upselling status: UPSELLING_APPLIED",
            "",
            None,
            29.07,
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_mirai_upselling(value))


class TestMiraiNoteLines(TransactionCase):
    """``_get_channel_note_lines`` renders the lines in the context language."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_ES")
        cls.folios = cls.env["channel.wubook.pms.folio"].with_context(lang="en_US")

    def test_other_channels_give_no_lines(self):
        for id_channel in (0, 2, 43):
            with self.subTest(id_channel=id_channel):
                booking = mirai_booking(CLUB_EXISTING, UPSELLING, id_channel=id_channel)
                self.assertEqual(self.folios._get_channel_note_lines(booking), [])

    def test_unexpected_ancillary_gives_no_lines(self):
        for ancillary in (None, "ClubStatus: x", [], {}):
            with self.subTest(ancillary=ancillary):
                booking = dict(mirai_booking(), ancillary=ancillary)
                self.assertEqual(self.folios._get_channel_note_lines(booking), [])

    def test_no_line_for_non_members_nor_pending_payments(self):
        booking = mirai_booking(CLUB_NO)
        booking["ancillary"]["BTP"] = "BTP|2026-07-01T10:00:00|Pending"
        self.assertEqual(self.folios._get_channel_note_lines(booking), [])

    def test_same_neutral_club_line_for_every_member_shape(self):
        for club in (CLUB_NEW, CLUB_EXISTING, CLUB_WEBSITE):
            with self.subTest(club=club):
                lines = self.folios._get_channel_note_lines(mirai_booking(club))
                self.assertEqual(lines, [CLUB_LINE])
                self.assertNotIn(MEMBER_ID, " ".join(lines))

    def test_upselling_line(self):
        lines = self.folios._get_channel_note_lines(mirai_booking(CLUB_NO, UPSELLING))
        self.assertEqual(lines, [UPSELLING_LINE])
        lines = self.folios._get_channel_note_lines(
            mirai_booking(CLUB_EXISTING, UPSELLING)
        )
        self.assertEqual(lines, [CLUB_LINE, UPSELLING_LINE])

    def test_no_upselling_line_when_not_applied(self):
        booking = mirai_booking(
            CLUB_NO, UPSELLING.replace("UPSELLING_APPLIED", "UPSELLING_REJECTED")
        )
        self.assertEqual(self.folios._get_channel_note_lines(booking), [])

    def test_upselling_only_on_the_first_stream_of_a_split_booking(self):
        for split, expected in (
            ((1, 2), [UPSELLING_LINE]),
            ((2, 2), []),
            (None, [UPSELLING_LINE]),
        ):
            with self.subTest(split=split):
                booking = mirai_booking(CLUB_NO, UPSELLING, split=split)
                self.assertEqual(self.folios._get_channel_note_lines(booking), expected)
        # Bookings with no channel data at all are not split either
        booking = dict(mirai_booking(CLUB_NO, UPSELLING), channel_data=None)
        self.assertEqual(self.folios._get_channel_note_lines(booking), [UPSELLING_LINE])

    def test_upselling_amount_without_a_known_currency(self):
        for currency, amount in (("ZZZ", "29.07 ZZZ"), (None, "29.07"), ("", "29.07")):
            with self.subTest(currency=currency):
                booking = mirai_booking(CLUB_NO, UPSELLING, currency=currency)
                self.assertEqual(
                    self.folios._get_channel_note_lines(booking),
                    [
                        f"Upselling applied: {amount} (according to Mirai, the guest"
                        " pays it at the hotel)"
                    ],
                )

    def test_spanish_rendering(self):
        """The lines come from es.po and its placeholders match."""
        folios = self.folios.with_context(lang="es_ES")
        lines = folios._get_channel_note_lines(mirai_booking(CLUB_NEW, UPSELLING))
        self.assertEqual(lines, [CLUB_LINE_ES, UPSELLING_LINE_ES])


class TestMiraiNotesMapping(TransactionCase):
    """The ``internal_comment`` mapping of the folio import mapper."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_ES")
        cls.company = cls.env["res.company"].create({"name": "Mirai Notes Co"})
        cls.pricelist = cls.env["product.pricelist"].create(
            {"name": "Mirai Notes Pricelist", "company_id": cls.company.id}
        )
        cls.pms_property = cls.env["pms.property"].create(
            {
                "name": "Mirai Notes Property",
                "company_id": cls.company.id,
                "default_pricelist_id": cls.pricelist.id,
                "lang": "en_US",
            }
        )
        cls.backend_type = cls.env["channel.wubook.backend.type"].create(
            {
                "name": "Mirai Notes Type",
                "model_type_id": cls.env.ref(
                    "connector_pms_wubook.model_channel_wubook_backend_type"
                ).id,
                "direct_channel_type_id": cls.env.ref(
                    "connector_pms_wubook.main_pms_sale_channel_wubook"
                ).id,
            }
        )
        cls.mirai_ota = cls._ota(MIRAI_CHANNEL_ID, import_channel_notes=True)
        cls.folio_model = type(cls.env["channel.wubook.pms.folio"])
        # The binding the importer passes when the folio does not exist yet
        cls.no_folio = cls.env["channel.wubook.pms.folio"]

    @classmethod
    def _ota(cls, id_channel, import_channel_notes):
        """Add a row to the OTA mappings of the backend type."""
        return cls.env["channel.wubook.backend.type.ota"].create(
            {
                "backend_type_id": cls.backend_type.id,
                "wubook_ota": id_channel,
                "import_channel_notes": import_channel_notes,
            }
        )

    def _create_folio(self, internal_comment=False):
        return self.env["pms.folio"].create(
            {
                "pms_property_id": self.pms_property.id,
                "partner_name": "Guest, Test",
                "internal_comment": internal_comment,
            }
        )

    def _map(self, booking, binding):
        """Run the mapping as the importer does, with ``binding`` in the
        options: the folio binding on updates, an empty recordset on
        creation, and no option at all (None) when the binder looks for the
        folio of a modified booking."""
        mapper = _FolioMapper(
            env=self.env,
            backend_record=SimpleNamespace(
                pms_property_id=self.pms_property,
                backend_type_id=self.backend_type.parent_id,
            ),
            options=MapOptions() if binding is None else MapOptions(binding=binding),
        )
        return mapper.internal_comment(booking)

    def test_mapping_also_runs_on_updates(self):
        method = ChannelWubookPmsFolioMapperImport.internal_comment
        self.assertTrue(getattr(method, "is_mapping", False))
        self.assertFalse(getattr(method, "only_create", False))

    def test_other_channels_never_touch_the_note(self):
        folio = self._create_folio(note(mirai_line(CLUB_LINE), "staff"))
        for id_channel in (0, 2, 43):
            with self.subTest(id_channel=id_channel):
                # Not even with their notes enabled: only Mirai passes lines
                self._ota(id_channel, import_channel_notes=True)
                booking = mirai_booking(CLUB_NO, id_channel=id_channel)
                self.assertEqual(self._map(booking, _Binding(folio)), {})

    def test_nothing_is_written_unless_enabled_for_mirai(self):
        """Disabled, the lines already in the note are left as they are."""
        text = note(mirai_line(CLUB_LINE), "staff")
        folio = self._create_folio(text)
        booking = mirai_booking(CLUB_NO, UPSELLING)
        self.mirai_ota.import_channel_notes = False
        self._ota(2, import_channel_notes=True)
        for case, binding in (("creation", self.no_folio), ("update", _Binding(folio))):
            with self.subTest(case=case), mock.patch.object(
                self.folio_model, "_get_channel_note_lines"
            ) as get_lines:
                self.assertEqual(self._map(booking, binding), {})
                get_lines.assert_not_called()
        # Neither without a mapping row for Mirai at all
        self.mirai_ota.unlink()
        self.assertEqual(self._map(booking, _Binding(folio)), {})
        self.assertEqual(folio.internal_comment, text)

    def test_label_of_the_setting(self):
        """The label and help of the column come from es.po in Spanish."""
        self.env["ir.module.module"]._load_module_terms(
            ["connector_pms_wubook"], ["es_ES"], overwrite=True
        )
        otas = self.env["channel.wubook.backend.type.ota"]
        for lang, string, help_start in (
            ("en_US", "Channel Notes", "Write in the folio internal notes"),
            ("es_ES", "Notas del canal", "Escribir en las notas internas de la ficha"),
        ):
            with self.subTest(lang=lang):
                description = otas.with_context(lang=lang).fields_get(
                    ["import_channel_notes"], ["string", "help"]
                )["import_channel_notes"]
                self.assertEqual(description["string"], string)
                self.assertTrue(description["help"].startswith(help_start))

    def test_one_enabled_row_of_the_channel_is_enough(self):
        """Nothing prevents two mapping rows for the same channel."""
        self._ota(MIRAI_CHANNEL_ID, import_channel_notes=False)
        self.assertEqual(
            self._map(mirai_booking(CLUB_NEW), self.no_folio),
            {"internal_comment": mirai_line(CLUB_LINE)},
        )

    def test_an_unreadable_mapping_leaves_the_note_alone(self):
        with mock.patch.object(
            type(self.backend_type.parent_id),
            "child_id",
            new_callable=mock.PropertyMock,
            side_effect=ValidationError("More than one backend type child"),
        ), self.assertLogs(MAPPER_LOGGER, "ERROR"):
            values = self._map(mirai_booking(CLUB_NEW), self.no_folio)
        self.assertEqual(values, {})

    def test_nothing_is_computed_while_the_binder_looks_for_the_folio(self):
        with mock.patch.object(
            self.folio_model, "_get_channel_note_lines"
        ) as get_lines:
            self.assertEqual(self._map(mirai_booking(CLUB_NEW, UPSELLING), None), {})
        get_lines.assert_not_called()

    def test_creation_writes_the_block(self):
        values = self._map(mirai_booking(CLUB_NEW, UPSELLING), self.no_folio)
        self.assertEqual(
            values,
            {
                "internal_comment": note(
                    mirai_line(CLUB_LINE), mirai_line(UPSELLING_LINE)
                )
            },
        )
        self.assertNotIn(MEMBER_ID, values["internal_comment"])

    def test_creation_without_lines_writes_nothing(self):
        self.assertEqual(self._map(mirai_booking(CLUB_NO), self.no_folio), {})

    def test_update_keeps_staff_text_and_writes_nothing_when_unchanged(self):
        folio = self._create_folio(
            note(mirai_line(CLUB_LINE), "Late arrival\n\nParking")
        )
        self.assertEqual(self._map(mirai_booking(CLUB_EXISTING), _Binding(folio)), {})
        values = self._map(mirai_booking(CLUB_EXISTING, UPSELLING), _Binding(folio))
        self.assertEqual(
            values,
            {
                "internal_comment": note(
                    mirai_line(CLUB_LINE),
                    mirai_line(UPSELLING_LINE),
                    "Late arrival\n\nParking",
                )
            },
        )

    def test_existing_member_seen_as_new_keeps_the_text(self):
        """Mirai sends a modification of an EXISTING member booking as NEW."""
        folio = self._create_folio()
        folio.write(self._map(mirai_booking(CLUB_EXISTING), self.no_folio))
        self.assertEqual(folio.internal_comment, mirai_line(CLUB_LINE))
        modified = mirai_booking(
            CLUB_NEW, reservation_code=1700000002, modified_reservations=[1700000001]
        )
        self.assertEqual(self._map(modified, _Binding(folio)), {})

    def test_lines_that_go_away_are_removed(self):
        folio = self._create_folio(
            note(mirai_line(CLUB_LINE), mirai_line(UPSELLING_LINE), "staff")
        )
        values = self._map(mirai_booking(CLUB_EXISTING), _Binding(folio))
        self.assertEqual(
            values, {"internal_comment": note(mirai_line(CLUB_LINE), "staff")}
        )
        folio.write(values)
        values = self._map(mirai_booking(CLUB_NO), _Binding(folio))
        self.assertEqual(values, {"internal_comment": "staff"})
        # A note holding only the block is emptied
        folio = self._create_folio(mirai_line(CLUB_LINE))
        values = self._map(mirai_booking(CLUB_NO), _Binding(folio))
        self.assertEqual(values, {"internal_comment": False})

    def test_lifecycle_create_modify_cancel_reimport(self):
        folio = self._create_folio()
        binding = _Binding(folio)
        # Creation of the folio, the member came through the website
        folio.write(self._map(mirai_booking(CLUB_WEBSITE), self.no_folio))
        # Reception writes its own notes below
        folio.internal_comment += "\nParking paid at the desk\n\nCall before arrival"
        # Modification: new code, the member arrives as NEW and Mirai applied
        # an upselling the guest pays at the hotel
        modified = mirai_booking(
            CLUB_NEW,
            UPSELLING,
            reservation_code=1700000002,
            modified_reservations=[1700000001],
        )
        folio.write(self._map(modified, binding))
        expected = note(
            mirai_line(CLUB_LINE),
            mirai_line(UPSELLING_LINE),
            "Parking paid at the desk",
            "",
            "Call before arrival",
        )
        self.assertEqual(folio.internal_comment, expected)
        # The cancellation carries the same booking data: nothing to write
        self.assertEqual(self._map(dict(modified, status=5), binding), {})
        # Neither when the version before the cancellation is imported again
        # afterwards (e.g. by arrival dates)
        self.assertEqual(self._map(modified, binding), {})
        self.assertEqual(folio.internal_comment, expected)
        self.assertEqual(folio.internal_comment.count(MIRAI_NOTE_PREFIX), 2)

    def test_lines_are_rendered_in_the_language_of_the_property(self):
        booking = mirai_booking(CLUB_NEW, UPSELLING)
        expected = {
            "internal_comment": note(
                mirai_line(CLUB_LINE_ES), mirai_line(UPSELLING_LINE_ES)
            )
        }
        self.pms_property.lang = "es_ES"
        self.assertEqual(self._map(booking, self.no_folio), expected)
        # Without a language on the property, the one of its company
        self.pms_property.lang = False
        self.company.partner_id.lang = "es_ES"
        self.assertEqual(self._map(booking, self.no_folio), expected)

    def test_a_failure_does_not_break_the_import(self):
        self.pms_property.lang = "es_ES"
        folio = self._create_folio("staff")
        messages = folio.message_ids
        with mock.patch.object(
            self.folio_model,
            "_get_channel_note_lines",
            side_effect=ValueError("unexpected payload"),
        ):
            # Every import of the booking fails the same way until the cause
            # is fixed: the folio gets a single warning
            for _import in range(3):
                with self.assertLogs(MAPPER_LOGGER, "ERROR"):
                    values = self._map(mirai_booking(CLUB_EXISTING), _Binding(folio))
                self.assertEqual(values, {})
            self.assertEqual(folio.internal_comment, "staff")
            warning = folio.message_ids - messages
            self.assertEqual(len(warning), 1)
            self.assertEqual(warning.subtype_id, self.env.ref("mail.mt_note"))
            self.assertIn(WARNING_ES, warning.body)
            # On creation there is no folio to warn yet: only the log
            with self.assertLogs(MAPPER_LOGGER, "ERROR"):
                values = self._map(mirai_booking(CLUB_EXISTING), self.no_folio)
            self.assertEqual(values, {})

    def test_a_failing_warning_does_not_break_the_import_either(self):
        folio = self._create_folio("staff")
        with mock.patch.object(
            self.folio_model,
            "_get_channel_note_lines",
            side_effect=ValueError("unexpected payload"),
        ), mock.patch.object(
            type(self.env["pms.folio"]),
            "message_post",
            side_effect=ValueError("cannot post"),
        ):
            with self.assertLogs("odoo.addons.connector_pms_wubook", "ERROR") as logs:
                values = self._map(mirai_booking(CLUB_EXISTING), _Binding(folio))
        self.assertEqual(values, {})
        self.assertEqual(len(logs.records), 2)
        self.assertEqual(folio.internal_comment, "staff")

    def test_a_failure_of_the_work_left_pending_still_fails_the_import(self):
        """It is not taken for a failure of the warning, which is posted
        after flushing what the import left pending."""
        folio = self._create_folio("staff")

        def pending_work():
            raise RuntimeError("pending work of the import")

        self.env.cr.precommit.add(pending_work)
        with mock.patch.object(
            self.folio_model,
            "_get_channel_note_lines",
            side_effect=ValueError("unexpected payload"),
        ), self.assertLogs(MAPPER_LOGGER, "ERROR"):
            with self.assertRaisesRegex(RuntimeError, "pending work"):
                self._map(mirai_booking(CLUB_EXISTING), _Binding(folio))

    def test_concurrency_errors_are_raised_for_a_retry(self):
        for error in (
            psycopg2.errors.SerializationFailure(),
            psycopg2.errors.LockNotAvailable(),
            RetryableJobError("busy"),
        ):
            with self.subTest(error=type(error).__name__, step="lines"):
                with mock.patch.object(
                    self.folio_model, "_get_channel_note_lines", side_effect=error
                ):
                    with self.assertRaises(type(error)):
                        self._map(mirai_booking(CLUB_EXISTING), self.no_folio)
            with self.subTest(error=type(error).__name__, step="mapping row"):
                with mock.patch.object(
                    type(self.backend_type.parent_id),
                    "child_id",
                    new_callable=mock.PropertyMock,
                    side_effect=error,
                ):
                    with self.assertRaises(type(error)):
                        self._map(mirai_booking(CLUB_EXISTING), self.no_folio)


@tagged("post_install", "-at_install")
class TestMiraiNotesImport(TransactionComponentCase):
    """The lines through the real folio importer (``importer.run``).

    The bookings are handed over as external data, and the adapter answers
    for the old code of a modified booking, which the importer reads again on
    every re-import of the new one: nothing reaches Wubook.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _make_backend_environment(cls)
        cls.env["res.lang"]._activate_lang("es_ES")
        cls.pms_property.lang = "es_ES"
        availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Mirai Notes Plan"}
        )
        pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Mirai Notes Rate",
                "availability_plan_id": availability_plan.id,
                "is_pms_available": True,
            }
        )
        for name in ("Mirai Notes 101", "Mirai Notes 102"):
            cls.env["pms.room"].create(
                {
                    "name": name,
                    "pms_property_id": cls.pms_property.id,
                    "room_type_id": cls.room_type_a.id,
                    "capacity": 2,
                }
            )
        sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Mirai Notes Channel", "channel_type": "indirect"}
        )
        cls.agency = cls.env["res.partner"].create(
            {
                "name": "Mirai Notes Agency",
                "is_agency": True,
                "sale_channel_id": sale_channel.id,
            }
        )
        cls.mirai_ota = cls.env["channel.wubook.backend.type.ota"].create(
            {
                "backend_type_id": cls.backend.backend_type_id.child_id.id,
                "wubook_ota": MIRAI_CHANNEL_ID,
                "agency_id": cls.agency.id,
                "import_channel_notes": True,
            }
        )
        cls.env["channel.wubook.pms.room.type"].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": cls.room_type_a.id,
                "backend_id": cls.backend.id,
                "external_id": ROOM_TYPE_EXTERNAL_ID,
            }
        )
        cls.env["channel.wubook.product.pricelist"].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": pricelist.id,
                "backend_id": cls.backend.id,
                "external_id": RATE_EXTERNAL_ID,
            }
        )
        cls.checkin = date.today() + timedelta(days=40)
        cls.folio_model = type(cls.env["channel.wubook.pms.folio"])

    def _booking(self, code, club, upselling=None, modified=None, nights=2):
        """Return a whole Mirai booking as the folio adapter hands it over."""
        lines = [
            {
                "ancillary": None,
                "price": 100.0,
                "day": self.checkin + timedelta(days=night),
                "room_id": ROOM_TYPE_EXTERNAL_ID,
                "board": None,
                "occupancy": 2,
                "children": 0,
                "board_included": True,
                "vat_included": True,
                "agency_id": self.agency.id,
                "rate_id": RATE_EXTERNAL_ID,
            }
            for night in range(nights)
        ]
        return mirai_booking(
            club,
            upselling,
            reservation_code=code,
            modified_reservations=modified or [],
            customer_surname="Guest",
            customer_name="Test",
            customer_language_iso="es",
            customer_phone=False,
            customer_mail=False,
            payment_gateway_fee=0,
            amount=100.0 * nights,
            reservations=[
                {
                    "room_id": ROOM_TYPE_EXTERNAL_ID,
                    "arrival_hour": None,
                    "ota_reservation_code": f"OTA-{code}",
                    "board": None,
                    "occupancy": 2,
                    "children": 0,
                    "rate_id": RATE_EXTERNAL_ID,
                    "customer_notes": "",
                    "id_channel": MIRAI_CHANNEL_ID,
                    "lines": lines,
                }
            ],
        )

    def _import(self, booking):
        """Import ``booking`` as a push from Wubook does and return its
        folio binding."""

        def read(adapter, external_id, ancillary=True):
            # The old code of a modified booking: Wubook marks it as such
            return {"reservation_code": external_id, "was_modified": 1}

        with mock.patch.object(ChannelWubookPmsFolioAdapter, "read", read), mock.patch(
            "xmlrpc.client.Server", side_effect=AssertionError("Wubook reached")
        ) as server:
            self.env["channel.wubook.pms.folio"].import_record(
                self.backend, booking["reservation_code"], external_data=booking
            )
        server.assert_not_called()
        return self.env["channel.wubook.pms.folio"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", str(booking["reservation_code"])),
            ]
        )

    def _warnings(self, folio):
        return folio.message_ids.filtered(lambda message: WARNING_ES in message.body)

    def test_lines_through_the_life_of_a_booking(self):
        get_lines = self.folio_model._get_channel_note_lines
        computed = []

        def spy_lines(folios, booking):
            computed.append(booking["reservation_code"])
            return get_lines(folios, booking)

        with mock.patch.object(self.folio_model, "_get_channel_note_lines", spy_lines):
            # Creation
            folio = self._import(self._booking(1700000101, CLUB_EXISTING)).odoo_id
            self.assertEqual(folio.internal_comment, note(mirai_line(CLUB_LINE_ES)))
            # Reception writes its own notes below
            folio.internal_comment += "\nParking pagado en recepción"
            # Modification: new code, the member arrives as NEW, Mirai applied
            # an upselling and the stay is one night longer
            modified = self._booking(
                1700000102, CLUB_NEW, UPSELLING, modified=[1700000101], nights=3
            )
            self.assertEqual(self._import(modified).odoo_id, folio)
            expected = note(
                mirai_line(CLUB_LINE_ES),
                mirai_line(UPSELLING_LINE_ES),
                "Parking pagado en recepción",
            )
            self.assertEqual(folio.internal_comment, expected)
            # Computed once per import, not while the binder looks for the
            # folio of the modification
            self.assertEqual(computed, [1700000101, 1700000102])
            # A re-import of the same version does not write the note
            written = []
            folio_write = type(folio).write

            def spy_write(folios, vals):
                written.append(set(vals))
                return folio_write(folios, vals)

            with mock.patch.object(type(folio), "write", spy_write):
                self.assertEqual(self._import(modified).odoo_id, folio)
            self.assertTrue(written)
            self.assertFalse([vals for vals in written if "internal_comment" in vals])
            # The cancellation keeps the note as it was
            self._import(dict(modified, status=5))
            self.assertEqual(folio.state, "cancel")
            self.assertEqual(folio.internal_comment, expected)

    def test_nothing_is_written_unless_enabled(self):
        self.mirai_ota.import_channel_notes = False
        with mock.patch.object(
            self.folio_model, "_get_channel_note_lines"
        ) as get_lines:
            folio = self._import(
                self._booking(1700000201, CLUB_EXISTING, UPSELLING)
            ).odoo_id
            modified = self._booking(
                1700000202, CLUB_NEW, UPSELLING, modified=[1700000201]
            )
            self.assertEqual(self._import(modified).odoo_id, folio)
        get_lines.assert_not_called()
        self.assertFalse(folio.internal_comment)

    def test_a_failure_neither_breaks_the_import_nor_repeats_the_warning(self):
        folio = self._import(self._booking(1700000301, CLUB_EXISTING)).odoo_id
        modified = self._booking(1700000302, CLUB_NEW, UPSELLING, modified=[1700000301])
        with mock.patch.object(
            self.folio_model,
            "_get_channel_note_lines",
            side_effect=ValueError("unexpected payload"),
        ), self.assertLogs(MAPPER_LOGGER, "ERROR") as logs:
            # The modification and two re-imports of it (push, cron...)
            for _import in range(3):
                self.assertEqual(self._import(modified).odoo_id, folio)
        # One error per import, none while the binder looks for the folio
        self.assertEqual(len(logs.records), 3)
        self.assertEqual(folio.internal_comment, note(mirai_line(CLUB_LINE_ES)))
        self.assertEqual(len(self._warnings(folio)), 1)

    def test_a_failure_on_creation_leaves_only_the_log(self):
        with mock.patch.object(
            self.folio_model,
            "_get_channel_note_lines",
            side_effect=ValueError("unexpected payload"),
        ), self.assertLogs(MAPPER_LOGGER, "ERROR") as logs:
            folio = self._import(self._booking(1700000401, CLUB_EXISTING)).odoo_id
        self.assertEqual(len(logs.records), 1)
        self.assertTrue(folio)
        self.assertFalse(folio.internal_comment)
        self.assertFalse(self._warnings(folio))
