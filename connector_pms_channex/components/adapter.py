# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime
import json
import logging

import requests

from odoo import _, fields

from odoo.addons.component.core import AbstractComponent
from odoo.addons.connector_pms.components.adapter import ChannelAdapterError
from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)

# Channex answers 404 with a body, so the id-missing case is detected by status.
NOT_FOUND = 404
# Validation and auth problems are permanent: retrying them just burns the queue.
PERMANENT_STATUSES = (400, 401, 403, 422)
# Throttling and conflicts are worth retrying, and so is anything 5xx.
THROTTLED_STATUSES = (409, 429)
DEFAULT_RETRY_SECONDS = 30
THROTTLED_RETRY_SECONDS = 60
# Backstop so a bad total_pages cannot spin a job forever.
MAX_PAGES = 200


class ChannexAPIError(ChannelAdapterError):
    """A rejection from Channex, carrying the parsed error body."""

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


class ChannelChannexAdapter(AbstractComponent):
    """REST adapter over the CRUD contract the connector framework expects.

    ``_resource`` is the collection path and ``_payload_root`` the single key
    Channex wraps a body in; both are set by the per-entity components.
    """

    _name = "channel.channex.adapter"
    _inherit = ["channel.adapter", "base.channel.channex.connector"]

    _id = "id"
    _date_format = "%Y-%m-%d"

    _resource = None
    _payload_root = None
    # Fields Channex can filter server-side. Anything else is filtered in memory
    # by ``channel.adapter._filter``.
    _server_filters = ()

    # -- transport ---------------------------------------------------------

    def _session(self):
        session = getattr(self, "_cached_session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "user-api-key": self.backend_record.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Roomdoo-Odoo/16 connector_pms_channex",
                }
            )
            self._cached_session = session
        return session

    def _timeout(self):
        return (
            self.backend_record.timeout_connect,
            self.backend_record.timeout_read,
        )

    def request(self, method, path, params=None, payload=None):
        """Single entry point for every call, so logging, error mapping and the
        rate limit are applied uniformly."""
        url = f"{self.backend_record.url.rstrip('/')}/{path.lstrip('/')}"
        control = self._call_control(
            method, f"{method} /{path.split('/')[0]}", params, payload
        )
        if control is None:
            return None
        try:
            response = self._session().request(
                method,
                url,
                params=params,
                json=payload,
                timeout=self._timeout(),
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            # Transport trouble is transient by nature; hand it back to the
            # queue instead of failing the job.
            control.add_result("error", str(e))
            raise RetryableJobError(
                _("Channex is unreachable: %s") % e,
                seconds=DEFAULT_RETRY_SECONDS,
            ) from e
        return self._handle(response, control, method, path)

    def _handle(self, response, control, method, path):
        body = self._parse(response)
        control.add_result(response.status_code, self._redact(body))
        if response.ok:
            return body
        message = self._error_message(body, response)
        if response.status_code == NOT_FOUND and method in ("GET", "PUT", "DELETE"):
            # The framework's importer and exporter both already handle this to
            # mean "gone on the other side".
            from odoo.addons.connector.exception import IDMissingInBackend

            raise IDMissingInBackend(message)
        if response.status_code in PERMANENT_STATUSES:
            raise ChannexAPIError(message, payload=body)
        if response.status_code in THROTTLED_STATUSES:
            raise RetryableJobError(
                message, seconds=self._retry_after(response, THROTTLED_RETRY_SECONDS)
            )
        if response.status_code >= 500:
            raise RetryableJobError(
                message, seconds=self._retry_after(response, DEFAULT_RETRY_SECONDS)
            )
        raise ChannexAPIError(message, payload=body)

    @staticmethod
    def _parse(response):
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    @staticmethod
    def _retry_after(response, default):
        try:
            return max(int(response.headers.get("Retry-After", default)), 1)
        except (TypeError, ValueError):
            return default

    def _error_message(self, body, response):
        """Flatten the Channex error body into something a failed job can show."""
        errors = (body or {}).get("errors")
        if not errors:
            return _("Channex returned HTTP %s") % response.status_code
        if isinstance(errors, str):
            return errors
        title = errors.get("title") or _("Channex rejected the request")
        code = errors.get("code")
        details = errors.get("details")
        parts = [title]
        if code:
            parts.append(f"[{code}]")
        if details:
            parts.append(
                json.dumps(details, sort_keys=True)
                if isinstance(details, dict | list)
                else str(details)
            )
        return " ".join(str(p) for p in parts)

    def _redact(self, body):
        """Never let credentials reach channel.backend.log."""
        if not isinstance(body, dict):
            return body
        return {k: v for k, v in body.items() if k not in ("user-api-key", "api_key")}

    def _call_control(self, method, funcname, params, payload):
        """Rate limit bookkeeping. Returns ``None`` when the call must be
        skipped because exports are disabled on this backend.

        Anything but a GET is a write, whether or not it carries a body: a
        DELETE has none. A backend with exports disabled is usually a copy of a
        live one, and letting a bodiless write through would reach the real
        Channex.
        """
        from .call_control import ChannexCallControl

        control = ChannexCallControl(self, funcname, {"params": params})
        if self.backend_record.export_disabled and method != "GET":
            control.add_result("skipped", "Export disabled on this backend")
            return None
        return control

    # -- CRUD contract -----------------------------------------------------

    def _unwrap(self, data):
        """Channex nests attributes; the mappers work with flat dicts."""
        if not data:
            return {}
        return {self._id: data.get("id"), **(data.get("attributes") or {})}

    def read(self, external_id, attributes=None):
        body = self.request("GET", f"{self._resource}/{external_id}")
        return self._unwrap((body or {}).get("data"))

    def create(self, values):
        body = self.request(
            "POST", self._resource, payload={self._payload_root: values}
        )
        return ((body or {}).get("data") or {}).get("id")

    def write(self, external_id, values):
        self.request(
            "PUT",
            f"{self._resource}/{external_id}",
            payload={self._payload_root: values},
        )
        return True

    def delete(self, external_id):
        self.request("DELETE", f"{self._resource}/{external_id}")
        return True

    def search_read(self, domain=None):
        domain = domain or []
        server_domain, memory_domain = self._extract_domain_clauses(
            domain, self._server_filters
        )
        params = {}
        for field_name, _operator, value in server_domain:
            params[f"filter[{field_name}]"] = self._convert_value(value)
        records = [self._unwrap(data) for data in self._paginate(params)]
        return self._filter(records, memory_domain)

    def search(self, domain=None):
        return [record[self._id] for record in self.search_read(domain)]

    def _convert_value(self, value):
        if isinstance(value, datetime.date | datetime.datetime):
            return fields.Date.to_string(value)
        return value

    def _paginate(self, params):
        page, results = 1, []
        while page <= MAX_PAGES:
            body = self.request(
                "GET",
                self._resource,
                params={
                    **params,
                    "pagination[page]": page,
                    "pagination[limit]": self.backend_record.page_limit,
                },
            )
            data = (body or {}).get("data") or []
            results.extend(data)
            total_pages = ((body or {}).get("meta") or {}).get("total_pages")
            if total_pages is not None:
                if page >= total_pages:
                    return results
            elif not data:
                # No pagination metadata: stop on the first empty page.
                return results
            page += 1
        _logger.warning(
            "Channex pagination hit the %s page cap on %s", MAX_PAGES, self._resource
        )
        return results
