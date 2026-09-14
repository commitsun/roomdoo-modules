# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import ChannexConnectorCase


class ChannexFeedCase(ChannexConnectorCase):
    """Reading the feed and running what the read queues."""

    def _import(self, rounds=1):
        """Read the feed and run the jobs it queues.

        One round by default, which is taking the messages in. Acknowledging
        them is queued by those jobs, so it takes a round more, and most tests
        want it left alone: an acknowledged message is gone from the feed, and
        what a second read sees is half of what they are about.
        """
        with trap_jobs() as trap:
            result = self.backend.channex_import_booking_revisions()
            for _round in range(rounds):
                if not trap.enqueued_jobs:
                    break
                trap.perform_enqueued_jobs()
        return result

    def _revisions(self):
        return self.env["channel.channex.booking.revision"].search(
            [("backend_id", "=", self.backend.id)]
        )


@tagged("post_install", "-at_install")
class TestChannexBookingRevisions(ChannexFeedCase):
    """What Channex is holding for this property, as this property's messages."""

    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )
        self.property_uuid = self.server.store["properties"][0]["id"]

    def _seed_revision(self, external_id="r1", property_id=None, **values):
        self.server.seed(
            "booking_revisions",
            [
                {
                    "id": external_id,
                    "property_id": self.property_uuid
                    if property_id is None
                    else property_id,
                    "booking_id": "b1",
                    "unique_id": "BDC-3333333333",
                    "ota_reservation_code": "3333333333",
                    "ota_name": "BookingCom",
                    "channel_id": "85016ebd-a1aa-2b9f-abb9-4ad3a0857835",
                    "status": "new",
                    "arrival_date": "2026-11-13",
                    "departure_date": "2026-11-15",
                    "amount": "153.00",
                    "currency": "EUR",
                    "inserted_at": "2026-11-12T11:39:50.111087",
                    "acknowledge_status": "pending",
                    **values,
                }
            ],
        )

    def test_the_message_channex_is_holding_is_recorded(self):
        self._seed_revision()
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        revision = self._revisions()
        self.assertEqual(revision.external_id, "r1")
        self.assertEqual(revision.booking_id, "b1")
        self.assertEqual(revision.unique_id, "BDC-3333333333")
        self.assertEqual(revision.ota_reservation_code, "3333333333")
        self.assertEqual(revision.ota_name, "BookingCom")
        self.assertEqual(revision.channel_id, "85016ebd-a1aa-2b9f-abb9-4ad3a0857835")
        self.assertEqual(revision.status, "new")
        self.assertEqual(str(revision.arrival_date), "2026-11-13")
        self.assertEqual(str(revision.departure_date), "2026-11-15")
        self.assertEqual(revision.amount, 153.0)
        self.assertEqual(revision.currency, "EUR")
        # Channex issues the timestamp in ISO 8601 UTC, Odoo stores it plain.
        self.assertEqual(str(revision.inserted_at), "2026-11-12 11:39:50")

    def test_the_repeated_message_stays_one_row(self):
        """The feed keeps offering an unacknowledged revision, so reading it
        again has to be harmless. It is looked at again -- what stopped it may
        be gone -- but it is the same message and so the same row."""
        self._seed_revision()
        self._import()
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertEqual(len(self._revisions()), 1)

    def test_a_later_revision_of_the_same_booking_is_its_own_row(self):
        self._seed_revision()
        self._import()
        self._seed_revision(external_id="r2", status="cancelled")
        self.assertEqual(self._import(), {"total": 2, "queued": 2})
        self.assertEqual(self._revisions().mapped("status"), ["cancelled", "new"])

    def test_the_read_ignores_revisions_of_another_property(self):
        """One API key reaches every property of its account, so the property
        filter is the scoping."""
        self._seed_revision(external_id="other", property_id="another-property")
        self.assertEqual(self._import(), {"total": 0, "queued": 0})
        self.assertFalse(self._revisions())

    def test_the_read_covers_every_page_of_the_feed(self):
        """The feed reports ``total`` where the rest of the API reports
        ``total_pages``."""
        self.backend.page_limit = 2
        for number in range(5):
            self._seed_revision(external_id=f"r{number}", booking_id=f"b{number}")
        self.assertEqual(self._import(), {"total": 5, "queued": 5})
        self.assertEqual(len(self._revisions()), 5)
        self.assertEqual(len(self.server.calls_to("GET", "booking_revisions")), 3)

    def test_the_read_asks_only_for_what_is_pending_here(self):
        """One API key reaches every property of its account, and a message
        already acknowledged is one already dealt with."""
        self._seed_revision()
        self._import()
        params = self.server.calls_to("GET", "booking_revisions")[0][3]
        self.assertEqual(params["filter[property_id]"], self.property_uuid)
        self.assertEqual(params["filter[acknowledge_status]"], "pending")
        self.assertEqual(params["order[inserted_at]"], "asc")


