from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from roomdoo_locks_base import (
    AccessGrant,
    LockAuthError,
    LockConnectionError,
    LockError,
    LockOfflineError,
)

from odoo.sql_db import db_connect

from odoo.addons.pms_smartlock_base.models.lock_code import (
    _GATEWAY_LOCK_CLASSID,
    _GATEWAY_LOCK_RETRY_BASE,
    _GATEWAY_LOCK_RETRY_JITTER,
)
from odoo.addons.queue_job.exception import RetryableJobError

from .common import CommonSmartlock


@contextmanager
def expect_raises(test, exc_type):
    """Assert that the block raises ``exc_type`` (or a subclass), failing
    with a helpful message otherwise. A thin wrapper kept for readability
    across the sync tests."""
    try:
        yield
    except exc_type:
        return
    except BaseException as exc:
        test.fail(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
    test.fail(f"expected {exc_type.__name__}, no exception raised")


def _grant(
    pin="9876",
    ref="vendor-grant-x",
    starts_at=None,
    ends_at=None,
):
    return AccessGrant(
        pin=pin,
        ref=ref,
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
        # _sync_create grants from scratch; clear the planted grant ref so
        # the create flow is the relevant path.
        self.code.sudo().write({"vendor_grant_ref": False, "pin": False})

    def test_happy_path_applies_grant_and_calls_connector_with_utc(self):
        grant = _grant(ref="created-1", pin="1111")
        self.connector.grant_access.return_value = grant

        self.code._sync_create()

        self.assertEqual(self.connector.grant_access.call_count, 1)
        kwargs = self.connector.grant_access.call_args.kwargs
        # The grant covers the room's own lock (no shared locks planted).
        self.assertEqual(kwargs["lock_ids"], [self.code.room_id.lock_device_id])
        # Datetimes handed to the library must be tz-aware UTC; Odoo
        # stores naive UTC, so the model is responsible for the
        # ``_to_utc`` conversion.
        self.assertEqual(kwargs["starts_at"].tzinfo, timezone.utc)
        self.assertEqual(kwargs["ends_at"].tzinfo, timezone.utc)
        self.assertEqual(self.code.vendor_grant_ref, "created-1")
        self.assertEqual(self.code.sudo().pin, "1111")
        # The lock set is snapshotted into target_ids.
        self.assertEqual(len(self.code.target_ids), 1)
        target = self.code.target_ids
        self.assertEqual(target.kind, "room")
        self.assertEqual(target.room_id, self.code.room_id)
        self.assertEqual(target.lock_device_id, self.code.room_id.lock_device_id)

    def test_grant_covers_room_plus_shared_locks(self):
        """A room with a shared common lock grants both device ids under
        the same credential."""
        common = self._add_common_lock(self.code.room_id)
        self.connector.grant_access.return_value = _grant(ref="multi-1")

        self.code._sync_create()

        kwargs = self.connector.grant_access.call_args.kwargs
        self.assertEqual(
            kwargs["lock_ids"],
            [self.code.room_id.lock_device_id, common.lock_device_id],
        )
        self.assertEqual(set(self.code.target_ids.mapped("kind")), {"room", "common"})
        common_target = self.code.target_ids.filtered(lambda t: t.kind == "common")
        self.assertEqual(common_target.common_lock_id, common)

    def test_already_cancelled_skips_connector(self):
        """``cancelled=True`` before the job runs (e.g. the reservation
        was cancelled while the job was in queue) → no vendor call."""
        self.code.cancelled = True
        self.code._sync_create()
        self.connector.grant_access.assert_not_called()

    def test_post_create_cancellation_enqueues_remove(self):
        """Race: between sending the grant to the vendor and the return,
        the reservation got cancelled. After ``_apply_grant`` re-reads
        ``cancelled`` from DB, if it's True we must enqueue a
        ``_sync_remove`` to clean up the freshly-created grant."""
        self.connector.grant_access.return_value = _grant()
        original_apply = type(self.code)._apply_grant

        def race_apply(record, grant, specs=None):
            original_apply(record, grant, specs=specs)
            self.env.cr.execute(
                "UPDATE lock_code SET cancelled = TRUE WHERE id = %s",
                (record.id,),
            )

        with patch.object(
            type(self.code), "_enqueue_sync", autospec=True
        ) as enqueue_mock, patch.object(type(self.code), "_apply_grant", race_apply):
            self.code._sync_create()
            enqueue_mock.assert_called_once_with(self.code, "_sync_remove")

    def test_connection_error_raises_retryable(self):
        self.connector.grant_access.side_effect = LockConnectionError("down")
        with expect_raises(self, RetryableJobError):
            self.code._sync_create()
        # State stays clean — neither failed nor partially synced
        self.assertFalse(self.code.failed)
        self.assertFalse(self.code.vendor_grant_ref)

    def test_offline_error_raises_retryable(self):
        self.connector.grant_access.side_effect = LockOfflineError("offline")
        with expect_raises(self, RetryableJobError):
            self.code._sync_create()

    def test_lock_error_persists_failed_and_reraises(self):
        """Non-transient vendor errors persist the failure via
        ``_persist_failed`` (which writes in its own transaction so the flag
        survives the job rollback) and re-raise so queue_job records the
        traceback. The cross-transaction persistence cannot be observed from a
        TransactionCase (single, never-committed transaction), so we assert the
        contract: the error path invokes ``_persist_failed`` and propagates."""
        self.connector.grant_access.side_effect = LockAuthError("bad creds")
        with patch.object(type(self.code), "_persist_failed") as persist:
            with expect_raises(self, LockError):
                self.code._sync_create()
        persist.assert_called_once()


class TestSyncModify(_SyncTestBase):
    def test_happy_path_applies_modified_grant(self):
        new_grant = _grant(ref="rotated-ref", pin="2222")
        self.connector.modify_access.return_value = new_grant
        new_from = datetime(2026, 6, 2, 13, 0)
        new_to = datetime(2026, 6, 5, 10, 0)
        self.code._sync_modify(date_from=new_from, date_to=new_to)
        kwargs = self.connector.modify_access.call_args.kwargs
        # Connector receives the *current* opaque ref of the grant.
        self.assertEqual(kwargs["grant_ref"], "vendor-grant-1")
        self.assertEqual(kwargs["starts_at"].tzinfo, timezone.utc)
        self.assertEqual(self.code.vendor_grant_ref, "rotated-ref")
        self.assertEqual(self.code.sudo().pin, "2222")

    def test_connection_error_raises_retryable(self):
        self.connector.modify_access.side_effect = LockConnectionError("down")
        with expect_raises(self, RetryableJobError):
            self.code._sync_modify(
                date_from=datetime(2026, 6, 2, 13, 0),
                date_to=datetime(2026, 6, 5, 10, 0),
            )
        self.assertFalse(self.code.failed)

    def test_lock_error_persists_failed(self):
        self.connector.modify_access.side_effect = LockAuthError("bad creds")
        with patch.object(type(self.code), "_persist_failed") as persist:
            with expect_raises(self, LockError):
                self.code._sync_modify(
                    date_from=datetime(2026, 6, 2, 13, 0),
                    date_to=datetime(2026, 6, 5, 10, 0),
                )
        persist.assert_called_once()


class TestSyncRemove(_SyncTestBase):
    def test_happy_path_revokes_and_marks_cancelled(self):
        self.connector.revoke_access.return_value = True
        self.code._sync_remove()
        self.connector.revoke_access.assert_called_once_with(
            grant_ref=self.code.vendor_grant_ref,
            pin=self.code.pin,
        )
        # The cancelled flag gets written **after** vendor confirmation
        # — the safety-first invariant from the design memo.
        self.assertTrue(self.code.cancelled)

    def test_connection_error_raises_retryable_keeping_state(self):
        """Critical: a transient remove failure must NOT set
        ``cancelled=True``. Otherwise Odoo would say the grant is
        revoked but the locks would still honour it — exactly the
        physical-access risk the safety-first invariant guards against."""
        self.connector.revoke_access.side_effect = LockOfflineError("offline")
        with expect_raises(self, RetryableJobError):
            self.code._sync_remove()
        self.assertFalse(self.code.cancelled)
        self.assertFalse(self.code.failed)

    def test_lock_error_persists_failed_not_cancelled(self):
        """Non-transient remove failure → ``_persist_failed`` is invoked and
        ``cancelled`` stays False. The UI must surface this loudly: revoke
        failed, the grant is still valid on the locks."""
        self.connector.revoke_access.side_effect = LockAuthError("bad creds")
        with patch.object(type(self.code), "_persist_failed") as persist:
            with expect_raises(self, LockError):
                self.code._sync_remove()
        persist.assert_called_once()
        self.assertFalse(self.code.cancelled)


class TestFailedFlagClearing(_SyncTestBase):
    """A previously failed credential must recover: a successful sync, or
    simply enqueuing a fresh one, clears ``failed`` so the state stops
    being stuck on ``failed``."""

    def test_successful_sync_clears_failed(self):
        """``_apply_grant`` runs on the happy path of a modify; it must wipe
        a prior failure flag in the same (committing) transaction."""
        self.code.sudo().failed = True
        self.connector.modify_access.return_value = _grant(ref="recovered")
        self.code._sync_modify(
            date_from=datetime(2026, 6, 2, 13, 0),
            date_to=datetime(2026, 6, 5, 10, 0),
        )
        self.assertFalse(self.code.failed)

    def test_enqueue_sync_clears_failed(self):
        """Enqueuing a new sync supersedes the prior failure up front, so the
        code shows ``syncing`` instead of staying on ``failed``."""
        self.code.sudo().failed = True
        self.code._enqueue_sync("_sync_remove")
        self.assertFalse(self.code.failed)


class TestGatewaySerialization(_SyncTestBase):
    """A vendor gateway can't service two requests at once. Before any
    vendor call the sync grabs a transaction-level advisory lock per
    ``lock_device_id``; if another job holds it the sync re-enqueues
    instead of hitting the busy gateway. The contention retry must not
    count against ``max_retries`` (``ignore_retry``), so a credential
    never ends up ``failed`` just for losing a gateway race."""

    @contextmanager
    def _hold_gateway_lock(self, device_id):
        """Hold the same advisory lock the model takes, from a *separate*
        DB connection (advisory locks are connection/transaction scoped),
        so the code under test — running on the test cursor — sees it as
        held by someone else. Released on rollback when the block exits."""
        connection = db_connect(self.env.cr.dbname)
        cr2 = connection.cursor()
        try:
            cr2.execute(
                "SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))",
                (_GATEWAY_LOCK_CLASSID, device_id),
            )
            self.assertTrue(
                cr2.fetchone()[0], "test setup: external lock should acquire"
            )
            yield
        finally:
            cr2.rollback()
            cr2.close()

    def test_try_lock_gateways_true_when_free(self):
        self.assertTrue(self.code._try_lock_gateways(["device-unused"]))

    def test_try_lock_gateways_false_when_held_elsewhere(self):
        with self._hold_gateway_lock("device-A"):
            self.assertFalse(self.code._try_lock_gateways(["device-A"]))
        # External holder gone → the device is acquirable again.
        self.assertTrue(self.code._try_lock_gateways(["device-A"]))

    def test_empty_device_list_acquires(self):
        """No devices to program → nothing to serialise on."""
        self.assertTrue(self.code._try_lock_gateways([]))

    def test_raise_gateway_busy_off_retry_counter(self):
        with self.assertRaises(RetryableJobError) as cm:
            self.code._raise_gateway_busy()
        # ``ignore_retry`` keeps contention off ``max_retries`` (job.py
        # decrements ``retry`` for these), so no ``failed`` from a race.
        self.assertTrue(cm.exception.ignore_retry)
        self.assertGreaterEqual(cm.exception.seconds, _GATEWAY_LOCK_RETRY_BASE)
        self.assertLessEqual(
            cm.exception.seconds,
            _GATEWAY_LOCK_RETRY_BASE + _GATEWAY_LOCK_RETRY_JITTER,
        )

    def test_create_skips_vendor_when_gateway_busy(self):
        """The bottleneck scenario: many checkins share the main entrance.
        While another job holds that gateway, this create must re-enqueue
        without calling the vendor and without dirtying its state."""
        self.code.sudo().write({"vendor_grant_ref": False, "pin": False})
        with self._hold_gateway_lock(self.code.room_id.lock_device_id):
            with expect_raises(self, RetryableJobError):
                self.code._sync_create()
        self.connector.grant_access.assert_not_called()
        self.assertFalse(self.code.failed)
        self.assertFalse(self.code.vendor_grant_ref)

    def test_remove_skips_vendor_when_gateway_busy(self):
        """Remove reads the locks from the grant snapshot. While the
        shared door is busy, revoke must not run — and crucially
        ``cancelled`` must stay False (safety-first invariant)."""
        common = self._add_common_lock(self.code.room_id)
        self.env["lock.code.target"].sudo().create(
            {
                "lock_code_id": self.code.id,
                "kind": "common",
                "lock_device_id": common.lock_device_id,
                "common_lock_id": common.id,
            }
        )
        with self._hold_gateway_lock(common.lock_device_id):
            with expect_raises(self, RetryableJobError):
                self.code._sync_remove()
        self.connector.revoke_access.assert_not_called()
        self.assertFalse(self.code.cancelled)
        self.assertFalse(self.code.failed)
