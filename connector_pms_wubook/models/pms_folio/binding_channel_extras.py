# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging
import zlib
from datetime import datetime

import pytz
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare
from odoo.tools.misc import formatLang

from .channel_extras import plan_service_lines
from .retryable import RETRYABLE_ERRORS

_logger = logging.getLogger(__name__)

# Extras whose service the import does not create (again) by itself: it was
# created already ("applied"), deleted by staff ("deleted") or staff added
# the same product by hand ("manual").
HANDLED_STATES = ("applied", "deleted", "manual")


def _digest(text):
    return format(zlib.crc32(str(text).encode()), "08x")


class ChannelWubookPmsFolioBinding(models.Model):
    _inherit = "channel.wubook.pms.folio"

    channel_extras = fields.Json(
        string="Channel Extras Received",
        copy=False,
        readonly=True,
        help="Extras the channel sent with the last version of the booking.",
    )
    channel_extras_state = fields.Json(
        string="Channel Extras Status",
        copy=False,
        readonly=True,
        help="What the import did with each channel extra of the folio.",
    )

    def _channel_extras_to_sync(self, folio):
        """Whether ``folio`` has channel extras to sync. Cheap, never fails.

        A cancelled folio has none: cancelling its rooms already took their
        services out of the total. Nor has a booking without extras, unless
        some were imported before.
        """
        self.ensure_one()
        try:
            payload = self.channel_extras
            if not isinstance(payload, dict) or folio.state == "cancel":
                return False
            if not payload.get("items") and not (
                (self.channel_extras_state or {}).get("items")
            ):
                return False
            return self.backend_id._channel_extras_enabled(
                payload.get("channel"), folio
            )
        except RETRYABLE_ERRORS:
            raise
        except Exception:  # pylint: disable=broad-except
            _logger.exception("Could not check the channel extras of %s", folio.name)
            return False

    def _sync_channel_extras_safe(self, folio):
        """Run ``_sync_channel_extras`` without ever failing the import.

        Database concurrency errors and ``RetryableJobError`` still abort it,
        so that queue_job retries the import. Any other error is logged and
        reported on the folio, and the folio goes on without the extras.

        :return: whether the extras were synced.
        """
        self.ensure_one()
        binding = self.with_context(lang=self._channel_extras_lang(folio))
        # Opening the savepoint flushes the work the import left pending (ORM
        # updates, precommit hooks), so it is flushed before the try: its
        # failures are not failures of the extras and must fail the import,
        # as they did before.
        self.env.cr.flush()
        try:
            with self.env.cr.savepoint():
                binding._sync_channel_extras(folio)
        except RETRYABLE_ERRORS:
            raise
        except Exception as error:  # pylint: disable=broad-except
            _logger.exception("Could not import the channel extras of %s", folio.name)
            binding._channel_extras_report_error(folio, error)
            return False
        return True

    def _sync_channel_extras(self, folio):
        """Turn the extras sold with the booking into services of ``folio``.

        It works on the extras the last import stored in ``channel_extras``,
        so it also runs outside the import (the "Apply to Pending Folios"
        button of the channel extras) without asking Wubook. Each extra
        becomes a ``pms.service`` of a reservation tagged with the key of the
        extra; the services without that tag were added by hand and are never
        touched. ``channel_extras_state`` keeps what was decided for each
        extra, so that later imports respect what staff did meanwhile:

        * a cancelled folio is left alone: cancelling its rooms already took
          their services out of the total;
        * when the channel split the booking into several folios, each one
          repeats the extras of the whole booking, and when the extras come
          in another currency than the folio's, nothing is imported: a note
          asks staff to add them by hand;
        * new services go to the reservation that already holds the imported
          ones, else to the first one by checkin date. The ones left on a
          reservation cancelled because the channel moved the stay are
          created again on the new stay, unless invoiced;
        * a new extra gets the product of its row in the channel extras
          (rows for unknown names are created as pending) or the fallback
          product of the backend. It is retried on later imports while it
          has no product or could not be created, and skipped when the folio
          already has that product added by hand;
        * an extra the channel changed rewrites its service and one the
          channel removed deletes it, unless staff changed or invoiced the
          service; a service staff deleted is never created again.

        Every case staff must review is reported once with a folio note.
        """
        self.ensure_one()
        payload = self.channel_extras
        if not isinstance(payload, dict) or folio.state == "cancel":
            return
        # The services are created and changed in the language of the notes,
        # which quote the pms errors (e.g. the daily limit of a product, with
        # its date)
        folio = folio.with_context(lang=self.env.lang)
        entries = (self.channel_extras_state or {}).get("items") or {}
        extras = payload.get("items") or []
        if not extras and not entries:
            return
        notes = []
        if payload.get("split"):
            if extras:
                notes.append(self._channel_extras_split_note(folio, payload, extras))
        elif payload.get("currency") and payload["currency"] != folio.currency_id.name:
            if extras:
                notes.append(self._channel_extras_currency_note(folio, payload, extras))
        else:
            self._channel_extras_apply(folio, extras, entries, notes)
        self._channel_extras_save(folio, entries, notes)

    def _channel_extras_apply(self, folio, extras, entries, notes):
        reservations = folio.reservation_ids.filtered(
            lambda reservation: reservation.state != "cancel"
        ).sorted(key=lambda reservation: (reservation.checkin, reservation.id))
        if not reservations:
            return
        self._channel_extras_move(folio, entries, notes)
        holders = reservations.filtered(
            lambda reservation: reservation.service_ids.filtered("channel_extra_key")
        )
        target = (holders or reservations)[0]
        for extra in extras:
            service = self._channel_extras_service(folio, extra["key"])
            entry = entries.get(extra["key"])
            if service:
                self._channel_extras_update(folio, service, extra, entries, notes)
            elif entry and entry.get("state") in HANDLED_STATES:
                self._channel_extras_keep(folio, extra, entry, notes)
            else:
                self._channel_extras_create(folio, target, extra, entries, notes)
        self._channel_extras_remove(folio, extras, entries, notes)
        pending = [
            extra
            for extra in extras
            if entries.get(extra["key"], {}).get("state") == "pending"
        ]
        if pending:
            notes.append(
                (
                    "pending:" + "|".join(sorted(extra["key"] for extra in pending)),
                    _(
                        "Channel extras without product, not imported: "
                        "%(extras)s. Assign them a product in the channel "
                        "extras and apply it to the pending folios.",
                        extras=self._channel_extras_list(folio, pending),
                    ),
                )
            )

    def _channel_extras_move(self, folio, entries, notes):
        """Free the services left on a stay the channel moved.

        A modification that moves the stay cancels the reservation as
        "modified" and creates another one. The extras belong to the booking,
        so their services are deleted there to be created again on the new
        stay (which also frees the daily limits of their products).
        """
        moved = folio.service_ids.filtered(
            lambda service: (
                service.channel_extra_key
                and service.reservation_id.state == "cancel"
                and service.reservation_id.cancelled_reason == "modified"
            )
        )
        for service in moved:
            key = service.channel_extra_key
            entry = entries.get(key)
            if entry and entry.get("state") != "applied":
                # Staff already took this extra over: leave it alone
                continue
            name = service.channel_extra_name or key
            if self._channel_extras_invoiced(service):
                notes.append(
                    (
                        f"moved-invoiced:{key}:{service.id}",
                        _(
                            "The channel moved the stay of the extra "
                            "%(extra)s, but its service is invoiced, so it "
                            "stays on the cancelled room: review it.",
                            extra=name,
                        ),
                    )
                )
                continue
            if entry and self._channel_extras_edited(service, entry):
                notes.append(
                    (
                        f"moved-edited:{key}:{service.id}",
                        _(
                            "The channel moved the stay of the extra "
                            "%(extra)s: its service is created again as the "
                            "channel sends it, without the changes made by "
                            "hand (%(old)s).",
                            extra=name,
                            old=self._channel_extras_values(
                                folio, service.product_qty, service.price_total
                            ),
                        ),
                    )
                )
            service.unlink()
            entries.pop(key, None)

    def _channel_extras_update(self, folio, service, extra, entries, notes):
        key = extra["key"]
        entry = entries.get(key)
        if not entry or entry.get("state") != "applied":
            # One of our services the status does not know about (e.g. the
            # binding was created again): take it as it is.
            entries[key] = self._channel_extras_entry(extra, "applied", service)
            return
        if not self._channel_extras_changed(entry, extra):
            entry["name"] = extra["name"]
            return
        old = self._channel_extras_values(folio, entry.get("qty"), entry.get("amount"))
        new = self._channel_extras_values(folio, extra["qty"], extra["amount"])
        if self._channel_extras_invoiced(service) or self._channel_extras_edited(
            service, entry
        ):
            notes.append(
                (
                    f"locked:{key}:{extra['qty']}:{extra['amount']}",
                    _(
                        "The channel changed the extra %(extra)s from %(old)s "
                        "to %(new)s, but its service was changed by hand or "
                        "invoiced, so it was not updated: review it.",
                        extra=extra["name"],
                        old=old,
                        new=new,
                    ),
                )
            )
            entry.update(qty=extra["qty"], amount=extra["amount"])
            return
        lines = self._channel_extras_lines(
            extra, service.reservation_id, service.product_id
        )
        # Flushed out of the try: only the changes below fail this extra
        self.env.cr.flush()
        try:
            with self.env.cr.savepoint():
                service.write(
                    {
                        "service_line_ids": [
                            (2, line.id) for line in service.service_line_ids
                        ]
                        + lines
                    }
                )
        except UserError as error:
            notes.append(
                (
                    f"update-failed:{key}:{extra['qty']}:{extra['amount']}:"
                    f"{_digest(error)}",
                    _(
                        "The channel changed the extra %(extra)s from %(old)s "
                        "to %(new)s, but its service could not be updated: "
                        "%(error)s",
                        extra=extra["name"],
                        old=old,
                        new=new,
                        error=error,
                    ),
                )
            )
            return
        entries[key] = self._channel_extras_entry(extra, "applied", service)

    def _channel_extras_keep(self, folio, extra, entry, notes):
        """Leave alone an extra staff deleted or added by hand."""
        if entry["state"] == "applied":
            # Its service was deleted, or its room cancelled, by hand
            entry["state"] = "deleted"
        entry["name"] = extra["name"]
        if not self._channel_extras_changed(entry, extra):
            return
        notes.append(
            (
                f"handmade:{extra['key']}:{extra['qty']}:{extra['amount']}",
                _(
                    "The channel changed the extra %(extra)s from %(old)s to "
                    "%(new)s, but it is managed by hand in this folio: review "
                    "it.",
                    extra=extra["name"],
                    old=self._channel_extras_values(
                        folio, entry.get("qty"), entry.get("amount")
                    ),
                    new=self._channel_extras_values(
                        folio, extra["qty"], extra["amount"]
                    ),
                ),
            )
        )
        entry.update(qty=extra["qty"], amount=extra["amount"])

    def _channel_extras_create(self, folio, reservation, extra, entries, notes):
        key = extra["key"]
        backend = self.backend_id
        row = self.env["channel.wubook.backend.extra"]._get_for_import(backend, extra)
        if row and (not row.active or row.action == "ignore"):
            entries[key] = self._channel_extras_entry(extra, "ignored")
            return
        product = row.product_id or backend.extra_fallback_product_id
        if not product:
            entries[key] = self._channel_extras_entry(extra, "pending")
            return
        if self._channel_extras_handmade(folio, product):
            entries[key] = self._channel_extras_entry(extra, "manual")
            notes.append(
                (
                    f"manual:{key}",
                    _(
                        "Channel extra %(extra)s not imported: the folio "
                        "already has %(product)s added by hand.",
                        extra=extra["name"],
                        product=product.display_name,
                    ),
                )
            )
            return
        service = folio.env["pms.service"]
        error = self._channel_extras_check(folio, product, extra)
        if not error:
            vals = self._channel_extras_service_vals(
                folio, reservation, extra, product, row
            )
            # Flushed out of the try: only the service below fails this extra
            self.env.cr.flush()
            try:
                with self.env.cr.savepoint():
                    service = service.create(vals)
            except UserError as exc:
                error = str(exc)
        if error:
            entries[key] = self._channel_extras_entry(extra, "failed")
            notes.append(
                (
                    f"failed:{key}:{_digest(error)}",
                    _(
                        "Channel extra %(extra)s not imported: %(error)s",
                        extra=extra["name"],
                        error=error,
                    ),
                )
            )
            return
        entries[key] = self._channel_extras_entry(extra, "applied", service)

    def _channel_extras_remove(self, folio, extras, entries, notes):
        """Delete the services of the extras the channel removed."""
        keys = {extra["key"] for extra in extras}
        for key in [key for key in entries if key not in keys]:
            entry = entries[key]
            service = self._channel_extras_service(folio, key)
            if entry.get("state") == "applied" and service:
                if self._channel_extras_invoiced(
                    service
                ) or self._channel_extras_edited(service, entry):
                    notes.append(
                        (
                            f"removed:{key}",
                            _(
                                "The channel removed the extra %(extra)s, but "
                                "its service was changed by hand or invoiced, "
                                "so it was kept: review it.",
                                extra=entry.get("name") or key,
                            ),
                        )
                    )
                    continue
                service.unlink()
            del entries[key]

    def _channel_extras_service_vals(self, folio, reservation, extra, product, row):
        vals = {
            "product_id": product.id,
            "reservation_id": reservation.id,
            "is_board_service": False,
            "no_auto_add_lines": True,
            "channel_extra_key": extra["key"],
            "channel_extra_name": extra["name"],
            "wubook_extra_id": row.id,
            "service_line_ids": self._channel_extras_lines(extra, reservation, product),
        }
        if product != row.product_id:
            # The fallback product says nothing about the extra by itself
            lang = folio.partner_id.lang or self.env.lang
            vals["name"] = "{}: {}".format(
                product.with_context(lang=lang).display_name, extra["name"]
            )
        return vals

    def _channel_extras_lines(self, extra, reservation, product):
        plan = plan_service_lines(
            extra["qty"],
            extra["amount"],
            reservation.checkin,
            reservation.checkout,
            product.per_day,
            product.consumed_on,
            onboard_date=self._channel_extras_onboard_date(reservation),
        )
        return [
            (0, 0, {"date": date, "day_qty": day_qty, "price_unit": price_unit})
            for date, day_qty, price_unit in plan
        ]

    def _channel_extras_onboard_date(self, reservation):
        """The current date of the hotel if the guests are already on board.

        pms charges the services that are not per day added to a reservation
        on board on that date (``pms.service._compute_service_line_ids``).
        """
        if reservation.state != "onboard":
            return None
        tz = reservation.pms_property_id.tz or self.env.user.tz or "UTC"
        return datetime.now(pytz.timezone(tz)).date()

    def _channel_extras_check(self, folio, product, extra):
        """Return why ``extra`` cannot be imported as ``product``, if so."""
        if extra["amount"] < 0:
            return _("its price is negative")
        taxes = folio.fiscal_position_id.map_tax(
            product.taxes_id.filtered(lambda tax: tax.company_id == folio.company_id)
        )
        # The channel amounts are what the guest pays, taxes included
        if not all(tax.price_include for tax in taxes.flatten_taxes_hierarchy()):
            return _(
                "the taxes of %(product)s are not included in its price",
                product=product.display_name,
            )
        return False

    def _channel_extras_service(self, folio, key):
        """The imported service of the extra ``key`` that is still in use."""
        return folio.service_ids.filtered(
            lambda service: (
                service.channel_extra_key == key
                and service.reservation_id.state != "cancel"
            )
        )[:1]

    def _channel_extras_handmade(self, folio, product):
        """Services of ``product`` added to ``folio`` by hand."""
        return folio.service_ids.filtered(
            lambda service: (
                not service.channel_extra_key
                and not service.is_board_service
                and service.product_id == product
                and service.reservation_id.state != "cancel"
            )
        )

    def _channel_extras_invoiced(self, service):
        return any(
            line.move_id.state != "cancel"
            for line in service.sale_line_ids.invoice_lines
        )

    def _channel_extras_edited(self, service, entry):
        """Whether staff changed ``service`` since the import created it."""
        if not entry.get("product_id"):
            return True
        return (
            service.product_id.id != entry["product_id"]
            or service.product_qty != entry.get("service_qty")
            or float_compare(
                service.price_total,
                entry.get("service_amount") or 0.0,
                precision_digits=2,
            )
            != 0
        )

    def _channel_extras_changed(self, entry, extra):
        """Whether the channel changed ``extra`` since ``entry`` was saved."""
        return (
            entry.get("qty") != extra["qty"]
            or float_compare(
                entry.get("amount") or 0.0, extra["amount"], precision_digits=2
            )
            != 0
        )

    def _channel_extras_entry(self, extra, state, service=None):
        entry = {
            "state": state,
            "name": extra["name"],
            "qty": extra["qty"],
            "amount": extra["amount"],
        }
        if service:
            # What the import left on the service, to tell staff changes
            entry.update(
                product_id=service.product_id.id,
                service_qty=service.product_qty,
                service_amount=round(service.price_total, 2),
            )
        return entry

    def _channel_extras_entry_state(self, key):
        items = (self.channel_extras_state or {}).get("items") or {}
        return (items.get(key) or {}).get("state")

    def _channel_extras_save(self, folio, entries, notes):
        """Post the notes not posted yet and keep the status of the extras."""
        state = self.channel_extras_state or {}
        posted = list(state.get("notes") or [])
        for key, text in notes:
            if key in posted:
                continue
            self._channel_extras_post(folio, text)
            posted.append(key)
        new_state = {"items": entries, "notes": posted}
        if new_state != state:
            self.channel_extras_state = new_state

    def _channel_extras_post(self, folio, text):
        folio.sudo().message_post(
            body=Markup("<p>%s</p>") % text,
            subtype_id=self.env.ref("mail.mt_note").id,
            email_from=self.env.user.partner_id.email_formatted
            or folio.pms_property_id.email_formatted,
        )

    def _channel_extras_report_error(self, folio, error):
        """Tell staff on the folio that its channel extras failed."""
        message = str(error) if isinstance(error, UserError) else repr(error)
        try:
            with self.env.cr.savepoint():
                self._channel_extras_save(
                    folio,
                    (self.channel_extras_state or {}).get("items") or {},
                    [
                        (
                            f"error:{_digest(message)}",
                            _(
                                "Channel extras could not be imported: %(error)s",
                                error=message[:500],
                            ),
                        )
                    ],
                )
        except RETRYABLE_ERRORS:
            raise
        except Exception:  # pylint: disable=broad-except
            _logger.exception("Could not report the channel extras of %s", folio.name)

    def _channel_extras_lang(self, folio):
        return (
            folio.pms_property_id.lang
            or folio.company_id.partner_id.lang
            or self.env.lang
        )

    def _channel_extras_values(self, folio, qty, amount, currency_code=None):
        """Text of the quantity and the total of an extra."""
        if currency_code:
            total = f"{amount or 0.0:.2f} {currency_code}"
        else:
            total = formatLang(self.env, amount or 0.0, currency_obj=folio.currency_id)
        return _("%(qty)s units, %(total)s", qty=qty, total=total)

    def _channel_extras_list(self, folio, extras, currency_code=None):
        return ", ".join(
            "{} ({})".format(
                extra["name"],
                self._channel_extras_values(
                    folio, extra["qty"], extra["amount"], currency_code
                ),
            )
            for extra in extras
        )

    def _channel_extras_split_note(self, folio, payload, extras):
        return (
            "split:" + "|".join(sorted(extra["key"] for extra in extras)),
            _(
                "The channel split this booking into %(count)s folios and each "
                "one repeats the extras of the whole booking, so they were not "
                "imported: %(extras)s. Add them by hand to the folio they "
                "belong to.",
                count=payload["split"][1],
                extras=self._channel_extras_list(folio, extras),
            ),
        )

    def _channel_extras_currency_note(self, folio, payload, extras):
        return (
            "currency:" + payload["currency"],
            _(
                "The channel extras are in %(currency)s and the folio in "
                "%(folio_currency)s, so they were not imported: %(extras)s.",
                currency=payload["currency"],
                folio_currency=folio.currency_id.name,
                extras=self._channel_extras_list(
                    folio, extras, currency_code=payload["currency"]
                ),
            ),
        )
