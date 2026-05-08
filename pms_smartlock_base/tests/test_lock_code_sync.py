from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from roomdoo_locks_base import (
    CodeResult,
    LockAuthError,
    LockCodeDeletionError,
    LockConnectionError,
    LockError,
    LockOfflineError,
)

from odoo.addons.queue_job.exception import RetryableJobError

from .common import CommonSmartlock


@contextmanager
def expect_raises(test, exc_type):
    """Like ``assertRaises`` but **without** the cursor-savepoint wrap
    that ``odoo.tests.common.BaseCase.assertRaises`` adds. The
    ``except LockError: self.failed = True`` write inside our model
    happens **before** the re-raise; ``assertRaises``' implicit
    savepoint rollback would erase that write, defeating the
    assertion. In production the call site has no such savepoint —
    queue_job's job runner catches the exception without rolling
    back the in-progress writes."""
    try:
        yield
    except exc_type:
        return
    except BaseException as exc:
        test.fail(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
    test.fail(f"expected {exc_type.__name__}, no exception raised")


def _result(
    code_id="vendor-code-x",
    pin="9876",
    lock_id="device-A",
    starts_at=None,
    ends_at=None,
):
    return CodeResult(
        code_id=code_id,
        pin=pin,
        lock_id=lock_id,
        starts_at=starts_at or datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
        ends_at=ends_at or datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc),
    )


