# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""Per-method call accounting, which is also the rate limit.

Channex documents a limit per property and per endpoint, and its certification
checks that an integration respects it, so the accounting has to be right from
the start rather than added later.
"""

import datetime
import logging

from odoo import _, fields

from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)

#: Logged instead of a call while exports are disabled. Nothing reached
#: Channex, so these rows do not count towards the limit.
SKIPPED = "skipped"

#: Channex's documented limits, per property and endpoint: ten calls a minute
#: to each of the two ARI endpoints. Set on the method row the first time it is
#: seen, so an installation is paced from its very first call instead of from
#: the day someone remembers to configure it. Anything else stays unlimited.
DEFAULT_LIMITS = {
    "POST /availability": (10, 60),
    "POST /restrictions": (10, 60),
}


class ChannexCallControl:
    def __init__(self, adapter, funcname, arguments):
        self.adapter = adapter
        self.funcname = funcname
        self.arguments = arguments
        self.exec_timestamp = fields.Datetime.now()
        self.method = self._method(funcname)

    @property
    def _log_backend(self):
        """``channel.backend.log.backend_id`` points at the generic backend, so
        both the count and the write have to use that id. Reading with the vendor
        id silently matched nothing, which is how the limit stayed inert."""
        return self.adapter.backend_record.parent_id

    def _method(self, funcname):
        Method = self.adapter.env["channel.backend.method"]
        backend_type = self.adapter.backend_record.backend_type_id
        method = Method.search(
            [("name", "=", funcname), ("backend_type_id", "=", backend_type.id)],
            limit=1,
        )
        if not method:
            max_calls, time_window = DEFAULT_LIMITS.get(funcname, (0, 0))
            method = Method.create(
                {
                    "name": funcname,
                    "backend_type_id": backend_type.id,
                    "max_calls": max_calls,
                    "time_window": time_window,
                }
            )
        return method

    def _calls_domain(self):
        window_start = self.exec_timestamp - datetime.timedelta(
            seconds=self.method.time_window
        )
        return [
            ("backend_id", "=", self._log_backend.id),
            ("method_id", "=", self.method.id),
            ("timestamp", ">=", window_start),
            ("response_code", "!=", SKIPPED),
        ]

    def check(self):
        """Refuse the call if it would go over the limit.

        Separate from the constructor because a call that is skipped -- exports
        disabled -- never reaches Channex and must not be refused for a limit it
        does not consume.

        Being over the limit is a "not yet", not a "never": the oldest call in
        the window falls out of it on its own and the same call goes through. So
        it is retryable, and it does not spend the job's retries, because being
        paced is not a failure. The delay is a range rather than the exact
        expiry: every job refused within the same window computes the same
        expiry, and they would all wake up together for a single free slot.
        """
        if not (self.method.max_calls > 0 and self.method.time_window > 0):
            return
        domain = self._calls_domain()
        calls = self.adapter.env["channel.backend.log"].search_count(domain)
        if calls < self.method.max_calls:
            return
        wait = self._seconds_until_a_call_expires(domain)
        raise RetryableJobError(
            _(
                "Channex rate limit reached for %(function)s: %(calls)s calls "
                "in the last %(seconds)s seconds"
            )
            % {
                "function": self.funcname,
                "calls": calls,
                "seconds": self.method.time_window,
            },
            seconds=(wait, wait + self.method.time_window),
            ignore_retry=True,
        )

    def _seconds_until_a_call_expires(self, domain):
        """How long until the oldest call in the window leaves it.

        One second more because the window boundary is inclusive: retrying at
        the exact expiry would still count that call.
        """
        oldest = self.adapter.env["channel.backend.log"].search(
            domain, order="timestamp asc", limit=1
        )
        if not oldest:
            return self.method.time_window
        elapsed = (self.exec_timestamp - oldest.timestamp).total_seconds()
        return max(1, int(self.method.time_window - elapsed) + 1)

    def add_result(self, response_code, response):
        self.adapter.env["channel.backend.log"].create(
            {
                "backend_id": self._log_backend.id,
                "timestamp": self.exec_timestamp,
                "method_id": self.method.id,
                "arguments": str(self.arguments),
                "response_code": str(response_code),
                "response": str(response),
            }
        )
