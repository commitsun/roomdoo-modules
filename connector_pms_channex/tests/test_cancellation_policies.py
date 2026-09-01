# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""The cancellation rule of the hotel, published as a Channex policy.

Channex splits a policy in three -- what holds until a deadline, what holds
after it, and the no-show -- and those are the three outcomes Odoo computes when
a reservation is cancelled. What Channex will not take is Odoo's habit of
charging a percentage *of part of* the stay, and a rule that does that is
refused rather than rounded: the policy is what the guest is promised, and pms
is what charges.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import ChannexConnectorCase

BINDING = "channel.channex.pms.cancelation.rule"
PROPERTY_BINDING = "channel.channex.pms.property"


@tagged("post_install", "-at_install")
class TestCancelationPolicyExport(ChannexConnectorCase):
    def setUp(self):
        super().setUp()
        self.env[PROPERTY_BINDING].with_context(connector_no_export=True).create(
            {"odoo_id": self.pms_property.id, "backend_id": self.backend.id}
        )
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        self.property_uuid = self.server.store["properties"][0]["id"]

    def _rule(self, **values):
        """A rule the hotel sells its default pricelist under."""
        rule = (
            self.env["pms.cancelation.rule"]
            .with_context(connector_no_export=True)
            .create({"name": "CR Channex", **values})
        )
        self.pricelist.with_context(connector_no_export=True).cancelation_rule_id = rule
        return rule

    def _sync(self, rule):
        rule._channex_reconcile()
        return self.server.calls_to("POST", "cancellation_policies")

    def _payload(self, rule):
        posts = self._sync(rule)
        self.assertEqual(len(posts), 1)
        return posts[0][2]["cancellation_policy"]

    # -- the payload -------------------------------------------------------

    def test_the_rule_becomes_a_policy_of_the_property(self):
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late="first",
            penalty_noshow=100,
            apply_on_noshow="all",
        )
        payload = self._payload(rule)
        self.assertEqual(payload["property_id"], self.property_uuid)
        self.assertEqual(payload["title"], "CR Channex")
        self.assertEqual(payload["currency"], self.company.currency_id.name)
        # In Odoo a cancellation in time never costs anything.
        self.assertEqual(payload["after_reservation_cancellation_logic"], "free")
        self.assertEqual(payload["cancellation_policy_logic"], "deadline")
        self.assertEqual(payload["cancellation_policy_deadline"], 3)
        self.assertEqual(payload["cancellation_policy_deadline_type"], "days")
        self.assertEqual(payload["cancellation_policy_mode"], "nights")
        self.assertEqual(payload["cancellation_policy_penalty"], "1")
        self.assertEqual(payload["non_show_policy"], "total_price")
        binding = self.env[BINDING].search([("odoo_id", "=", rule.id)])
        binding.invalidate_recordset()
        self.assertTrue(binding.external_id)

    def test_the_pre_payment_policy_is_stated_once_and_never_again(self):
        """Channex refuses to create a policy without one, and deposits are the
        one thing here Odoo has nothing to say about. An update is a merge on
        Channex, so leaving it out of the update is what lets a hotel arrange
        its own deposits and keep them."""
        rule = self._rule(days_intime=3, penalty_late=100, apply_on_late="first")
        self.assertEqual(self._payload(rule)["guarantee_payment_policy"], "none")
        rule.with_context(connector_no_export=True).name = "CR Renamed"
        self._sync(rule)
        put = self.server.calls_to("PUT", "cancellation_policies")[-1][2]
        self.assertNotIn("guarantee_payment_policy", put["cancellation_policy"])

    def test_the_property_is_left_naming_the_policy(self):
        """Which cannot happen in the same call: Channex will not take a policy
        for a property it does not have, and the property cannot name a policy
        that does not exist yet."""
        rule = self._rule(days_intime=3, penalty_late=100, apply_on_late="first")
        self._sync(rule)
        policy_uuid = self.server.store["cancellation_policies"][0]["id"]
        puts = self.server.calls_to("PUT", "properties")
        self.assertTrue(puts)
        self.assertEqual(
            puts[-1][2]["property"]["default_cancellation_policy_id"], policy_uuid
        )

    def test_a_property_with_no_rule_says_nothing_about_the_policy(self):
        """Rather than saying "none": a hotel that set its policy by hand in
        Channex before this connector existed would lose it otherwise, and what
        a guest was promised is not dropped quietly."""
        self.env[PROPERTY_BINDING].export_record(self.backend, self.pms_property)
        payload = self.server.calls_to("PUT", "properties")[-1][2]["property"]
        self.assertNotIn("default_cancellation_policy_id", payload)

    def test_a_second_sync_updates_the_same_policy(self):
        rule = self._rule(days_intime=3, penalty_late=100, apply_on_late="first")
        self._sync(rule)
        rule.with_context(connector_no_export=True).name = "CR Renamed"
        self._sync(rule)
        self.assertEqual(len(self._sync(rule)), 1)
        puts = self.server.calls_to("PUT", "cancellation_policies")
        self.assertEqual(puts[-1][2]["cancellation_policy"]["title"], "CR Renamed")

    def test_a_rule_no_property_sells_under_is_not_sent(self):
        """A policy is only reachable in Channex from a rate plan or from the
        property default, and rate plans are not bound yet, so any other rule
        would be a policy nothing could point at."""
        rule = self.env["pms.cancelation.rule"].create({"name": "CR Unused"})
        self.assertFalse(self._sync(rule))
        self.assertFalse(self.env[BINDING].search([("odoo_id", "=", rule.id)]))

    # -- the free window ---------------------------------------------------

    def test_no_free_window_is_sent_as_no_deadline(self):
        """With ``days_intime`` at zero Odoo treats every cancellation before
        arrival as in time, so the late penalty is unreachable. A deadline of
        zero days would say the opposite: that nothing is ever free."""
        rule = self._rule(days_intime=0, penalty_late=100, apply_on_late="first")
        payload = self._payload(rule)
        self.assertEqual(payload["cancellation_policy_logic"], "free")
        self.assertIsNone(payload["cancellation_policy_deadline"])

    def test_a_free_window_with_nothing_to_charge_after_it_is_free(self):
        rule = self._rule(days_intime=3, penalty_late=0, apply_on_late="first")
        self.assertEqual(self._payload(rule)["cancellation_policy_logic"], "free")

    # -- what a late cancellation costs ------------------------------------

    def test_the_whole_of_a_span_of_nights_travels_as_nights(self):
        rule = self._rule(
            days_intime=3, penalty_late=100, apply_on_late="days", days_late=4
        )
        payload = self._payload(rule)
        self.assertEqual(payload["cancellation_policy_mode"], "nights")
        self.assertEqual(payload["cancellation_policy_penalty"], "4")

    def test_a_percentage_of_the_whole_stay_travels_as_a_percentage(self):
        rule = self._rule(
            days_intime=3,
            penalty_late=50,
            apply_on_late="all",
            penalty_noshow=50,
            apply_on_noshow="all",
        )
        payload = self._payload(rule)
        self.assertEqual(payload["cancellation_policy_mode"], "percent")
        self.assertEqual(payload["cancellation_policy_penalty"], "50")

    def test_a_percentage_of_part_of_the_stay_cannot_be_published(self):
        """Half of two nights is not one night once the two are priced
        differently, which is the normal case. Refused rather than rounded."""
        rule = self._rule(days_intime=3, penalty_late=50, apply_on_late="first")
        with self.assertRaises(ValidationError):
            rule._channex_reconcile()
        self.assertFalse(self.server.calls_to("POST", "cancellation_policies"))

    def test_an_empty_span_is_read_as_the_whole_stay(self):
        """Neither selection is required in pms, and pms itself reads an empty
        one as every day: it enters no branch and keeps the whole stay. Trusting
        the default the database does not enforce would publish two nights where
        pms charges five."""
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late=False,
            days_late=2,
            penalty_noshow=100,
            apply_on_noshow=False,
        )
        payload = self._payload(rule)
        self.assertEqual(payload["cancellation_policy_mode"], "percent")
        self.assertEqual(payload["cancellation_policy_penalty"], "100")
        self.assertEqual(payload["non_show_policy"], "total_price")

    def test_a_span_of_no_nights_is_free(self):
        """pms adds up no nights and charges nothing, so saying "nights: 0"
        would be saying something Channex has to interpret."""
        rule = self._rule(
            days_intime=3, penalty_late=100, apply_on_late="days", days_late=0
        )
        self.assertEqual(self._payload(rule)["cancellation_policy_logic"], "free")

    # -- the no-show -------------------------------------------------------

    def test_a_no_show_charged_whole_travels_as_the_total_price(self):
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late="first",
            penalty_noshow=100,
            apply_on_noshow="all",
        )
        self.assertEqual(self._payload(rule)["non_show_policy"], "total_price")

    def test_a_no_show_charged_like_a_cancellation_travels_as_the_default(self):
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late="first",
            penalty_noshow=100,
            apply_on_noshow="first",
        )
        self.assertEqual(self._payload(rule)["non_show_policy"], "default")

    def test_a_no_show_of_its_own_cannot_be_published(self):
        """Channex has two postures on a no-show and no more, and there is not
        even a percentage mode to fall back on here."""
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late="first",
            penalty_noshow=50,
            apply_on_noshow="all",
        )
        with self.assertRaises(ValidationError):
            rule._channex_reconcile()

    def test_a_no_show_span_is_never_taken_for_a_cancellation(self):
        """Identical on the screen, and not identical when charged: pms reads
        the *late* span for a no-show, minus one, and never the no-show span it
        was given. Publishing "same as a cancellation" here would promise
        something pms does not do."""
        rule = self._rule(
            days_intime=3,
            penalty_late=100,
            apply_on_late="days",
            days_late=4,
            penalty_noshow=100,
            apply_on_noshow="days",
            days_noshow=4,
        )
        with self.assertRaises(ValidationError):
            rule._channex_reconcile()