class _SyncTestBase(CommonSmartlock):
    """Shared scaffolding: build a reservation + lock.code, replace the
    vendor connector with a ``MagicMock`` whose return values / side
    effects each test sets explicitly. Vendor errors come from the
    actual ``roomdoo_locks_base`` package so the mapping under test is
    exercised against real exception classes."""

    def setUp(self):
        super().setUp()
        self.reservation = self._create_reservation()
        self.code = self._plant_live_code(self.reservation)
        self.connector = MagicMock(name="FakeConnector")
        patcher = patch.object(
            self.env.registry["lock.vendor"],
            "get_connector",
            return_value=self.connector,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TestSyncCreate(_SyncTestBase):
    def setUp(self):
        super().setUp()
        # _sync_create only operates on records that have not been
        # synced yet; clear the planted vendor_code_id so the create
        # flow is the relevant path.
        self.code.sudo().write({"vendor_code_id": False, "pin": False})

    def test_happy_path_applies_result_and_calls_connector_with_utc(self):
        result = _result(code_id="created-1", pin="1111")
        self.connector.create_code.return_value = result

        self.code._sync_create()

        self.assertEqual(self.connector.create_code.call_count, 1)
        kwargs = self.connector.create_code.call_args.kwargs
        self.assertEqual(kwargs["lock_id"], self.code.room_id.lock_device_id)
        # Datetimes handed to the library must be tz-aware UTC; Odoo
        # stores naive UTC, so the model is responsible for the
        # ``_to_utc`` conversion.
        self.assertEqual(kwargs["starts_at"].tzinfo, timezone.utc)
        self.assertEqual(kwargs["ends_at"].tzinfo, timezone.utc)
        self.assertEqual(self.code.vendor_code_id, "created-1")
        self.assertEqual(self.code.sudo().pin, "1111")

    def test_already_cancelled_skips_connector(self):
        """``cancelled=True`` before the job runs (e.g. the reservation
        was cancelled while the job was in queue) → no vendor call.
        The guard prevents creating a code on the lock that we already
        know we won't honour."""
        self.code.cancelled = True
        self.code._sync_create()
        self.connector.create_code.assert_not_called()

    def test_post_create_cancellation_enqueues_remove(self):
        """Race: between sending ``create`` to the vendor and the
        return, the reservation got cancelled. After ``_apply_code_result``
        re-reads ``cancelled`` from DB, if it's True we must enqueue
        a ``_sync_remove`` to clean up the freshly-created vendor code."""
        result = _result()
        self.connector.create_code.return_value = result
        # Simulate the race by flipping cancelled in the DB after the
        # connector "responds" but before invalidate_recordset is run.
        original_apply = type(self.code)._apply_code_result

        def race_apply(record, res):
            original_apply(record, res)
            self.env.cr.execute(
                "UPDATE lock_code SET cancelled = TRUE WHERE id = %s",
                (record.id,),
            )

        with patch.object(
            type(self.code), "_enqueue_sync", autospec=True
        ) as enqueue_mock, patch.object(
            type(self.code), "_apply_code_result", race_apply
        ):
            self.code._sync_create()
            enqueue_mock.assert_called_once_with(self.code, "_sync_remove")

    def test_connection_error_raises_retryable(self):
        self.connector.create_code.side_effect = LockConnectionError("down")
        with expect_raises(self, RetryableJobError):
            self.code._sync_create()
        # State stays clean — neither failed nor partially synced
        self.assertFalse(self.code.failed)
        self.assertFalse(self.code.vendor_code_id)

    def test_offline_error_raises_retryable(self):
        self.connector.create_code.side_effect = LockOfflineError("offline")
        with expect_raises(self, RetryableJobError):
            self.code._sync_create()

    def test_lock_error_marks_failed_and_reraises(self):
        """Non-transient vendor errors mark the code as failed (so the
        UI can surface it) and re-raise. Failed codes drop out of the
        ``_should_have_lock_codes`` predicate's live filter."""
        self.connector.create_code.side_effect = LockAuthError("bad creds")
        with expect_raises(self, LockError):
            self.code._sync_create()
        self.assertTrue(self.code.failed)


class TestSyncModify(_SyncTestBase):
    def test_happy_path_applies_modified_result(self):
        new_result = _result(code_id="rotated-id", pin="2222")
        self.connector.modify_code.return_value = new_result
        new_from = datetime(2026, 6, 2, 13, 0)
        new_to = datetime(2026, 6, 5, 10, 0)
        self.code._sync_modify(date_from=new_from, date_to=new_to)
        kwargs = self.connector.modify_code.call_args.kwargs
        # Connector receives the *current* vendor_code_id of the
        # record (the one already on the lock); the result's
        # ``code_id`` is what gets persisted afterwards.
        self.assertEqual(kwargs["code_id"], "vendor-code-1")
        self.assertEqual(kwargs["starts_at"].tzinfo, timezone.utc)
        self.assertEqual(self.code.vendor_code_id, "rotated-id")
        self.assertEqual(self.code.sudo().pin, "2222")

    def test_deletion_error_applies_new_and_retries_invalidate(self):
        """Some vendors regenerate the code on modify (delete-and-create)
        rather than truly modify. ``LockCodeDeletionError`` carries the
        new result and the orphan ``old_code_id`` — the model must
        apply the new result and enqueue an invalidate of the old one."""
        new_result = _result(code_id="brand-new-id", pin="3333")
        exc = LockCodeDeletionError(
            "rotated", old_code_id="orphan-id", new_result=new_result
        )
        self.connector.modify_code.side_effect = exc

        # Patch ``with_delay`` to capture the retry enqueue without
        # actually scheduling a queue job.
        with patch.object(type(self.code), "with_delay") as with_delay_mock:
            delayed = MagicMock()
            with_delay_mock.return_value = delayed
            self.code._sync_modify(
                date_from=datetime(2026, 6, 2, 13, 0),
                date_to=datetime(2026, 6, 5, 10, 0),
            )
            self.assertEqual(self.code.vendor_code_id, "brand-new-id")
            self.assertEqual(self.code.sudo().pin, "3333")
            delayed._retry_invalidate.assert_called_once_with("orphan-id")

    def test_connection_error_raises_retryable(self):
        self.connector.modify_code.side_effect = LockConnectionError("down")
        with expect_raises(self, RetryableJobError):
            self.code._sync_modify(
                date_from=datetime(2026, 6, 2, 13, 0),
                date_to=datetime(2026, 6, 5, 10, 0),
            )
        self.assertFalse(self.code.failed)

    def test_lock_error_marks_failed(self):
        self.connector.modify_code.side_effect = LockAuthError("bad creds")
        with expect_raises(self, LockError):
            self.code._sync_modify(
                date_from=datetime(2026, 6, 2, 13, 0),
                date_to=datetime(2026, 6, 5, 10, 0),
            )
        self.assertTrue(self.code.failed)


class TestSyncRemove(_SyncTestBase):
    def test_happy_path_invalidates_and_marks_cancelled(self):
        self.connector.invalidate_code.return_value = None
        self.code._sync_remove()
        self.connector.invalidate_code.assert_called_once_with(
            lock_id=self.code.room_id.lock_device_id,
            code_id=self.code.vendor_code_id,
        )
        # The cancelled flag gets written **after** vendor confirmation
        # — the safety-first invariant from the design memo.
        self.assertTrue(self.code.cancelled)

    def test_connection_error_raises_retryable_keeping_state(self):
        """Critical: a transient remove failure must NOT set
        ``cancelled=True``. Otherwise Odoo would say the code is
        invalid but the lock would still honour it — exactly the
        physical-access risk the safety-first invariant guards against."""
        self.connector.invalidate_code.side_effect = LockOfflineError("offline")
        with expect_raises(self, RetryableJobError):
            self.code._sync_remove()
        self.assertFalse(self.code.cancelled)
        self.assertFalse(self.code.failed)

    def test_lock_error_marks_failed_not_cancelled(self):
        """Non-transient remove failure → ``failed=True``, ``cancelled``
        stays False. The UI should surface this loudly: cancel failed,
        code still valid on the lock."""
        self.connector.invalidate_code.side_effect = LockAuthError("bad creds")
        with expect_raises(self, LockError):
            self.code._sync_remove()
        self.assertTrue(self.code.failed)
        self.assertFalse(self.code.cancelled)