class ChannexBookingCase(ChannexFeedCase):
    """A property with one mapped room type, and messages to feed it."""

    def setUp(self):
        super().setUp()
        self._bind_and_export("channel.channex.pms.property", self.pms_property)
        self.room_type_binding = self._bind_and_export(
            "channel.channex.pms.room.type", self.room_type
        )
        self.property_uuid = self.server.store["properties"][0]["id"]
        self.room_type_uuid = self.room_type_binding.external_id

    def _bind_and_export(self, model, record):
        binding = (
            self.env[model]
            .with_context(connector_no_export=True)
            .create({"odoo_id": record.id, "backend_id": self.backend.id})
        )
        self.env[model].export_record(self.backend, record)
        binding.invalidate_recordset()
        return binding

    def _room(self, **values):
        return {
            "room_type_id": self.room_type_uuid,
            "rate_plan_id": "rp-1",
            "checkin_date": "2026-11-13",
            "checkout_date": "2026-11-15",
            "amount": "153.00",
            "days": {"2026-11-13": "76.50", "2026-11-14": "76.50"},
            "occupancy": {"adults": 2, "children": 1, "infants": 1},
            **values,
        }

    def _seed_booking(self, external_id="r1", rooms=None, **values):
        self.server.seed(
            "booking_revisions",
            [
                {
                    "id": external_id,
                    "property_id": self.property_uuid,
                    "booking_id": "b1",
                    "unique_id": "BDC-3333333333",
                    "ota_reservation_code": "3333333333",
                    "ota_name": "BookingCom",
                    "channel_id": "chn-1",
                    "status": "new",
                    "arrival_hour": "18:00",
                    "notes": "Quiet room, please",
                    "customer": {
                        "name": "User",
                        "surname": "Channex",
                        "mail": "user@channex.io",
                        "phone": "1234567890",
                        "language": "en",
                    },
                    "occupancy": {"adults": 2, "children": 1, "infants": 1},
                    "arrival_date": "2026-11-13",
                    "departure_date": "2026-11-15",
                    "amount": "153.00",
                    "currency": "EUR",
                    "inserted_at": "2026-11-12T11:39:50.111087",
                    "acknowledge_status": "pending",
                    "rooms": rooms if rooms is not None else [self._room()],
                    **values,
                }
            ],
        )

    def _revision(self, external_id="r1"):
        return self.env["channel.channex.booking.revision"].search(
            [("backend_id", "=", self.backend.id), ("external_id", "=", external_id)]
        )

    def _folios(self):
        return self.env["channel.channex.pms.folio"].search(
            [("backend_id", "=", self.backend.id)]
        )

    def _invoice(self, folio):
        """Invoice the folio for real: what the guard reads is ``move_ids``, and
        that is computed from the invoice lines of the folio's own sale lines."""
        receivable = self._setup_accounting()
        folio.partner_id = (
            self.env["res.partner"]
            .with_company(self.company)
            .create(
                {"name": "A guest", "property_account_receivable_id": receivable.id}
            )
        )
        return folio._create_invoices(partner_invoice_id=folio.partner_id.id)

    def _setup_accounting(self):
        """A company created by a test has no chart of accounts, and an invoice
        needs the little of one that it touches."""
        accounts = self.env["account.account"]
        income = accounts.create(
            {
                "name": "Channex income",
                "code": "CHX700",
                "account_type": "income",
                "company_id": self.company.id,
            }
        )
        receivable = accounts.create(
            {
                "name": "Channex receivable",
                "code": "CHX430",
                "account_type": "asset_receivable",
                "reconcile": True,
                "company_id": self.company.id,
            }
        )
        # pms picks the journal off the property, and the two it looks for are
        # these: whom the invoice is for decides which.
        journals = self.env["account.journal"].create(
            [
                {
                    "name": "Channex invoices",
                    "code": "CHXI",
                    "type": "sale",
                    "company_id": self.company.id,
                },
                {
                    "name": "Channex simplified invoices",
                    "code": "CHXSI",
                    "type": "sale",
                    "is_simplified_invoice": True,
                    "company_id": self.company.id,
                },
            ]
        )
        self.pms_property.write(
            {
                "journal_normal_invoice_id": journals[0].id,
                "journal_simplified_invoice_id": journals[1].id,
            }
        )
        self.room_type.product_id.with_company(
            self.company
        ).property_account_income_id = income
        return receivable


