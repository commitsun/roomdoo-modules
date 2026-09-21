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


class ChannexCallControl:
    def __init__(self, adapter, funcname, arguments):
        self.adapter = adapter
        self.arguments = arguments
        self.exec_timestamp = fields.Datetime.now()
        self.method = self._method(funcname)
        self._check_limit(funcname)

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
            method = Method.create(
                {"name": funcname, "backend_type_id": backend_type.id}
            )
        return method

    def _check_limit(self, funcname):
        if not (self.method.max_calls > 0 and self.method.time_window > 0):
            return
        window_start = self.exec_timestamp - datetime.timedelta(
            seconds=self.method.time_window
        )
        calls = self.adapter.env["channel.backend.log"].search_count(
            [
                ("backend_id", "=", self._log_backend.id),
                ("method_id", "=", self.method.id),
                ("timestamp", ">=", window_start),
            ]
        )
        if calls >= self.method.max_calls:
            # Deliberately retryable: a traffic spike must delay the job, not
            # fail it, or a burst would drop updates on the floor.
            raise RetryableJobError(
                _(
                    "Channex rate limit reached for %(function)s: %(calls)s calls "
                    "in the last %(seconds)s seconds"
                )
                % {
                    "function": funcname,
                    "calls": calls,
                    "seconds": self.method.time_window,
                },
                seconds=self.method.time_window,
            )

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
