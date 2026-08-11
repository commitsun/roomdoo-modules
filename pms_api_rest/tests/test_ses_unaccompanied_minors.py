# Copyright 2026 Commit [Sun]
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Minors travelling on their own with the authorization of their legal guardian
are declared through the guest resource, but the declaration and its
authorization document belong to the folio: the guardians may be booked in one
reservation and the minors in another one of the same folio.

These tests cover the API side of that mapping:

* the declaration is reported on and written through any guest of the folio,
* an omitted ``unaccompaniedMinors`` leaves the declaration alone, which is what
  keeps a request writing one guest from withdrawing a declaration made through
  another one,
* the declaration is stored even when the same request boards the guest, whose
  checkin data is only complete thanks to the declaration,
* the guardian authorization is a single document per folio, size capped.
"""
import base64
import datetime
import io
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from odoo.exceptions import MissingError, ValidationError
from odoo.tests import tagged

from odoo.addons.base_rest.controllers.main import _PseudoCollection
from odoo.addons.component.core import WorkContext
from odoo.addons.pms.tests.common import TestPms

from ..services.pms_reservation_service import MAX_MINORS_AUTHORIZATION_SIZE


@tagged("post_install", "-at_install")
class TestSesUnaccompaniedMinors(TestPms):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use admin (uid=1) so record rules do not get in the way; the endpoints
        # themselves sudo() their lookups. They do check the property of the
        # records against the caller's own properties, so the caller gets the
        # test property assigned, like a receptionist working on their hotel.
        cls.env = cls.env(user=cls.env["res.users"].browse(1))
        cls.env.user.write(
            {
                "company_ids": [(4, cls.company1.id)],
                "pms_property_ids": [(4, cls.pms_property1.id)],
            }
        )
        cls.room_type = cls.env["pms.room.type"].create(
            {
                "pms_property_ids": [cls.pms_property1.id],
                "name": "Double Minors",
                "default_code": "DBL_MIN",
                "class_id": cls.room_type_class1.id,
                "list_price": 30,
            }
        )
        for number in (101, 102):
            cls.env["pms.room"].create(
                {
                    "pms_property_id": cls.pms_property1.id,
                    "name": "Room %s" % number,
                    "room_type_id": cls.room_type.id,
                    "capacity": 2,
                }
            )
        cls.partner = cls.env["res.partner"].create({"name": "Booker"})
        cls.sale_channel = cls.env["pms.sale.channel"].create(
            {"name": "Direct minors", "channel_type": "direct"}
        )

    # ---- fixtures ----------------------------------------------------------

    def _service(self):
        collection = _PseudoCollection("pms.services", self.env)
        work = WorkContext(
            model_name="rest.service.registration", collection=collection
        )
        return work.component(usage="reservations")

    def _checkin_partner_info(self, **values):
        return self.env.datamodels["pms.checkin.partner.info"](**values)

    def _create_reservation(self, adults=1, folio=None):
        values = {
            "pms_property_id": self.pms_property1.id,
            "checkin": datetime.date.today(),
            "checkout": datetime.date.today() + datetime.timedelta(days=2),
            "adults": adults,
            "room_type_id": self.room_type.id,
            "partner_id": self.partner.id,
            "sale_channel_origin_id": self.sale_channel.id,
        }
        if folio:
            values["folio_id"] = folio.id
        return self.env["pms.reservation"].create(values)

    def _fill_minor(self, checkin_partner):
        """Complete every checkin datum a minor needs but the relationship.

        A guest born 10 years ago needs no identity document, and the country is
        not Spain so no state is required either. What is left missing is the
        relationship with an accompanying guest, which is exactly what the
        declaration waives.
        """
        checkin_partner.write(
            {
                "firstname": "Lone",
                "lastname": "Minor",
                "birthdate_date": datetime.date.today()
                - datetime.timedelta(days=365 * 10),
                "gender": "female",
                "nationality_id": self.env.ref("base.fr").id,
                "country_id": self.env.ref("base.fr").id,
                "street": "Rue de la Paix 1",
                "city": "Paris",
                "zip": "75002",
            }
        )
        return checkin_partner

    def _upload(self, reservation, checkin_partner, files):
        """Call the upload endpoint with the given multipart parts.

        The request is faked because the uploaded parts do not travel in the
        datamodel: base_rest keeps them out of ``request.params``, so the
        endpoint reads them from the request itself.
        """
        # ``new`` is given so mock does not inspect the original: ``request`` is
        # a proxy that raises as soon as it is touched with no request bound.
        with patch(
            "odoo.addons.pms_api_rest.services.pms_reservation_service.request",
            new=SimpleNamespace(httprequest=SimpleNamespace(files=files)),
        ):
            return self._service().upload_minors_authorization(
                reservation.id, checkin_partner.id
            )

    # ---- the declaration ---------------------------------------------------

    def test_declaration_is_reported_on_every_guest_of_the_folio(self):
        reservation = self._create_reservation()
        sibling = self._create_reservation(folio=reservation.folio_id)
        reservation.folio_id.ses_unaccompanied_minors = True

        for booked in (reservation, sibling):
            guests = self._service().get_checkin_partners(booked.id)
            self.assertTrue(guests)
            for guest in guests:
                self.assertTrue(guest.unaccompaniedMinors)

    def test_all_guests_minors_is_reported_when_every_guest_is_a_minor(self):
        reservation = self._create_reservation()
        self._fill_minor(reservation.checkin_partner_ids[0])

        guests = self._service().get_checkin_partners(reservation.id)
        self.assertTrue(guests[0].allGuestsMinors)

    def test_all_guests_minors_is_not_reported_when_a_guest_is_of_age(self):
        reservation = self._create_reservation()
        sibling = self._create_reservation(folio=reservation.folio_id)
        self._fill_minor(reservation.checkin_partner_ids[0])
        sibling.checkin_partner_ids[0].birthdate_date = (
            datetime.date.today() - datetime.timedelta(days=365 * 30)
        )

        # A guardian booked in another reservation of the same folio is what
        # makes the declaration meaningless, so it is not reported as available.
        guests = self._service().get_checkin_partners(reservation.id)
        self.assertFalse(guests[0].allGuestsMinors)

    def test_declaration_written_through_a_guest_lands_on_the_folio(self):
        reservation = self._create_reservation()
        checkin_partner = reservation.checkin_partner_ids[0]

        self._service().write_reservation_checkin_partner(
            reservation.id,
            checkin_partner.id,
            self._checkin_partner_info(
                firstname="Lone", lastname="Minor", unaccompaniedMinors=True
            ),
        )

        self.assertTrue(reservation.folio_id.ses_unaccompanied_minors)

    def test_omitted_declaration_is_left_untouched(self):
        reservation = self._create_reservation()
        sibling = self._create_reservation(folio=reservation.folio_id)
        reservation.folio_id.ses_unaccompanied_minors = True

        # A request that writes another guest of the folio and says nothing
        # about the declaration must not withdraw it.
        self._service().write_reservation_checkin_partner(
            sibling.id,
            sibling.checkin_partner_ids[0].id,
            self._checkin_partner_info(firstname="Other", lastname="Guest"),
        )

        self.assertTrue(reservation.folio_id.ses_unaccompanied_minors)

    def test_declaration_sent_as_false_is_withdrawn(self):
        reservation = self._create_reservation()
        reservation.folio_id.ses_unaccompanied_minors = True

        self._service().write_reservation_checkin_partner(
            reservation.id,
            reservation.checkin_partner_ids[0].id,
            self._checkin_partner_info(
                firstname="Lone", lastname="Minor", unaccompaniedMinors=False
            ),
        )

        self.assertFalse(reservation.folio_id.ses_unaccompanied_minors)

    def test_declaration_is_stored_before_boarding_the_guest(self):
        reservation = self._create_reservation()
        checkin_partner = self._fill_minor(reservation.checkin_partner_ids[0])

        # Declaring and boarding in a single request: the guest has no
        # relationship with an accompanying guest, so boarding only succeeds if
        # the declaration was stored first.
        self._service().write_reservation_checkin_partner(
            reservation.id,
            checkin_partner.id,
            self._checkin_partner_info(unaccompaniedMinors=True, actionOnBoard=True),
        )

        self.assertTrue(reservation.folio_id.ses_unaccompanied_minors)
        self.assertEqual(checkin_partner.state, "onboard")

    def test_declaration_written_when_completing_a_guest_slot(self):
        reservation = self._create_reservation(adults=2)

        self._service().create_reservation_checkin_partner(
            reservation.id,
            self._checkin_partner_info(
                firstname="Lone", lastname="Minor", unaccompaniedMinors=True
            ),
        )

        self.assertTrue(reservation.folio_id.ses_unaccompanied_minors)

    def test_read_only_data_is_not_part_of_the_write_contract(self):
        """The guest endpoints share a datamodel, and base_rest builds the same
        schema for the request and the response, so what is only reported must
        stay out of the one the write endpoints declare.
        """
        written = self.env.datamodels["pms.checkin.partner.info"].get_schema()
        reported = self.env.datamodels["pms.checkin.partner.info.output"].get_schema()

        for name in ("allGuestsMinors", "minorsAuthorizationFilename"):
            self.assertNotIn(name, written.fields)
            self.assertIn(name, reported.fields)
        self.assertIn("unaccompaniedMinors", written.fields)

    # ---- the guardian authorization ----------------------------------------

    def test_authorization_is_shared_by_the_guests_of_the_folio(self):
        reservation = self._create_reservation()
        sibling = self._create_reservation(folio=reservation.folio_id)
        service = self._service()

        service._store_minors_authorization(
            reservation.checkin_partner_ids[0], b"scanned pdf", "authorization.pdf"
        )

        # Uploaded through a guest of one reservation, readable through a guest
        # of the other one: there is a single document per folio.
        content, filename = service._read_minors_authorization(
            sibling.checkin_partner_ids[0]
        )
        self.assertEqual(content, b"scanned pdf")
        self.assertEqual(filename, "authorization.pdf")
        self.assertEqual(
            base64.b64decode(reservation.folio_id.ses_minors_authorization),
            b"scanned pdf",
        )

    def test_authorization_filename_is_reported_on_the_guest(self):
        reservation = self._create_reservation()
        checkin_partner = reservation.checkin_partner_ids[0]
        service = self._service()

        self.assertIsNone(
            service.get_checkin_partners(reservation.id)[0].minorsAuthorizationFilename
        )

        service._store_minors_authorization(
            checkin_partner, b"scanned pdf", "authorization.pdf"
        )

        self.assertEqual(
            service.get_checkin_partners(reservation.id)[0].minorsAuthorizationFilename,
            "authorization.pdf",
        )

    def test_authorization_is_uploaded_as_a_multipart_file(self):
        reservation = self._create_reservation()
        upload = FileStorage(
            stream=io.BytesIO(b"scanned pdf"), filename="authorization.pdf"
        )

        self._upload(reservation, reservation.checkin_partner_ids[0], {"file": upload})

        self.assertEqual(
            base64.b64decode(reservation.folio_id.ses_minors_authorization),
            b"scanned pdf",
        )
        self.assertEqual(
            reservation.folio_id.ses_minors_authorization_filename,
            "authorization.pdf",
        )

    def test_upload_without_the_file_part_is_rejected(self):
        reservation = self._create_reservation()
        with self.assertRaisesRegex(ValidationError, "file"):
            self._upload(reservation, reservation.checkin_partner_ids[0], {})

    def test_a_guest_of_another_reservation_is_not_found(self):
        reservation = self._create_reservation()
        other = self._create_reservation()
        with self.assertRaises(MissingError):
            self._service().delete_minors_authorization(
                reservation.id, other.checkin_partner_ids[0].id
            )

    def test_empty_authorization_is_rejected(self):
        reservation = self._create_reservation()
        with self.assertRaisesRegex(ValidationError, "empty"):
            self._service()._store_minors_authorization(
                reservation.checkin_partner_ids[0], b"", "authorization.pdf"
            )

    def test_oversized_authorization_is_rejected(self):
        reservation = self._create_reservation()
        with self.assertRaisesRegex(ValidationError, "larger"):
            self._service()._store_minors_authorization(
                reservation.checkin_partner_ids[0],
                b"x" * (MAX_MINORS_AUTHORIZATION_SIZE + 1),
                "authorization.pdf",
            )

    def test_reading_a_missing_authorization_is_not_found(self):
        reservation = self._create_reservation()
        with self.assertRaises(MissingError):
            self._service()._read_minors_authorization(
                reservation.checkin_partner_ids[0]
            )

    def test_deleting_the_authorization_clears_name_and_content(self):
        reservation = self._create_reservation()
        checkin_partner = reservation.checkin_partner_ids[0]
        service = self._service()
        service._store_minors_authorization(
            checkin_partner, b"scanned pdf", "authorization.pdf"
        )

        service.delete_minors_authorization(reservation.id, checkin_partner.id)

        self.assertFalse(reservation.folio_id.ses_minors_authorization)
        self.assertFalse(reservation.folio_id.ses_minors_authorization_filename)