@tagged("post_install", "-at_install")
class TestChannexBookingImport(ChannexBookingCase):
    """From message to folio, for a new booking."""

    def test_new_booking_becomes_a_folio(self):
        self._seed_booking()
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        binding = self._folios()
        self.assertEqual(len(binding), 1)
        # Bound on the booking, not on the message.
        self.assertEqual(binding.external_id, "b1")
        self.assertEqual(binding.revision_external_id, "r1")
        self.assertEqual(str(binding.revision_inserted_at), "2026-11-12 11:39:50")
        folio = binding.odoo_id
        self.assertEqual(folio.pms_property_id, self.pms_property)
        self.assertEqual(folio.partner_name, "Channex, User")
        self.assertEqual(folio.email, "user@channex.io")
        self.assertEqual(folio.mobile, "1234567890")
        self.assertEqual(self._revision().state, "applied")

    def test_the_room_becomes_a_reservation_priced_as_the_ota_sold_it(self):
        self._seed_booking()
        self._import()
        reservation = self._folios().odoo_id.reservation_ids
        self.assertEqual(len(reservation), 1)
        self.assertEqual(reservation.room_type_id, self.room_type)
        self.assertEqual(str(reservation.checkin), "2026-11-13")
        self.assertEqual(str(reservation.checkout), "2026-11-15")
        self.assertEqual(reservation.adults, 2)
        # Infants are not children: Channex counts them apart because they take
        # no place.
        self.assertEqual(reservation.children, 1)
        # Stated once for the booking, needed on the reservation.
        self.assertEqual(reservation.ota_reservation_code, "3333333333")
        self.assertEqual(reservation.arrival_hour, "18:00")
        self.assertEqual(reservation.partner_requests, "Quiet room, please")
        lines = reservation.reservation_line_ids.sorted("date")
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [str(line.date) for line in lines], ["2026-11-13", "2026-11-14"]
        )
        self.assertEqual(lines.mapped("price"), [76.5, 76.5])

    def test_a_breakdown_that_does_not_cover_the_stay_is_not_applied(self):
        """No price is ever derived here: what the OTA sold is what gets
        written, or nothing gets written."""
        self._seed_booking(rooms=[self._room(days={"2026-11-13": "153.00"})])
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertFalse(self._folios())
        self.assertIn("2026-11-14", self._revision().error)

    def test_a_breakdown_reaching_outside_the_stay_is_not_applied(self):
        """Dropping a night the OTA priced would be dropping money silently."""
        self._seed_booking(
            rooms=[
                self._room(
                    days={
                        "2026-11-13": "76.50",
                        "2026-11-14": "76.50",
                        "2026-11-15": "76.50",
                    }
                )
            ]
        )
        self._import()
        self.assertFalse(self._folios())
        self.assertIn("2026-11-15", self._revision().error)

    def test_a_night_the_ota_gave_away_is_not_applied(self):
        """pms reads a price of zero as "not priced yet" and would replace it
        with the one from its pricelist, so it cannot be written as it came."""
        self._seed_booking(
            rooms=[self._room(days={"2026-11-13": "153.00", "2026-11-14": "0.00"})]
        )
        self._import()
        self.assertFalse(self._folios())
        self.assertIn("2026-11-14", self._revision().error)

    def test_the_reservation_is_blocked(self):
        self._seed_booking()
        self._import()
        self.assertTrue(self._folios().odoo_id.reservation_ids.blocked)

    def test_two_rooms_become_two_reservations(self):
        self._seed_booking(
            rooms=[
                self._room(),
                self._room(checkout_date="2026-11-14", days={"2026-11-13": "80.00"}),
            ]
        )
        self._import()
        reservations = self._folios().odoo_id.reservation_ids
        self.assertEqual(len(reservations), 2)
        self.assertEqual(
            sorted(str(checkout) for checkout in reservations.mapped("checkout")),
            ["2026-11-14", "2026-11-15"],
        )

    # -- idempotence -------------------------------------------------------

    def test_the_same_message_twice_leaves_one_folio(self):
        """Nothing is acknowledged, so the feed hands the message over again."""
        self._seed_booking()
        self._import()
        # Not queued again: there is nothing left to make of it.
        self.assertEqual(self._import(), {"total": 1, "queued": 0})
        self.assertEqual(len(self._folios()), 1)
        self.assertEqual(len(self._folios().odoo_id.reservation_ids), 1)
        self.assertEqual(self._revision().state, "applied")

    def test_a_message_older_than_the_folio_is_not_applied(self):
        self._seed_booking()
        self._import()
        self._seed_booking(external_id="r0", inserted_at="2026-11-12T09:00:00.000000")
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(self._revision("r0").state, "superseded")
        self.assertEqual(self._folios().revision_external_id, "r1")

    # -- what does not become a folio --------------------------------------

    def test_an_unmapped_room_type_leaves_the_message_unapplied(self):
        self._seed_booking(rooms=[self._room(room_type_id="not-mapped")])
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertFalse(self._folios())
        revision = self._revision()
        self.assertEqual(revision.state, "error")
        self.assertIn("not-mapped", revision.error)

    def test_a_message_of_an_unknown_status_is_reported(self):
        """Channex has three, and a fourth would mean something we have not been
        told about. Reported rather than guessed at."""
        self._seed_booking(status="reinstated")
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertFalse(self._folios())
        self.assertEqual(self._revision().state, "error")
        self.assertIn("reinstated", self._revision().error)

    def test_a_modification_of_a_booking_we_never_saw_becomes_a_folio(self):
        """A modification is a whole snapshot of the booking, not a diff, so it
        can be taken in on its own. It happens when the message that created the
        booking was acknowledged before this connector existed."""
        self._seed_booking(status="modified")
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertEqual(len(self._folios().odoo_id.reservation_ids), 1)

    def test_a_message_with_no_booking_id_is_reported(self):
        self._seed_booking(booking_id=None)
        self._import()
        self.assertFalse(self._folios())
        self.assertEqual(self._revision().state, "error")

    def test_a_message_that_could_not_be_applied_is_retried(self):
        """It stays unacknowledged, so the next read brings it back. Mapping the
        room type is all it takes."""
        self._seed_booking(rooms=[self._room(room_type_id="not-mapped")])
        self._import()
        self.server.store["booking_revisions"][0]["rooms"] = [self._room()]
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertEqual(len(self._folios()), 1)
        self.assertEqual(self._revision().state, "applied")

    # -- the agency --------------------------------------------------------

    def _seed_channel(self, agency=None, active=True):
        channel = self.env["channel.channex.channel"].create(
            {
                "backend_id": self.backend.id,
                "external_id": "chn-1",
                "code": "BookingCom",
                "title": "BookingCom - Channex Property",
                "active": active,
                "ota_id": self.env["channel.channex.ota"]
                ._channex_of_code("BookingCom")
                .id,
            }
        )
        if agency:
            channel.agency_id = agency
        return channel

    def _agency(self):
        sale_channel = self.env["pms.sale.channel"].create(
            {"name": "An OTA", "channel_type": "indirect"}
        )
        # An agency in pms needs an indirect sale channel and a pricelist that
        # is available for pms, which in turn needs an availability plan.
        self.pricelist.write(
            {
                "is_pms_available": True,
                "availability_plan_id": self.env["pms.availability.plan"]
                .create({"name": "Channex AP"})
                .id,
            }
        )
        # The pricelist of a partner is per company, so the agency has to be
        # created in the one this property belongs to.
        agency = (
            self.env["res.partner"]
            .with_company(self.company)
            .create(
                {
                    "name": "An agency",
                    "is_agency": True,
                    "sale_channel_id": sale_channel.id,
                    "property_product_pricelist": self.pricelist.id,
                }
            )
        )
        return agency, sale_channel

    def test_the_agency_comes_from_the_channel_of_the_message(self):
        agency, sale_channel = self._agency()
        self._seed_channel(agency=agency)
        self._seed_booking()
        self._import()
        folio = self._folios().odoo_id
        self.assertEqual(folio.agency_id, agency)
        self.assertEqual(folio.sale_channel_origin_id, sale_channel)

    def test_a_channel_gone_from_channex_still_names_the_agency(self):
        """A channel that disappears is deactivated and never deleted, so what
        it sold keeps resolving to its partner."""
        agency, _sale_channel = self._agency()
        self._seed_channel(agency=agency, active=False)
        self._seed_booking()
        self._import()
        self.assertEqual(self._folios().odoo_id.agency_id, agency)

    def test_a_channel_with_no_agency_does_not_hold_up_the_booking(self):
        """Losing a booking is worse than not knowing whose it is."""
        self._seed_channel()
        self._seed_booking()
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertFalse(self._folios().odoo_id.agency_id)

    def test_an_unknown_channel_does_not_hold_up_the_booking_either(self):
        self._seed_booking(channel_id="never-seen")
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertFalse(self._folios().odoo_id.agency_id)


