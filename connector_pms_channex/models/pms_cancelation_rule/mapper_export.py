# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create


class ChannelChannexPmsCancelationRuleExportMapper(Component):
    """The rule as a Channex policy.

    Channex splits a policy in three, and the three line up with the three
    outcomes Odoo computes when a reservation is cancelled. Verified against the
    sentence Channex itself composes for a saved policy, which is the only
    authoritative account of what the combination means:

        Free cancellation up to 3 days before arrival. After this time there
        will be a 3.00% fee. Non-show will be charged the total price.

    So: what holds until the deadline is Odoo's "in time", what holds after it
    is Odoo's "late", and the no-show is its own. Nothing is published that pms
    would not charge -- a policy is a promise to a guest, and a promise the PMS
    does not keep is worse than no policy at all.
    """

    _name = "channel.channex.pms.cancelation.rule.export.mapper"
    _inherit = "channel.channex.export.mapper"
    _apply_on = "channel.channex.pms.cancelation.rule"

    @mapping
    def identity(self, record):
        return {
            "property_id": record._channex_property_external_id(),
            "title": record.odoo_id.name,
            "currency": record.backend_id.pms_property_id.company_id.currency_id.name,
        }

    @only_create
    @mapping
    def guarantee(self, record):
        """Channex will not create a policy without a pre-payment policy, and
        that is the one thing here Odoo has no opinion about: deposits are not
        modelled at all. So it is stated once, as "none", and never again.

        Never again matters, and is safe: an update is a merge, not a
        replacement -- verified against staging, a pre-payment set by hand
        survived a PUT that did not mention it. So a hotel that arranges its
        deposits in Channex keeps them, while everything this connector does
        have an opinion about is sent on every write and does overwrite.
        """
        return {"guarantee_payment_policy": "none"}

    @mapping
    def in_time(self, record):
        """A cancellation in time costs nothing in Odoo, ever: it computes a
        penalty of zero and stops there. So the policy that holds until the
        deadline is always free, and a hotel that had set something else here by
        hand in Channex loses it -- which is what it means for Odoo to own the
        masters."""
        return {
            "after_reservation_cancellation_logic": "free",
            "after_reservation_cancellation_amount": None,
        }

    @mapping
    def deadline(self, record):
        """The free window, and what is charged once it has passed."""
        rule = record.odoo_id
        # With no free window Odoo never reaches its late penalty: every
        # cancellation before the arrival date is in time. Sent as no deadline
        # at all, because a deadline of zero days would say something else --
        # that nothing is ever free. A span of no nights is the same story from
        # the other end: pms adds up no nights and charges nothing.
        if (
            not rule.days_intime
            or not rule.penalty_late
            or (self._channex_span(rule.apply_on_late) == "days" and rule.days_late < 1)
        ):
            return {
                "cancellation_policy_logic": "free",
                "cancellation_policy_deadline": None,
                "cancellation_policy_deadline_type": None,
                "cancellation_policy_mode": None,
                "cancellation_policy_penalty": None,
            }
        mode, penalty = self._channex_late_penalty(rule)
        return {
            "cancellation_policy_logic": "deadline",
            "cancellation_policy_deadline": rule.days_intime,
            # Odoo counts days and nothing else. Channex would also take hours.
            "cancellation_policy_deadline_type": "days",
            "cancellation_policy_mode": mode,
            "cancellation_policy_penalty": penalty,
        }

    def _channex_span(self, apply_on):
        """The span of nights a penalty covers, as pms actually reads it.

        Neither of these selections is required, and pms treats an empty one the
        same as "all days": it enters no branch and keeps the whole stay. So
        does this, rather than trusting a default the database does not enforce.
        """
        return apply_on if apply_on in ("first", "days") else "all"

    def _channex_late_penalty(self, rule):
        """What a late cancellation costs, in the one term Channex will take.

        Odoo says it with two knobs at once -- a percentage, and a span of
        nights to apply it to -- and Channex takes one: a percentage of the whole
        stay, or a number of nights charged whole. The two meet in two places.
        Where the span is the whole stay, the percentage says everything. Where
        the percentage is 100, the span does. In between there is nothing to
        send: half of two nights is not one night once the two nights are priced
        differently, which is the normal case.
        """
        span = self._channex_span(rule.apply_on_late)
        if span == "all":
            return "percent", str(rule.penalty_late)
        if rule.penalty_late == 100:
            return "nights", str(1 if span == "first" else rule.days_late)
        raise ValidationError(
            _(
                "Cancellation rule %(name)s charges %(percent)s%% of part of the "
                "stay, and Channex only takes a percentage of the whole stay or "
                "a number of nights charged in full. Either charge 100%% of "
                "those nights, or apply the percentage to every day."
            )
            % {"name": rule.name, "percent": rule.penalty_late}
        )

    @mapping
    def non_show(self, record):
        """A no-show, in the two postures Channex has for it: charged like a
        cancellation, or charged whole.

        Odoo can say a percentage of a span here too, and this time there is not
        even a percentage mode to fall back on, so most of what it can say
        cannot be published.
        """
        rule = record.odoo_id
        span = self._channex_span(rule.apply_on_noshow)
        if rule.penalty_noshow == 100 and span == "all":
            return {"non_show_policy": "total_price"}
        # "Charged following cancellation conditions" is what Channex calls its
        # default, so it only says the truth where Odoo charges a no-show the
        # same as a late cancellation.
        #
        # "Specify days" is left out of that comparison on purpose: pms reads
        # the *late* span for a no-show, minus one, and never the no-show span
        # it was given, so two columns that look identical on screen are not
        # charged identically.
        if (
            span in ("first", "all")
            and span == self._channex_span(rule.apply_on_late)
            and rule.penalty_noshow == rule.penalty_late
        ):
            return {"non_show_policy": "default"}
        raise ValidationError(
            _(
                "Cancellation rule %s charges a no-show differently from a late "
                "cancellation, and Channex only takes one of the two: the same "
                "as a cancellation, or the whole price."
            )
            % rule.name
        )
