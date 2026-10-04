# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo import _, api, fields, models

from odoo.addons.queue_job.job import identity_exact


def _channex_datetime(value):
    """Channex stamps revisions in UTC, which is what Odoo stores, only in ISO
    8601: a ``T`` where Odoo wants a space, and microseconds it has no room
    for. Odoo derives the format from the length of the string, so what is left
    over is not ignored, it raises."""
    if not value:
        return False
    return fields.Datetime.to_datetime(str(value).replace("T", " ")[:19])


def _channex_nights(room):
    """The nights of a room, as the dates Channex keys its prices by."""
    checkin = datetime.date.fromisoformat(room["checkin_date"])
    checkout = datetime.date.fromisoformat(room["checkout_date"])
    nights, night = [], checkin
    while night < checkout:
        nights.append(night.isoformat())
        night += datetime.timedelta(days=1)
    return nights


#: Everything Channex can say about a booking. A message of any other status is
#: recorded without one: this field is not the place to keep a word we do not
#: understand, and the reason on the row says what the word was.
CHANNEX_STATUSES = [
    ("new", "New"),
    ("modified", "Modified"),
    ("cancelled", "Cancelled"),
]


def _channex_cancels(payload):
    """Whether this message takes the whole booking away.

    Channex says it in ``status`` and there only: the rooms of a cancellation
    keep ``is_cancelled`` false, with their dates and their prices, which is
    verified against staging and not assumed. A modification whose every room
    is cancelled says the same thing the other way round, and there is the same
    thing to do about it.
    """
    if payload.get("status") == "cancelled":
        return True
    rooms = payload.get("rooms") or []
    return bool(rooms) and all(room.get("is_cancelled") for room in rooms)


