# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""How a change in Odoo becomes a call to Channex's ARI endpoints.

The same mechanism as Wubook's, with one difference that Channex forces.

What is the same: nothing calls Channex where the change happens. A change
notes which nights of what moved, the notes of a transaction are merged when it
commits, each binding concerned widens the window of nights it has pending,
and a job is queued with an identity key, so queue_job does not pile up a
second one while the first is waiting. The job computes the values when it
runs, so what reaches Channex is always the state of that moment.

What is different: the job is one per backend and kind of data, not one per
binding. Channex's certification asks for a change across several rate plans
or dates to arrive in a single call, and its limit of ten calls a minute is
per property, not per room type or rate plan. A job per binding would be a
call per binding.
"""

import datetime
import functools

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


def union_window(window, other):
    """Smallest window covering both. ``None`` on either end means "as far as
    Channex publishes", so it absorbs any date on that end."""
    if window is None:
        return other
    date_from = None if None in (window[0], other[0]) else min(window[0], other[0])
    date_to = None if None in (window[1], other[1]) else max(window[1], other[1])
    return date_from, date_to


def stage_ari(env, kind, key, date_from=None, date_to=None):
    """Note that something has to be published to Channex again.

    ``key`` says what, in the terms of ``kind`` -- for availability, a
    ``(property_id, room_type_id)`` pair where either may be ``None`` to mean
    every one. The binding model of the kind resolves it into bindings when the
    transaction commits.

    Nothing is written here: the notes are kept for the transaction and merged,
    so a reservation of ten nights is one window and not ten writes.
    """
    data = env.cr.precommit.data
    buffer = data.get(_BUFFER_KEY)
    if buffer is None:
        buffer = data[_BUFFER_KEY] = {}
        env.cr.precommit.add(functools.partial(_flush_ari, env))
    entries = buffer.setdefault(kind, {})
    entries[key] = union_window(entries.get(key), (date_from, date_to))


def _flush_ari(env):
    """Precommit hook: widen the pending window of every binding concerned and
    queue the send of each backend and kind.

    As superuser: the change that got here may well be a receptionist's, who
    can see no binding and no backend, and a send must not depend on who
    happened to trigger it.
    """
    buffer = env.cr.precommit.data.pop(_BUFFER_KEY, None)
    if not buffer:
        return
    env = env(su=True)
    to_send = set()
    for kind, entries in buffer.items():
        resolved = env[ARI_BINDING_MODELS[kind]]._channex_ari_resolve(entries)
        for binding, (date_from, date_to) in resolved.items():
            if binding._channex_ari_stage(date_from, date_to):
                to_send.add((binding.backend_id.id, kind))
    # A precommit hook runs after the transaction was flushed, so what it writes
    # has to be flushed here or it would never be committed.
    env.flush_all()
    for backend_id, kind in sorted(to_send):
        env["channel.channex.backend"].browse(backend_id)._channex_schedule_ari(kind)


def nights(date_from, date_to):
    """Every night from ``date_from`` to ``date_to``, both included."""
    for offset in range((date_to - date_from).days + 1):
        yield date_from + datetime.timedelta(days=offset)


def runs(values):
    """Group consecutive nights holding the same value.

    :param values: ``(date, value)`` pairs, ordered by date with no gaps.
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
    """The nights a binding still has to publish.

    One window per binding, widened by every change and cleared by the send
    that covers it.
    """

    _name = "channel.channex.ari.mixin"
    _description = "Channel Channex ARI pending window"

    channex_ari_pending_from = fields.Date(
        string="Pending from",
        readonly=True,
        copy=False,
        help="First night waiting to be published to Channex.",
    )
    channex_ari_pending_to = fields.Date(
        string="Pending to",
        readonly=True,
        copy=False,
        help="Last night waiting to be published to Channex.",
    )

    @api.model
    def _channex_ari_resolve(self, entries):
        """Turn the notes of a transaction into the bindings concerned.

        :param entries: ``{key: (date_from, date_to)}`` as noted by
            :func:`stage_ari` for this model's kind.
        :return: ``{binding: (date_from, date_to)}``.
        """
        raise NotImplementedError()

    def _channex_ari_stage(self, date_from, date_to):
        """Widen the pending window so it also covers these nights.

        Clipped to what Channex publishes: a night before it is past, and one
        after it is not on sale yet -- it gets published the day it enters.

        The row is written even when the window already covers the nights. A
        send running at this very moment computed its values before this change
        was committed, and it is writing to this row to clear the window; the
        two writes clash, and PostgreSQL makes one of them start again, so the
        change is never cleared away unsent.

        :return: whether any night was left to publish.
        """
        self.ensure_one()
        first, last = self.backend_id._channex_ari_horizon()
        date_from = max(date_from or first, first)
        date_to = min(date_to or last, last)
        if date_from > date_to:
            return False
        pending_from = self.channex_ari_pending_from
        pending_to = self.channex_ari_pending_to
        self.write(
            {
                "channex_ari_pending_from": min(pending_from or date_from, date_from),
                "channex_ari_pending_to": max(pending_to or date_to, date_to),
            }
        )
        return True


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
