# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""How a change in Odoo becomes a call to Channex's ARI endpoints.

The same mechanism as Wubook's, with one difference that Channex forces.

What is the same: nothing calls Channex where the change happens. A change
notes which nights of what moved, the notes of a transaction are merged when it
commits into the nights each binding concerned has pending, and a job is queued
with an identity key, so queue_job does not pile up a second one while the
first is waiting. The job computes the values when it runs, so what reaches
Channex is always the state of that moment.

What is different: the job is one per backend and kind of data, not one per
binding. Channex's certification asks for a change across several rate plans
or dates to arrive in a single call, and its limit of ten calls a minute is
per property, not per room type or rate plan. A job per binding would be a
call per binding.

The nights pending are kept as they were noted, not as the span that covers
them: a night today and one next summer, changed within the same few seconds,
are two nights to publish and not the year between them.
"""

import datetime
import functools
from collections import defaultdict

from odoo import api, fields, models

#: What can be published, and the binding model whose rows carry each one.
ARI_KINDS = [
    ("availability", "Availability"),
    ("channel_caps", "Caps of the agencies"),
    ("restrictions", "Rates and restrictions"),
]
ARI_BINDING_MODELS = {
    "availability": "channel.channex.pms.room.type",
    "channel_caps": "channel.channex.inventory.cap",
    "restrictions": "channel.channex.rate.plan",
}

#: Seconds a send waits before it runs, so a burst of changes -- a reservation
#: of several nights, a massive change -- goes out in one call. It is also the
#: pace Channex recommends, one call every six seconds, which keeps a kind under
#: its ten calls a minute.
ARI_DELAY = 6

_BUFFER_KEY = "connector_pms_channex.ari"


def merge_ranges(ranges):
    """The nights of these ranges in as few ranges as they make: in order, and
    none overlapping or following straight on from another.

    :param ranges: ``(date_from, date_to)`` pairs, both dates included.
    :return: a list of ``(date_from, date_to)`` pairs.
    """
    merged = []
    for date_from, date_to in sorted(ranges):
        # Subtracted rather than a day added: a range may end on date.max.
        if merged and (date_from - merged[-1][1]).days <= 1:
            if date_to > merged[-1][1]:
                merged[-1] = (merged[-1][0], date_to)
            continue
        merged.append((date_from, date_to))
    return merged


def clip_ranges(ranges, first, last):
    """These ranges cut down to the nights between ``first`` and ``last``,
    those left with none dropped."""
    clipped = []
    for date_from, date_to in ranges:
        date_from, date_to = max(date_from, first), min(date_to, last)
        if date_from <= date_to:
            clipped.append((date_from, date_to))
    return clipped


def envelope(ranges):
    """First and last night of ranges as :func:`merge_ranges` gives them."""
    return ranges[0][0], ranges[-1][1]


def stage_ari(env, kind, key, date_from=None, date_to=None):
    """Note that something has to be published to Channex again.

    ``key`` says what, in the terms of ``kind`` -- for availability, a
    ``(property_id, room_type_id)`` pair where either may be ``None`` to mean
    every one. The binding model of the kind resolves it into bindings when the
    transaction commits. No date on either end means as far as Channex
    publishes.

    Nothing is written here: the notes are kept for the transaction and merged
    when it commits, so a reservation of ten nights is one range and not ten.
    """
    data = env.cr.precommit.data
    buffer = data.get(_BUFFER_KEY)
    if buffer is None:
        buffer = data[_BUFFER_KEY] = {}
        env.cr.precommit.add(functools.partial(_flush_ari, env))
    entries = buffer.setdefault(kind, {})
    # Merged once, when the transaction commits: a massive change notes
    # thousands of nights, one by one.
    entries.setdefault(key, []).append(
        (date_from or datetime.date.min, date_to or datetime.date.max)
    )


def _flush_ari(env):
    """Precommit hook: note the nights pending of every binding concerned and
    queue the send of each backend and kind.

    As superuser: the change that got here may well be a receptionist's, who
    can see no binding and no backend, and a send must not depend on who
    happened to trigger it.
    """
    buffer = env.cr.precommit.data.pop(_BUFFER_KEY, None)
    if not buffer:
        return
    env = env(su=True)
    horizons = {}
    pending = []
    for kind, entries in buffer.items():
        resolved = env[ARI_BINDING_MODELS[kind]]._channex_ari_resolve(entries)
        for binding, ranges in resolved.items():
            backend = binding.backend_id
            if backend not in horizons:
                horizons[backend] = backend._channex_ari_horizon()
            # Clipped to what Channex publishes: a night before it is past, and
            # one after it is not on sale yet -- it gets published the day it
            # enters.
            for date_from, date_to in merge_ranges(
                clip_ranges(ranges, *horizons[backend])
            ):
                pending.append(
                    {
                        "backend_id": backend.id,
                        "kind": kind,
                        "res_id": binding.id,
                        "date_from": date_from,
                        "date_to": date_to,
                    }
                )
    if not pending:
        return
    env["channel.channex.ari.pending"].create(pending)
    # A precommit hook runs after the transaction was flushed, so what it writes
    # has to be flushed here or it would never be committed.
    env.flush_all()
    to_send = {(values["backend_id"], values["kind"]) for values in pending}
    for backend_id, kind in sorted(to_send):
        env["channel.channex.backend"].browse(backend_id)._channex_schedule_ari(kind)


def nights(date_from, date_to):
    """Every night from ``date_from`` to ``date_to``, both included."""
    for offset in range((date_to - date_from).days + 1):
        yield date_from + datetime.timedelta(days=offset)


def range_nights(ranges):
    """Every night of these ranges, in order."""
    for date_from, date_to in ranges:
        yield from nights(date_from, date_to)


def runs(values):
    """Group consecutive nights holding the same value.

    :param values: ``(date, value)`` pairs, ordered by date. A night missing
        ends a run, whatever the next one holds.
    :return: ``(date_from, date_to, value)`` triples, both dates included.
    """
    run = None
    for date, value in values:
        if run and run[2] == value and run[1] + datetime.timedelta(days=1) == date:
            run = (run[0], date, value)
            continue
        if run:
            yield run
        run = (date, date, value)
    if run:
        yield run


class ChannelChannexAriMixin(models.AbstractModel):
    """A binding whose values are published to Channex's ARI endpoints.

    What it has pending is kept in ``channel.channex.ari.pending``; what each
    one has to say is how a change finds it.
    """

    _name = "channel.channex.ari.mixin"
    _description = "Channel Channex ARI binding"

    @api.model
    def _channex_ari_resolve(self, entries):
        """Turn the notes of a transaction into the bindings concerned.

        :param entries: ``{key: [(date_from, date_to)]}`` as noted by
            :func:`stage_ari` for this model's kind.
        :return: ``{binding: [(date_from, date_to)]}``, the ranges as they come:
            they are merged afterwards.
        """
        raise NotImplementedError()


class ChannelChannexAriPending(models.Model):
    """Nights of a binding still to be published to Channex.

    Every transaction that changes something adds rows of its own, and a send
    takes away the rows it read and no other. What is noted while a send is
    working out its values goes in rows that send never saw, so they stay for
    the next one: nothing is cleared away unsent, and no one writes to rows
    anyone else wrote.
    """

    _name = "channel.channex.ari.pending"
    _description = "Channel Channex ARI pending nights"
    _order = "id"
    # Rows that live a few seconds: who wrote them and when says nothing.
    _log_access = False

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        ondelete="cascade",
        index=True,
    )
    kind = fields.Selection(selection=ARI_KINDS, required=True)
    res_id = fields.Integer(
        string="Binding",
        required=True,
        help="The binding, of the model its kind publishes.",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)

    def _channex_ranges(self, first, last):
        """The nights of these rows between ``first`` and ``last``, by binding.

        :return: ``{res_id: [(date_from, date_to)]}``, merged; a binding left
            with no night is left out.
        """
        ranges = defaultdict(list)
        for row in self:
            ranges[row.res_id].append((row.date_from, row.date_to))
        clipped = {
            res_id: merge_ranges(clip_ranges(binding_ranges, first, last))
            for res_id, binding_ranges in ranges.items()
        }
        return {res_id: merged for res_id, merged in clipped.items() if merged}


class ChannelChannexAriWarning(models.Model):
    """What Channex refused out of a send it otherwise accepted.

    Channex answers 200 and applies everything valid, and lists what it threw
    away under ``warnings``. Those nights are not retried -- a value Channex
    refuses now it will refuse again -- so they are kept here for someone to
    look at, and published again by the next change that touches them.
    """

    _name = "channel.channex.ari.warning"
    _description = "Channel Channex ARI warning"
    _order = "create_date desc, id desc"

    backend_id = fields.Many2one(
        comodel_name="channel.channex.backend",
        required=True,
        ondelete="cascade",
        index=True,
    )
    kind = fields.Selection(selection=ARI_KINDS, required=True)
    count = fields.Integer(help="Values Channex refused in that send.")
    warnings = fields.Text(help="What Channex answered, value by value.")