class ChannelChannexBookingRevision(models.Model):
    """What Channex has sent for this property, and what became of each message.

    A log of outcomes, not a queue: the queue is ``queue.job``, one job per
    message, and a row here is only written once there is something to say
    about the message. Nothing is ever ``pending`` in this table.

    That split runs through the whole model. What we can foresee -- a room type
    nobody mapped, a breakdown that does not cover its own stay, a status not
    taken in yet -- is not a failure, it is a decision, and it is written here
    in words a hotel can act on. Anything else pms says is a job that failed,
    with its traceback, and its transaction is undone whole; no row is left
    behind claiming something happened.

    A revision is immutable on Channex -- a change to a booking is a new
    revision with a new id -- so the identity of a row never changes once
    written. The message itself is not kept: the body is in
    ``channel.backend.log`` and in the arguments of its own job, which is what
    makes retrying one possible even after Channex stops offering it.
    """

    _name = "channel.channex.booking.revision"
    _description = "Channel Channex Booking Revision"
    _rec_name = "unique_id"
    _order = "inserted_at desc, id desc"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    external_id = fields.Char(
        string="Revision ID",
        required=True,
        readonly=True,
        index=True,
        help="Identifies the message. A new one for every change to a booking.",
    )
    booking_id = fields.Char(
        string="Booking ID",
        readonly=True,
        help="The same across every revision of one booking, so it is what a "
        "folio gets bound to.",
    )
    unique_id = fields.Char(
        readonly=True,
        help="OTA code and reservation code together, as Channex composes it, "
        "e.g. BDC-3333333333.",
    )
    ota_reservation_code = fields.Char(
        readonly=True,
        help="The code the guest and the OTA see.",
    )
    ota_name = fields.Char(readonly=True)
    channel_id = fields.Char(
        string="Channex channel ID",
        readonly=True,
        help="What the agency is resolved by. Channex does not document it on "
        "this payload, so it may well come in empty.",
    )
    status = fields.Selection(
        selection=CHANNEX_STATUSES,
        readonly=True,
        help="Of the message, not of the booking.",
    )
    arrival_date = fields.Date(readonly=True)
    departure_date = fields.Date(readonly=True)
    amount = fields.Float(readonly=True)
    currency = fields.Char(readonly=True, help="As the OTA sold it.")
    inserted_at = fields.Datetime(
        string="Issued at",
        readonly=True,
        help="When Channex received the message from the OTA.",
    )

    state = fields.Selection(
        selection=[
            ("applied", "Applied"),
            ("superseded", "Superseded"),
            ("error", "Not applied"),
        ],
        readonly=True,
        required=True,
        help="What became of this message. There is no state for work still to "
        "be done: that is what the job queue is for.",
    )
    error = fields.Char(
        string="Reason",
        readonly=True,
        help="Why the message could not become a folio, when that is something "
        "the hotel can settle. It stays unacknowledged, so fixing the cause and "
        "reading the feed again is all it takes.",
    )
    acknowledged_at = fields.Datetime(
        string="Acknowledged",
        readonly=True,
        help="When Channex was told the message is in. From then on it is not "
        "handed over again, so it is only ever stamped after the folio is "
        "saved for good.",
    )

    _sql_constraints = [
        (
            "external_uniq",
            "unique(backend_id, external_id)",
            "This booking revision is already recorded for this backend.",
        ),
    ]

    # -- taking in one message ----------------------------------------------

    @api.model
    def _channex_schedule(self, backend, payloads):
        """Queue one job per message of this read, and say how many.

        Nothing is decided here: reading the feed and making sense of a message
        are separate concerns, and separating them is what gives each message a
        transaction of its own.

        A message already settled is not queued again -- there is nothing left
        to make of it -- while one that could not be applied is, because the
        reason may well be gone by now.

        The order jobs run in does not matter, and that is not luck: the folio
        carries the revision it is at, so a message applied out of order is
        recognised as the older one and lands as ``superseded``.
        """
        # The feed is live, so paginating over it can hand the same revision
        # over twice.
        by_id = {payload["id"]: payload for payload in payloads if payload.get("id")}
        settled = set(
            self.search(
                [
                    ("backend_id", "=", backend.id),
                    ("external_id", "in", list(by_id)),
                    ("state", "in", ("applied", "superseded")),
                ]
            ).mapped("external_id")
        )
        queued = 0
        for external_id, payload in by_id.items():
            if external_id in settled:
                continue
            self.with_delay(identity_key=identity_exact).channex_take_message(
                backend, payload
            )
            queued += 1
        return queued

    @api.model
    def channex_take_message(self, backend, payload):
        """Make of one message whatever can be made of it. Job entry point.

        The row is written last and in one go, so the outcome it reports is
        never a guess: if the import raises, this transaction is undone and
        there is no row at all, only a failed job holding the message and its
        traceback.
        """
        settled = self._channex_settled(backend, payload)
        if settled:
            values = {"state": settled, "error": False}
        else:
            problem = self._channex_problem(backend, payload)
            if problem:
                values = {"state": "error", "error": problem}
            else:
                self._channex_import(backend, payload)
                values = {"state": "applied", "error": False}
        revision = self._channex_record(backend, payload, values)
        revision._channex_schedule_acknowledge()
        return revision.state

    @api.model
    def _channex_import(self, backend, payload):
        """Write the folio. Whatever pms says about it is the job's business."""
        with backend.work_on("channel.channex.pms.folio") as work:
            work.component(usage="direct.record.importer").run(
                payload["booking_id"], external_data=payload
            )

    @api.model
    def _channex_record(self, backend, payload, values):
        """The row for this message, with what became of it."""
        revision = self.search(
            [
                ("backend_id", "=", backend.id),
                ("external_id", "=", payload["id"]),
            ],
            limit=1,
        )
        if revision:
            revision.write(values)
            return revision
        return self.create({**self._channex_values(backend, payload), **values})

    @api.model
    def _channex_values(self, backend, values):
        """The identity of the message, as Channex states it."""
        return {
            "backend_id": backend.id,
            "external_id": values["id"],
            "booking_id": values.get("booking_id"),
            "unique_id": values.get("unique_id"),
            "ota_reservation_code": values.get("ota_reservation_code"),
            "ota_name": values.get("ota_name"),
            "channel_id": values.get("channel_id"),
            "status": values.get("status")
            if values.get("status") in dict(CHANNEX_STATUSES)
            else False,
            "arrival_date": values.get("arrival_date"),
            "departure_date": values.get("departure_date"),
            "amount": values.get("amount") or 0.0,
            "currency": values.get("currency"),
            "inserted_at": _channex_datetime(values.get("inserted_at")),
        }

    # -- acknowledging ------------------------------------------------------

    def _channex_schedule_acknowledge(self):
        """Queue the receipt for every message that is settled and unconfirmed.

        Queued and not sent inline on purpose: a job only exists once the
        transaction that queued it commits, so no receipt is ever sent for a
        folio that was rolled back. Acknowledging cannot be undone -- the feed
        never offers the message again -- so it has to wait for the save to be
        final.
        """
        for revision in self.filtered(
            lambda r: not r.acknowledged_at and r.state in ("applied", "superseded")
        ):
            revision.with_delay(identity_key=identity_exact).channex_acknowledge()

    def channex_acknowledge(self):
        """Tell Channex this message is in. Job entry point.

        A message that could not be applied is never acknowledged: Channex
        keeps offering it, which is what makes retrying free. The price is that
        Channex emails a warning half an hour later, and that is the right
        trade: a warning is recoverable, a lost booking is not.
        """
        self.ensure_one()
        if self.acknowledged_at or self.state not in ("applied", "superseded"):
            return False
        with self.backend_id.work_on(self._name) as work:
            sent = work.component(usage="backend.adapter").ack(self.external_id)
        if not sent:
            return False
        self.acknowledged_at = fields.Datetime.now()
        return True

    # -- what can be foreseen -----------------------------------------------

    @api.model
    def _channex_settled(self, backend, payload):
        """``applied`` if the folio is already at this message, ``superseded``
        if it is at a later one, and nothing if it is behind.

        This is the guard that makes re-reading the feed free, and the one that
        keeps a message delivered late from reverting a folio.
        """
        binding = self._channex_binding(backend, payload)
        if not binding:
            return False
        if binding.revision_external_id == payload["id"]:
            return "applied"
        issued = _channex_datetime(payload.get("inserted_at"))
        if binding.revision_inserted_at and issued:
            if binding.revision_inserted_at > issued:
                return "superseded"
        return False

    @api.model
    def _channex_binding(self, backend, payload):
        """The folio this booking is already at, if it is at one."""
        if not payload.get("booking_id"):
            return self.env["channel.channex.pms.folio"]
        return self.env["channel.channex.pms.folio"].search(
            [
                ("backend_id", "=", backend.id),
                ("external_id", "=", payload["booking_id"]),
            ],
            limit=1,
        )

    @api.model
    def _channex_problem(self, backend, payload):
        """Why this message cannot be applied, if we can tell in advance.

        Reported rather than raised: none of these is a fault, they are all
        things a person can settle, and losing the message is worse than any of
        them.
        """
        if payload.get("status") not in dict(CHANNEX_STATUSES):
            return _("Messages of status %s are not taken in.") % (
                payload.get("status") or _("none")
            )
        if not payload.get("booking_id"):
            return _("The message carries no booking id to file it under.")
        binding = self._channex_binding(backend, payload)
        invoiced = binding.odoo_id.move_ids.filtered(
            lambda move: move.state == "posted"
        )
        if invoiced:
            return _(
                "The folio is invoiced by %s, which would no longer say what "
                "was sold."
            ) % ", ".join(invoiced.mapped("name"))
        if _channex_cancels(payload):
            return self._channex_cancellation_problem(backend, payload, binding)
        # A modification can cancel one room of several, and a room the message
        # itself cancels is not a room to write.
        rooms = [
            room
            for room in (payload.get("rooms") or [])
            if not room.get("is_cancelled")
        ]
        if not rooms:
            return _("The message carries no room.")
        with backend.work_on("channel.channex.pms.room.type") as work:
            binder = work.component(usage="binder")
            for room in rooms:
                problem = self._channex_room_problem(room, binder)
                if problem:
                    return problem
                problem = self._channex_price_problem(room)
                if problem:
                    return problem
            return self._channex_dropped_problem(backend, payload, rooms, binder)

    @api.model
    def _channex_cancellation_problem(self, backend, payload, binding):
        """Why this cancellation cannot be applied, if we can tell in advance.

        Almost nothing stops one, and that is the point: a cancellation that
        does not land is a room the hotel keeps blocked and a guest charged for
        a stay they called off. It is never held up by a price, by a breakdown
        that does not add up or by anything else the message says about the
        booking, because none of that gets written.
        """
        if binding:
            underway = binding.odoo_id.reservation_ids.filtered(
                lambda r: r.state != "cancel" and not r.allowed_cancel
            )
            if underway:
                return _(
                    "The booking is cancelled at the OTA, and %s cannot be "
                    "cancelled here: the stay is already under way."
                ) % ", ".join(underway.mapped("name"))
            return False
        # The booking never reached Odoo, so this message is what puts it there.
        # A cancellation nobody can see is a cancellation lost: we reflect what
        # the OTA sold, we do not decide which bookings existed. That much of
        # the message does have to be writable.
        rooms = payload.get("rooms") or []
        if not rooms:
            return _(
                "The message cancels a booking that is not in the system, and "
                "carries no room to record it with."
            )
        with backend.work_on("channel.channex.pms.room.type") as work:
            binder = work.component(usage="binder")
            for room in rooms:
                problem = self._channex_room_problem(room, binder)
                if problem:
                    return problem
        return False

    @api.model
    def _channex_dropped_problem(self, backend, payload, rooms, binder):
        """Whether the message drops a stay that cannot be dropped.

        A reservation no room of the message accounts for is cancelled, and pms
        refuses to cancel one whose guest is in the room or has already left.
        Said here, before anything is written: nobody is taken out of a room by
        a message, and a person has to look at it.
        """
        binding = self._channex_binding(backend, payload)
        if not binding:
            return False
        offered = {
            (
                binder.to_internal(room["room_type_id"], unwrap=True).id,
                room["checkin_date"],
                room["checkout_date"],
            )
            for room in rooms
        }
        dropped = binding.reservation_ids.filtered(
            lambda r: r.state != "cancel"
            and not r.allowed_cancel
            and (r.room_type_id.id, str(r.checkin), str(r.checkout)) not in offered
        )
        if dropped:
            return _(
                "The change drops %s, and a stay already under way cannot be "
                "cancelled."
            ) % ", ".join(dropped.mapped("name"))
        return False

    @api.model
    def _channex_room_problem(self, room, binder):
        """Whether one room of the message can be written at all.

        What it costs is asked separately: a cancellation has to get in whatever
        its prices look like, and this much has to hold for every message.
        """
        if not room.get("checkin_date") or not room.get("checkout_date"):
            return _("The message does not say what nights a room is for.")
        if not room.get("room_type_id") or not binder.to_internal(room["room_type_id"]):
            return _("Room type %s is not mapped to this property.") % (
                room.get("room_type_id") or _("none")
            )
        return False

    @api.model
    def _channex_price_problem(self, room):
        """Whether every night of a room can be priced exactly as the OTA sold it.

        No price of an OTA booking is ever derived here. Either the message
        carries the price of every night of the stay and that is what gets
        written, or the message is not applied: a folio priced from our own
        pricelist would be an invoice for money nobody agreed to.

        Asked of a booking coming in or being modified, and of nothing else. A
        cancellation is not priced, it is cancelled.
        """
        days = room.get("days") or {}
        nights = _channex_nights(room)
        missing = [night for night in nights if night not in days]
        if missing:
            return _("The message prices no night on %s.") % ", ".join(missing)
        outside = sorted(date for date in days if date not in nights)
        if outside:
            return _("The message prices %s, which is outside the stay.") % ", ".join(
                outside
            )
        # pms reads a price of zero as "not priced yet" and replaces it with the
        # one from its pricelist, so a night the OTA gave away cannot be written
        # as it came. Reported rather than quietly repriced: the fix belongs in
        # pms, not in a connector working around it.
        free = [night for night in nights if not float(days[night])]
        if free:
            return _("The message gives %s away, and that price cannot be kept.") % (
                ", ".join(free)
            )
        return False