@tagged("post_install", "-at_install")
class TestCancelationPolicyListeners(ChannexConnectorCase):
    """What has to happen for the policy to go out of step, and does not.

    Three things change what a hotel promises: the rule itself, which rule its
    pricelist answers to, and which pricelist it sells by default. All three are
    watched, unlike a room type, where only the life cycle is: a policy that
    says something other than what pms will charge is worse than none.
    """

    def setUp(self):
        super().setUp()
        self.env["channel.channex.pms.property"].export_record(
            self.backend, self.pms_property
        )
        rule = (
            self.env["pms.cancelation.rule"]
            .with_context(connector_no_export=True)
            .create({"name": "CR Channex", "days_intime": 3, "penalty_late": 100})
        )
        # The flag travels in the context of the recordset ``create`` hands
        # back, so a write on that one would be skipped too. A clean recordset
        # is what a person editing the rule actually has.
        self.rule = self.env["pms.cancelation.rule"].browse(rule.id)

    def _synced(self, trap):
        return [
            job
            for job in trap.enqueued_jobs
            if job.method_name == "channex_sync"
            and job.recordset._name == "pms.cancelation.rule"
        ]

    def test_a_pricelist_pointed_at_a_rule_queues_it(self):
        with trap_jobs() as trap:
            self.pricelist.cancelation_rule_id = self.rule
            jobs = self._synced(trap)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].recordset, self.rule)

    def test_changing_the_rule_queues_it(self):
        self.pricelist.with_context(
            connector_no_export=True
        ).cancelation_rule_id = self.rule
        with trap_jobs() as trap:
            self.rule.penalty_late = 50
            jobs = self._synced(trap)
        self.assertEqual(len(jobs), 1)

    def test_a_property_changing_its_default_pricelist_queues_the_new_rule(self):
        other = self.env["product.pricelist"].create(
            {"name": "Channex PL 2", "company_id": self.company.id}
        )
        other.with_context(connector_no_export=True).cancelation_rule_id = self.rule
        with trap_jobs() as trap:
            self.pms_property.default_pricelist_id = other
            jobs = self._synced(trap)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].recordset, self.rule)

    def test_a_pricelist_change_of_no_consequence_queues_nothing(self):
        self.pricelist.with_context(
            connector_no_export=True
        ).cancelation_rule_id = self.rule
        with trap_jobs() as trap:
            self.pricelist.name = "Channex PL Renamed"
            self.assertFalse(self._synced(trap))