@tagged("post_install", "-at_install")
class TestChannexBookingModification(ChannexBookingCase):
    """From message to folio, for a booking that changed after it was sold.

    Channex issues a whole new snapshot of the booking, and none of the ids it
    puts on a room survive it, so which reservation of the folio each room of
    the message is has to be deduced. A room recognisably one of them updates
    it; one that is not is a reservation being added; and a reservation no room
    accounts for is one the booking no longer has.
    """

    def setUp(self):
        super().setUp()
        self._seed_booking()
        self._import()
        self.folio = self._folios().odoo_id
        self.reservation = self.folio.reservation_ids

    def _seed_modification(self, rooms=None, **values):
        """The same booking, changed, as Channex hands it over: a second
        message, later, with the booking id of the first."""
        return self._seed_booking(
            external_id="r2",
            status="modified",
            inserted_at="2026-11-12T18:20:00.000000",
            rooms=rooms,
            **values,
        )

    # -- a change the reservation can take ----------------------------------

    def test_a_new_price_lands_on_the_reservation_that_is_there(self):
        """Same room type and same nights: the same reservation, repriced. The
        one thing that must not happen is a second reservation for the stay the
        hotel is already holding."""
        self._seed_modification(
            rooms=[self._room(days={"2026-11-13": "70.00", "2026-11-14": "80.00"})]
        )
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(self.folio.reservation_ids, self.reservation)
        lines = self.reservation.reservation_line_ids.sorted("date")
        self.assertEqual(lines.mapped("price"), [70.0, 80.0])

    def test_the_folio_is_left_pointing_at_the_message_applied(self):
        self._seed_modification(rooms=[self._room()], customer={"name": "Other"})
        self._import()
        binding = self._folios()
        self.assertEqual(binding.revision_external_id, "r2")
        self.assertEqual(str(binding.revision_inserted_at), "2026-11-12 18:20:00")
        self.assertEqual(self._revision("r2").state, "applied")

    def test_a_new_guest_name_is_written(self):
        self._seed_modification(
            rooms=[self._room()],
            customer={"name": "Someone", "surname": "Else"},
        )
        self._import()
        self.assertEqual(self.folio.partner_name, "Else, Someone")

    # -- a change the reservation cannot take -------------------------------

    def test_a_new_departure_replaces_the_reservation(self):
        """The nights of a stay are what the reservation is, so a stay of other
        nights is another reservation. The one it replaces is cancelled, never
        deleted: it is what the hotel worked with until now."""
        self._seed_modification(
            rooms=[
                self._room(
                    checkout_date="2026-11-16",
                    days={
                        "2026-11-13": "76.50",
                        "2026-11-14": "76.50",
                        "2026-11-15": "80.00",
                    },
                )
            ]
        )
        self._import()
        self.assertEqual(self.reservation.state, "cancel")
        # No penalty: the guest did not cancel anything.
        self.assertEqual(self.reservation.cancelled_reason, "modified")
        live = self.folio.reservation_ids - self.reservation
        self.assertEqual(len(live), 1)
        self.assertEqual(str(live.checkout), "2026-11-16")
        self.assertEqual(
            live.reservation_line_ids.sorted("date").mapped("price"),
            [76.5, 76.5, 80.0],
        )

    def test_the_reservation_a_modification_creates_is_blocked_too(self):
        """The mappings that only run on creation do not run on this path by
        themselves, and the block against manual edits is one of them."""
        self._seed_modification(
            rooms=[self._room(checkout_date="2026-11-14", days={"2026-11-13": "76.50"})]
        )
        self._import()
        live = self.folio.reservation_ids.filtered(lambda r: r.state != "cancel")
        self.assertTrue(live.blocked)
        self.assertEqual(live.pms_property_id, self.pms_property)

    def test_the_folio_of_a_replaced_reservation_stays_confirmed(self):
        """Cancelling the last live reservation of a folio cancels the folio, and
        the reservation replacing it arrives in the same message."""
        self._seed_modification(
            rooms=[self._room(checkout_date="2026-11-14", days={"2026-11-13": "76.50"})]
        )
        self._import()
        self.assertEqual(self.folio.state, "confirm")

    def test_a_room_the_message_cancels_leaves_its_reservation_cancelled(self):
        """One room of several dropped, the rest of the booking standing."""
        self._seed_booking(
            external_id="r0",
            booking_id="b2",
            inserted_at="2026-11-12T11:00:00.000000",
            rooms=[
                self._room(),
                self._room(checkout_date="2026-11-14", days={"2026-11-13": "80.00"}),
            ],
        )
        self._import()
        folio = self._folios().filtered(lambda b: b.external_id == "b2").odoo_id
        self._seed_booking(
            external_id="r3",
            booking_id="b2",
            status="modified",
            inserted_at="2026-11-12T19:00:00.000000",
            rooms=[
                self._room(),
                self._room(
                    checkout_date="2026-11-14",
                    days={"2026-11-13": "80.00"},
                    is_cancelled=True,
                ),
            ],
        )
        self._import()
        dropped = folio.reservation_ids.filtered(
            lambda r: str(r.checkout) == "2026-11-14"
        )
        self.assertEqual(dropped.state, "cancel")
        self.assertEqual(dropped.cancelled_reason, "modified")
        kept = folio.reservation_ids - dropped
        self.assertEqual(kept.state, "confirm")

    def test_a_stay_already_under_way_holds_up_the_change(self):
        """pms refuses to cancel a reservation the guest is in or has already
        had, so nobody is thrown out of a room by a message: it is reported and
        a person decides."""
        self.reservation.state = "done"
        self._seed_modification(
            rooms=[
                self._room(
                    checkout_date="2026-11-16",
                    days={
                        "2026-11-13": "76.50",
                        "2026-11-14": "76.50",
                        "2026-11-15": "80.00",
                    },
                )
            ]
        )
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(self._revision("r2").state, "error")
        # Nothing half done: the folio is as it was.
        self.assertEqual(self.folio.reservation_ids, self.reservation)
        self.assertEqual(str(self.reservation.checkout), "2026-11-15")

    # -- money already on paper --------------------------------------------

    def test_an_invoiced_folio_holds_up_the_change(self):
        invoice = self._invoice(self.folio)
        invoice.action_post()
        self._seed_modification(
            rooms=[self._room(days={"2026-11-13": "70.00", "2026-11-14": "80.00"})]
        )
        self._import()
        self.assertEqual(self._revision("r2").state, "error")
        self.assertIn(invoice.name, self._revision("r2").error)
        self.assertEqual(
            self.reservation.reservation_line_ids.sorted("date").mapped("price"),
            [76.5, 76.5],
        )

    def test_a_draft_invoice_is_deleted_and_the_change_applied(self):
        """It says what was sold before the change, so it cannot stand."""
        invoice = self._invoice(self.folio)
        self.assertEqual(invoice.state, "draft")
        self._seed_modification(
            rooms=[self._room(days={"2026-11-13": "70.00", "2026-11-14": "80.00"})]
        )
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        self.assertFalse(invoice.exists())
        self.assertEqual(
            self.reservation.reservation_line_ids.sorted("date").mapped("price"),
            [70.0, 80.0],
        )


