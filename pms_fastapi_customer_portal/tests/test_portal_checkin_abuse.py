import hashlib
import json

from fastapi import status

from .common import MERGE_PATCH, CommonTestPortalCheckinUpdate

UNKNOWN_NUMBER = "NOBODY-0001"


class TestPortalCheckinAbuse(CommonTestPortalCheckinUpdate):
    """What a link holder may try with the guest PATCH.

    A document number held by another contact, a wildcard or a case variant of
    it answers exactly as an unused number does, so the PATCH tells nothing
    about the documents of other people.
    """

    def setUp(self):
        super().setUp()
        self.other_company = self.env["res.company"].create({"name": "Other co"})
        self.victim_folio = self._create_folio([(self.single, 4, 2, 1, 0)])
        self.victim = self.victim_folio.checkin_partner_ids
        self.victim.write({"firstname": "Victor", "document_number": "VIC999"})

    def _row_hash(self, record):
        self.env.flush_all()
        self.env.cr.execute(
            f"SELECT row_to_json(t)::text FROM {record._table} t WHERE id = %s",
            (record.id,),
        )
        return hashlib.sha256(self.env.cr.fetchone()[0].encode()).hexdigest()

    def _guest_with_own_contact(self):
        response = self._patch(self._complete_data("OWN-0001"))
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertTrue(self.guest.partner_id, "the guest got its own contact")
        return self.guest.partner_id

    def _assert_held_numbers_answer_alike(self, payloads, holder):
        """Each payload answers as the same one with an unused number does.

        The guest stores what was sent, keeps its own contact and copies no
        document that another contact holds; the holder is left untouched.
        """
        own_contact = self.guest.partner_id
        holder_before = self._row_hash(holder)
        document_before = self._row_hash(holder.id_numbers)
        for payload in payloads:
            with self.subTest(payload=payload):
                unknown = self._patch({**payload, "documentNumber": UNKNOWN_NUMBER})
                self.assertIn(UNKNOWN_NUMBER, own_contact.id_numbers.mapped("name"))

                held = self._patch(payload)

                self.assertEqual(
                    (held.status_code, held.text), (unknown.status_code, unknown.text)
                )
                self.assertEqual(held.status_code, status.HTTP_204_NO_CONTENT)
                self.assertEqual(self.guest.document_number, payload["documentNumber"])
                self.assertEqual(self.guest.partner_id, own_contact)
                self.assertNotIn(
                    payload["documentNumber"], own_contact.id_numbers.mapped("name")
                )
        self.assertEqual(self._row_hash(holder), holder_before)
        self.assertEqual(self._row_hash(holder.id_numbers), document_before)
        self.assertEqual(holder.id_numbers.mapped("name"), ["X1234"])

    # Document number oracle

    def test_a_document_held_by_another_contact_answers_like_an_unknown_one(self):
        holder = self._create_contact_with_document("X1234")
        self._guest_with_own_contact()

        known = self._patch({"documentNumber": "X1234"})
        unknown = self._patch({"documentNumber": "NOBODY-0002"})

        self.assertEqual(
            (known.status_code, known.text), (unknown.status_code, unknown.text)
        )
        self._assert_held_numbers_answer_alike(
            [{"documentNumber": number} for number in ("X1234", "x1234")],
            holder,
        )

    def test_a_wildcard_document_number_reveals_nothing(self):
        holder = self._create_contact_with_document("X1234", company=self.other_company)
        self._guest_with_own_contact()

        for pattern in ("X%", "X12_4", "%"):
            with self.subTest(pattern=pattern):
                match = self._patch({"documentNumber": pattern})
                none = self._patch({"documentNumber": "NOBODY-0002"})
                self.assertEqual(match.status_code, none.status_code, match.text)
        self._assert_held_numbers_answer_alike(
            [
                {"documentNumber": pattern}
                for pattern in ("X%", "x%", "X12_4", "_1234", "%", "_%")
            ],
            holder,
        )

    def test_a_document_type_change_answers_alike(self):
        other_category = self.env["res.partner.id_category"].create(
            {"name": "Other document", "code": "ODOC"}
        )
        holder = self._create_contact_with_document("X1234", category=other_category)
        self._guest_with_own_contact()

        self._assert_held_numbers_answer_alike(
            [
                {"documentType": other_category.id, "documentNumber": "X1234"},
                {
                    "documentType": other_category.id,
                    "documentCountry": self.portugal.id,
                    "documentNumber": "x1234",
                },
            ],
            holder,
        )

    def test_later_document_writes_answer_alike(self):
        """A guest that keeps the number of another contact copies its document
        again on every document write, and still answers as with its own."""
        holder = self._create_contact_with_document("X1234")
        holder_before = self._row_hash(holder)
        self._guest_with_own_contact()
        follow_ups = [
            {"documentCountry": self.portugal.id},
            {"documentType": self.category.id},
            {"firstname": "Ann", "email": "ann@example.org"},
        ]
        l10n_es = self.env["ir.module.module"]._get("pms_fastapi_l10n_es")
        if l10n_es.state == "installed":
            follow_ups.append({"documentSupportNumber": "AAA000001"})
        for follow_up in follow_ups:
            with self.subTest(follow_up=follow_up):
                self._patch({"documentNumber": UNKNOWN_NUMBER})
                unknown = self._patch(follow_up)
                self._patch({"documentNumber": "X1234"})
                held = self._patch(follow_up)

                self.assertEqual(
                    (held.status_code, held.text), (unknown.status_code, unknown.text)
                )
                self.assertEqual(held.status_code, status.HTTP_204_NO_CONTENT)
                self.assertEqual(self.guest.document_number, "X1234")
        self.assertEqual(self._row_hash(holder), holder_before)

    def test_a_guest_without_contact_answers_alike_for_any_number(self):
        """Once the other guest has its contact, ``%`` matches the documents
        of two contacts, which the document lookup does not expect."""
        holder = self._create_contact_with_document("X1234")
        holder_before = self._row_hash(holder)
        guests = self.folio.checkin_partner_ids.sorted("id")
        unknown = self._patch(self._complete_data(UNKNOWN_NUMBER), guests[1])
        self.assertEqual(unknown.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(guests[1].partner_id)

        for number in ("X1234", "x1234", "X%", "X12_4", "%"):
            with self.subTest(number=number):
                held = self._patch(self._complete_data(number), guests[0])

                self.assertEqual(
                    (held.status_code, held.text), (unknown.status_code, unknown.text)
                )
                self.assertEqual(guests[0].document_number, number)
                self.assertFalse(guests[0].partner_id)
        self.assertEqual(self._row_hash(holder), holder_before)

    def test_a_vat_held_by_a_contact_answers_like_an_unknown_number(self):
        """Identification types mapped to the VAT are also unique against the
        VAT of the contacts, which no document lookup finds."""
        vat_category = self.env["res.partner.id_category"].create(
            {"name": "Portal VAT", "code": "PVAT", "partner_map_field": "vat"}
        )
        holder = self.company.partner_id
        holder_before = self._row_hash(holder)
        first, second = self.folio.checkin_partner_ids.sorted("id")
        payload = dict(
            self._complete_data(""),
            documentType=vat_category.id,
            documentCountry=self.spain.id,
        )

        held = self._patch(dict(payload, documentNumber="11111111H"), first)
        unknown = self._patch(dict(payload, documentNumber="22222222J"), second)

        self.assertEqual(
            (held.status_code, held.text), (unknown.status_code, unknown.text)
        )
        self.assertEqual(held.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(first.document_number, "11111111H")
        self.assertTrue(first.partner_id)
        self.assertNotEqual(first.partner_id, holder)
        self.assertFalse(
            self.env["res.partner.id_number"].search([("name", "=", "11111111H")])
        )
        self.assertEqual(second.partner_id.id_numbers.mapped("name"), ["22222222J"])
        self.assertEqual(self._row_hash(holder), holder_before)

    # Scope

    def test_patch_of_another_folio_guest_changes_nothing(self):
        before = self._row_hash(self.victim)
        for guest_id in (self.victim.id, 0, -1, 2**63):
            with self.subTest(guest_id=guest_id):
                response = self._request(
                    "PATCH",
                    f"/portal/{self.folio.access_token}/checkin-partners/{guest_id}",
                    content=json.dumps({"firstname": "Pwned"}),
                    headers={"Content-Type": MERGE_PATCH},
                )
                self._assert_problem(response, 404, "/errors/guest-not-found")
        self.assertEqual(self._row_hash(self.victim), before)

    def test_fields_outside_the_staff_schema_are_rejected(self):
        before = self._row_hash(self.guest)
        for field, value in (
            ("partner_id", self.partner.id),
            ("state", "onboard"),
            ("portal_document_scan_count", 0),
            ("acceptedTerms", True),
        ):
            with self.subTest(field=field):
                response = self._patch({field: value})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self._row_hash(self.guest), before)

    def test_the_document_of_another_company_contact_is_not_linked(self):
        contact = self._create_contact_with_document(
            "Y5678", company=self.other_company
        )
        before = self._row_hash(contact)

        response = self._patch(self._complete_data("Y5678"))

        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        self.assertFalse(self.guest.partner_id)
        detail = self._get(self._url()).json()
        self.assertNotIn("email", detail)
        self.assertEqual(detail["firstname"], "Ann")
        self.assertEqual(self._row_hash(contact), before)

    def test_the_scan_counter_cannot_be_reset_through_the_patch(self):
        self.guest.flush_recordset()
        self.env.cr.execute(
            "UPDATE pms_checkin_partner SET portal_document_scan_count = 5 "
            "WHERE id = %s",
            (self.guest.id,),
        )
        self.guest.invalidate_recordset()
        for payload in (
            {"portal_document_scan_count": 0},
            {"portalDocumentScanCount": 0},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self._patch(payload).status_code, 422)
        self.assertEqual(self._patch({"firstname": "Reset"}).status_code, 204)
        response = self._request(
            "POST", f"{self._url()}/document-scan", json={"front": "ZnJvbnQ="}
        )
        self._assert_problem(response, 429, "/errors/document-scan-limit")
        self.assertEqual(self.guest.portal_document_scan_count, 5)

    def test_a_responsible_of_another_folio_is_rejected(self):
        l10n_es = self.env["ir.module.module"]._get("pms_fastapi_l10n_es")
        if l10n_es.state != "installed":
            self.skipTest("pms_fastapi_l10n_es is not installed")
        before = self._row_hash(self.guest)
        for responsible in (self.victim.id, self.guest.id):
            with self.subTest(responsible=responsible):
                response = self._patch({"responsibleCheckinPartnerId": responsible})
                self._assert_problem(response, 422, "/errors/unknown-responsible")
        self.assertEqual(self._row_hash(self.guest), before)
