# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Errors the channel data syncs of the folio import must never swallow."""

import psycopg2

from odoo.addons.queue_job.exception import RetryableJobError

# What must still abort the import, so that queue_job retries it: database
# concurrency errors (serialization failures, locks not available...) and
# RetryableJobError
RETRYABLE_ERRORS = (psycopg2.OperationalError, RetryableJobError)