@tagged("post_install", "-at_install")
class TestChannexBookingCancellation(ChannexBookingCase):
    """A booking called off at the OTA.

    Channex says so in the status of the message and nowhere else: the rooms of
    a cancellation come over with their dates and their prices and their
    ``is_cancelled`` false, which is checked against staging and not assumed. So
    a cancellation is not a snapshot to write, it is a state to reach -- and it
    has to reach it whatever the message looks like, because a cancellation that
    does not land is a room the hotel keeps blocked and a guest charged for a
    stay they called off.
    """

    def _seed_cancellation(self, external_id="r2", **values):
        return self._seed_booking(
            external_id=external_id,
            status="cancelled",
            inserted_at="2026-11-12T18:20:00.000000",
            **values,
        )

    def _booked(self):
        """The booking, sold and in the folio, before anyone calls it off."""
        self._seed_booking()
        self._import()
        folio = self._folios().odoo_id
        return folio, folio.reservation_ids

    def _cancelation_rule(self):
        """The hotel's own terms for this pricelist, which is what a guest
        cancelling is charged by."""
        rule = self.env["pms.cancelation.rule"].create(
            {
                "name": "Channex CR",
                # Every cancellation of these tests falls inside the window.
                "days_intime": 3650,
                "penalty_late": 100,
                "apply_on_late": "first",
            }
        )
        self.pricelist.cancelation_rule_id = rule
        return rule

    # -- a booking the hotel has -------------------------------------------

    def test_the_booking_is_cancelled(self):
        folio, reservation = self._booked()
        self._seed_cancellation()
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(reservation.state, "cancel")
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(self._revision("r2").state, "applied")

    def test_the_guest_is_charged_by_the_rule_of_the_pricelist(self):
        """Not cancelled as ``modified``: that reason is for a reservation our
        own import supersedes, and it is what tells pms to charge nothing. A
        guest calling off a stay is charged whatever the hotel said they would
        be."""
        self._cancelation_rule()
        folio, reservation = self._booked()
        self._seed_cancellation()
        self._import()
        self.assertEqual(reservation.cancelled_reason, "late")
        penalty = folio.service_ids.filtered(lambda s: s.reservation_id == reservation)
        self.assertEqual(len(penalty), 1)
        self.assertEqual(penalty.service_line_ids.price_unit, 76.5)

    def test_the_folio_is_left_pointing_at_the_cancellation(self):
        """So a message delivered after it cannot bring the booking back."""
        self._booked()
        self._seed_cancellation()
        self._import()
        binding = self._folios()
        self.assertEqual(binding.revision_external_id, "r2")
        self.assertEqual(str(binding.revision_inserted_at), "2026-11-12 18:20:00")

    def test_the_prices_the_hotel_sold_at_are_not_touched(self):
        """Channex hands cancelled bookings of some OTAs over with their rates
        zeroed, and the prices in the folio are what the charge is worked out
        from. A cancellation restates nothing."""
        folio, reservation = self._booked()
        self._seed_cancellation(
            rooms=[self._room(days={"2026-11-13": "0.00", "2026-11-14": "0.00"})]
        )
        self._import()
        self.assertEqual(
            reservation.reservation_line_ids.sorted("date").mapped("price"),
            [76.5, 76.5],
        )
        self.assertEqual(reservation.state, "cancel")

    def test_the_stay_is_not_restated_either(self):
        folio, reservation = self._booked()
        self._seed_cancellation(
            rooms=[
                self._room(
                    checkout_date="2026-11-20",
                    days={f"2026-11-{day}": "10.00" for day in range(13, 20)},
                )
            ]
        )
        self._import()
        self.assertEqual(folio.reservation_ids, reservation)
        self.assertEqual(str(reservation.checkout), "2026-11-15")

    def test_a_breakdown_that_does_not_add_up_still_cancels(self):
        """The same message as a new booking would be refused. Nothing is
        allowed to hold up a cancellation, least of all a price nobody is
        going to be charged."""
        folio, reservation = self._booked()
        self._seed_cancellation(rooms=[self._room(days={"2026-11-13": "76.50"})])
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        self.assertEqual(reservation.state, "cancel")

    def test_a_modification_that_cancels_every_room_is_a_cancellation(self):
        """It says the same thing the other way round."""
        folio, reservation = self._booked()
        self._cancelation_rule()
        self._seed_booking(
            external_id="r2",
            status="modified",
            inserted_at="2026-11-12T18:20:00.000000",
            rooms=[self._room(is_cancelled=True)],
        )
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(reservation.cancelled_reason, "late")

    def test_a_stay_already_under_way_is_not_cancelled(self):
        """pms refuses to cancel a stay the guest is in or has already had, and
        nobody is taken out of a room by a message: it is reported and a person
        decides."""
        folio, reservation = self._booked()
        reservation.state = "done"
        self._seed_cancellation()
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(self._revision("r2").state, "error")
        self.assertEqual(reservation.state, "done")
        self.assertNotEqual(folio.state, "cancel")

    # -- a booking the hotel does not have ---------------------------------

    def test_a_cancellation_of_a_booking_we_never_saw_still_lands(self):
        """Its first message was acknowledged before this connector existed, or
        the job carrying it has not run yet. Either way the hotel has to see the
        cancellation: we reflect what the OTA sold, we do not decide which
        bookings existed."""
        self._seed_cancellation(external_id="r1")
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        binding = self._folios()
        self.assertEqual(len(binding), 1)
        self.assertEqual(binding.external_id, "b1")
        folio = binding.odoo_id
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(folio.partner_name, "Channex, User")
        self.assertEqual(len(folio.reservation_ids), 1)
        self.assertEqual(folio.reservation_ids.state, "cancel")
        self.assertEqual(
            folio.reservation_ids.reservation_line_ids.sorted("date").mapped("price"),
            [76.5, 76.5],
        )

    def test_it_lands_even_with_no_prices_at_all(self):
        """This message is the only account of the booking there will ever be,
        so it goes in as it came. What pms puts on the nights it does not price
        is a price nobody will be charged: the stay is not happening."""
        self._seed_cancellation(external_id="r1", rooms=[self._room(days={})])
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertEqual(self._revision("r1").state, "applied")
        folio = self._folios().odoo_id
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(len(folio.reservation_ids.reservation_line_ids), 2)

    def test_the_booking_arriving_afterwards_does_not_bring_it_back(self):
        """Jobs run in any order, so the cancellation can land before the
        message that sold the booking. The folio carries which message it is at,
        and the older one is recognised as older."""
        self._seed_cancellation(external_id="r2")
        self._import()
        self._seed_booking()
        self.assertEqual(self._import(), {"total": 2, "queued": 1})
        self.assertEqual(self._revision("r1").state, "superseded")
        folio = self._folios().odoo_id
        self.assertEqual(folio.state, "cancel")
        self.assertEqual(len(folio.reservation_ids), 1)

    def test_an_unmapped_room_type_holds_up_a_cancellation_of_an_unknown_booking(self):
        """The one thing this message does have to be able to do is record the
        booking, and it cannot."""
        self._seed_cancellation(
            external_id="r1", rooms=[self._room(room_type_id="not-mapped")]
        )
        self._import()
        self.assertEqual(self._revision("r1").state, "error")
        self.assertFalse(self._folios())

    def test_a_cancellation_carrying_no_room_at_all_is_reported(self):
        self._seed_cancellation(external_id="r1", rooms=[])
        self._import()
        self.assertEqual(self._revision("r1").state, "error")
        self.assertFalse(self._folios())

    # -- money already on paper --------------------------------------------

    def test_an_invoiced_folio_holds_up_the_cancellation(self):
        """The invoice says the stay was sold, and cancelling under it would
        leave it saying so. Someone has to issue a credit note."""
        folio, reservation = self._booked()
        invoice = self._invoice(folio)
        invoice.action_post()
        self._seed_cancellation()
        self._import()
        self.assertEqual(self._revision("r2").state, "error")
        self.assertIn(invoice.name, self._revision("r2").error)
        self.assertNotEqual(reservation.state, "cancel")

    def test_a_draft_invoice_is_deleted_and_the_cancellation_applied(self):
        folio, reservation = self._booked()
        invoice = self._invoice(folio)
        self.assertEqual(invoice.state, "draft")
        self._seed_cancellation()
        self._import()
        self.assertEqual(self._revision("r2").state, "applied")
        self.assertFalse(invoice.exists())
        self.assertEqual(reservation.state, "cancel")


@tagged("post_install", "-at_install")
class TestChannexBookingAck(ChannexBookingCase):
    """Acknowledging what was taken in.

    Channex hands a message over until it is acknowledged, and never again
    after. That makes the receipt the one irreversible step of the import, so it
    is sent as a job: a job only exists once the transaction that queued it
    commits, and never for a folio that was rolled back.
    """

    def _acks(self):
        return [
            call
            for call in self.server.calls_to("POST", "booking_revisions")
            if call[1].endswith("/ack")
        ]

    def _import(self, rounds=2):
        """Two rounds: taking the message in, and then the receipt that queues.

        ``self.receipts`` is left holding what the last round queued and did not
        run, which is how a test can look at a receipt before it is sent.
        """
        sent = len(self._acks())
        self.receipts = []
        with trap_jobs() as trap:
            result = self.backend.channex_import_booking_revisions()
            self.assertEqual(
                len(self._acks()), sent, "the read itself must send no receipt"
            )
            for _round in range(rounds):
                if not trap.enqueued_jobs:
                    break
                trap.perform_enqueued_jobs()
                self.receipts = trap.enqueued_jobs
        return result

    def test_a_saved_message_is_acknowledged(self):
        self._seed_booking()
        self._import()
        self.assertEqual(len(self._acks()), 1)
        self.assertEqual(self._acks()[0][1], "booking_revisions/r1/ack")
        self.assertTrue(self._revision().acknowledged_at)

    def test_the_receipt_is_a_job_of_its_own(self):
        """So that it cannot be sent before the folio is saved for good."""
        self._seed_booking()
        self._import(rounds=1)
        self.assertEqual(self._revision().state, "applied")
        self.assertEqual(len(self.receipts), 1)
        self.assertFalse(self._acks())
        self.assertFalse(self._revision().acknowledged_at)

    def test_a_message_that_could_not_be_applied_is_not_acknowledged(self):
        """It has to keep coming back until the hotel can take it in."""
        self._seed_booking(rooms=[self._room(room_type_id="not-mapped")])
        self._import()
        self.assertFalse(self._acks())
        self.assertFalse(self._revision().acknowledged_at)

    def test_a_superseded_message_is_acknowledged(self):
        """It is dealt with too: the folio is at a later message already."""
        self._seed_booking()
        self._import()
        self._seed_booking(external_id="r0", inserted_at="2026-11-12T09:00:00.000000")
        self._import()
        self.assertEqual(self._revision("r0").state, "superseded")
        self.assertTrue(self._revision("r0").acknowledged_at)

    def test_it_is_not_acknowledged_twice(self):
        self._seed_booking()
        self._import()
        self.assertEqual(self.backend.channex_acknowledge_booking_revisions(), 0)
        self.assertEqual(len(self._acks()), 1)

    def test_a_message_channex_no_longer_holds_counts_as_acknowledged(self):
        """Nothing to acknowledge is the outcome acknowledging aims for."""
        self._seed_booking()
        with trap_jobs() as trap:
            self.backend.channex_import_booking_revisions()
            trap.perform_enqueued_jobs()
            self.server.store["booking_revisions"] = []
            trap.perform_enqueued_jobs()
        self.assertTrue(self._revision().acknowledged_at)

    def test_exports_disabled_leaves_the_message_unacknowledged(self):
        """A backend with exports off is usually a copy of a live one, and a
        receipt sent from it would make the real hotel lose the message."""
        self.backend.export_disabled = True
        self._seed_booking()
        self._import()
        self.assertFalse(self._acks())
        self.assertFalse(self._revision().acknowledged_at)
        # Still pending, so turning exports back on sends it.
        self.assertEqual(self.backend.channex_acknowledge_booking_revisions(), 1)

    def test_an_acknowledged_message_is_not_read_again(self):
        """Which is what makes the folio stay put on the next read."""
        self._seed_booking()
        self._import()
        self.assertEqual(self._import(), {"total": 0, "queued": 0})
        self.assertEqual(len(self._folios()), 1)


@tagged("post_install", "-at_install")
class TestChannexBookingSweep(ChannexBookingCase):
    """The two readings, and the sweep that runs one of them.

    Channex stops offering a message in the feed half an hour after issuing it,
    acknowledged or not, and keeps it in the listing of unacknowledged ones. So
    the feed is what to read on notice and the listing is what to sweep with: a
    connector sweeping the feed would miss exactly the messages a sweep is for.
    """

    def test_a_message_the_feed_no_longer_offers_is_still_taken_in(self):
        self._seed_booking()
        self.server.age_out_of_feed("r1")
        self.assertEqual(self._import(), {"total": 1, "queued": 1})
        self.assertEqual(len(self._folios()), 1)

    def test_the_feed_reading_takes_in_what_was_just_issued(self):
        """The short path, for when Channex tells us something arrived."""
        self._seed_booking()
        with trap_jobs() as trap:
            result = self.backend.channex_import_booking_feed()
            trap.perform_enqueued_jobs()
        self.assertEqual(result, {"total": 1, "queued": 1})
        self.assertEqual(len(self._folios()), 1)

    def test_the_feed_reading_does_not_see_what_aged_out_of_it(self):
        self._seed_booking()
        self.server.age_out_of_feed("r1")
        with trap_jobs() as trap:
            result = self.backend.channex_import_booking_feed()
            trap.perform_enqueued_jobs()
        self.assertEqual(result, {"total": 0, "queued": 0})
        self.assertFalse(self._folios())

    def test_a_message_already_acknowledged_is_not_taken_in(self):
        """Channex answers a filter it does not know with everything instead of
        refusing it, so the status of each message is checked here as well.
        Without that, the day the filter stops being supported we would take in
        again every booking ever received."""
        self.server.ignore_filter("acknowledge_status")
        self._seed_booking(acknowledge_status="acknowledged")
        self.assertEqual(self._import(), {"total": 0, "queued": 0})
        self.assertFalse(self._folios())

    # -- the sweep ---------------------------------------------------------

    def _second_backend(self, exported):
        """Another hotel of the same account, on Channex or not."""
        pms_property = self.env["pms.property"].create(
            {
                "name": "Channex Property 2",
                "company_id": self.company.id,
                "default_pricelist_id": self.pricelist.id,
                "tz": "Europe/Madrid",
            }
        )
        backend = self.env["channel.channex.backend"].create(
            {
                "name": "Channex Backend 2",
                "pms_property_id": pms_property.id,
                "backend_type_id": self.backend.backend_type_id.id,
                "api_key": "test-key",
                "environment": "staging",
                "group_id": self.backend.group_id,
            }
        )
        if exported:
            self.env["channel.channex.pms.property"].export_record(
                backend, pms_property
            )
        return backend

    def _swept(self, trap):
        return [job.recordset.id for job in trap.enqueued_jobs]

    def test_the_sweep_queues_a_reading_for_every_backend(self):
        other = self._second_backend(exported=True)
        with trap_jobs() as trap:
            self.env["channel.channex.backend"].channex_cron_import_booking_revisions()
            self.assertEqual(set(self._swept(trap)), {self.backend.id, other.id})

    def test_the_sweep_skips_a_property_never_exported(self):
        """There is nothing on Channex waiting for it, and a property Channex
        cannot answer for must not stop the sweep of the others."""
        self._second_backend(exported=False)
        with trap_jobs() as trap:
            self.env["channel.channex.backend"].channex_cron_import_booking_revisions()
            self.assertEqual(self._swept(trap), [self.backend.id])

    def test_the_sweep_reads_each_backend_in_its_own_job(self):
        """So a hotel Channex cannot answer for is retried on its own."""
        self._seed_booking()
        with trap_jobs() as trap:
            self.env["channel.channex.backend"].channex_cron_import_booking_revisions()
            self.assertFalse(self.server.calls_to("GET", "booking_revisions"))
            for _round in range(3):
                if not trap.enqueued_jobs:
                    break
                trap.perform_enqueued_jobs()
        self.assertEqual(len(self._folios()), 1)
