# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.component.core import Component
from odoo.addons.queue_job.job import identity_exact

from ...components.adapter import ChannexAPIError

PRICELIST_BINDING = "channel.channex.product.pricelist"
ROOM_TYPE_BINDING = "channel.channex.pms.room.type"

#: What a rate plan can have changed since it was made. The occupancies are
#: not among them: Channex keeps them in line with the room type.
_UPDATABLE = ("title", "currency", "meal_type", "children_fee")


def _same(field, remote, wanted):
    """Channex hands a fee back as a string of its own formatting."""
    if field == "children_fee":
        return float(remote or 0) == float(wanted)
    return remote == wanted


class ChannelChannexRatePlanSync(Component):
    """Brings the rate plans of a property on Channex in line with what is
    sold: a rate plan for every pricelist connected, board service it sells
    here, and room type on Channex.

    The rate plans on Channex are read every time rather than trusted to be as
    they were left, so that one made but never recorded -- the transaction
    that made it failed afterwards -- is taken over and not made twice.
    Taken over by its title on its room type, which also takes over the ones
    the hotel made by hand under the same name, with what is mapped to them.
    Every other rate plan of the hotel is left alone.
    """

    _name = "channel.channex.rate.plan.sync"
    _inherit = "base.channel.channex.connector"
    _apply_on = "channel.channex.rate.plan"
    _usage = "rate_plan.sync"

    def run(self):
        """:return: how many rate plans were made, taken over, rewritten and
        queued for removal."""
        result = {"created": 0, "adopted": 0, "updated": 0, "removing": 0}
        property_external_id = self.backend_record._channex_bound_property_id()
        if not property_external_id:
            return result
        adapter = self.component(usage="backend.adapter")
        remote = {
            plan["id"]: plan
            for plan in adapter.search_read(
                [("property_id", "=", property_external_id)]
            )
        }
        rate_plans = self.model.search([("backend_id", "=", self.backend_record.id)])
        wanted = self._wanted()
        for rate_plan in rate_plans:
            if self._key(rate_plan) not in wanted:
                result["removing"] += self._remove(rate_plan, remote)
        rate_plans = rate_plans.exists()
        owned = set(rate_plans.mapped("external_id"))
        problems = []
        for key in wanted:
            rate_plan = rate_plans.filtered(
                lambda r, key=key: self._key(r) == key
            ) or self.model.create(
                {
                    "backend_id": self.backend_record.id,
                    "pricelist_id": key[0],
                    "board_service_id": key[1],
                    "room_type_binding_id": key[2],
                }
            )
            if rate_plan.channex_to_remove:
                rate_plan.channex_to_remove = False
            try:
                outcome = self._sync(adapter, rate_plan, remote, owned)
            except ChannexAPIError as error:
                problems.append(f"{rate_plan.display_name}: {error}")
                continue
            if outcome:
                result[outcome] += 1
            if outcome in ("created", "adopted"):
                # Nothing it holds on Channex is known to be right yet.
                rate_plan._channex_stage_restrictions()
        if problems:
            # What was recorded here is undone, but not what Channex took:
            # the next time it is taken over, never made twice.
            raise UserError(
                _("Channex refused some rate plans:\n%s") % "\n".join(problems)
            )
        return result

    @staticmethod
    def _key(rate_plan):
        return (
            rate_plan.pricelist_id.id,
            rate_plan.board_service_id.id,
            rate_plan.room_type_binding_id.id,
        )

    def _wanted(self):
        """``(pricelist, board service, room type binding)`` of every rate plan
        that should be on Channex."""
        pms_property = self.backend_record.pms_property_id
        room_types = self.env[ROOM_TYPE_BINDING].search(
            [
                ("backend_id", "=", self.backend_record.id),
                ("external_id", "!=", False),
            ]
        )
        # An archived pricelist hides its bindings, so it is not found here.
        bindings = self.env[PRICELIST_BINDING].search(
            [("backend_id", "=", self.backend_record.id)]
        )
        wanted = []
        for pricelist in bindings.odoo_id:
            if pricelist.pms_property_ids and (
                pms_property not in pricelist.pms_property_ids
            ):
                continue
            board_services = pricelist._get_available_board_services(
                pms_property, None, None
            )
            for board_service in board_services:
                for room_type in room_types:
                    wanted.append((pricelist.id, board_service.id, room_type.id))
        return wanted

    def _sync(self, adapter, rate_plan, remote, owned):
        """Bring one rate plan in line. :return: what was done, if anything."""
        values = rate_plan._channex_values()
        plan = remote.get(rate_plan.external_id)
        if not plan:
            plan = self._adoptable(values, remote, owned)
        if not plan:
            rate_plan.external_id = adapter.create(values)
            owned.add(rate_plan.external_id)
            return "created"
        outcome = None
        if rate_plan.external_id != plan["id"]:
            rate_plan.external_id = plan["id"]
            owned.add(plan["id"])
            outcome = "adopted"
        changes = {
            field: values[field]
            for field in _UPDATABLE
            if not _same(field, plan.get(field), values[field])
        }
        if changes:
            adapter.write(rate_plan.external_id, changes)
            outcome = outcome or "updated"
        return outcome

    def _adoptable(self, values, remote, owned):
        """The rate plan already on Channex under this title and room type,
        that no other one of ours is.

        Channex will not have two with the same title on a room type, so one
        priced some other way cannot be taken over, and this one cannot be
        made either: that is for the hotel to sort out on Channex.
        """
        for plan in remote.values():
            if plan["id"] in owned:
                continue
            if (plan.get("room_type_id"), plan.get("title")) != (
                values["room_type_id"],
                values["title"],
            ):
                continue
            if (plan.get("sell_mode"), plan.get("rate_mode")) != (
                "per_person",
                "manual",
            ):
                raise ChannexAPIError(
                    _(
                        "a rate plan with this name is already on Channex, priced "
                        "another way; rename it or remove it there"
                    )
                )
            return plan
        return None

    def _remove(self, rate_plan, remote):
        """Take down one rate plan no longer sold. :return: whether its removal
        was queued.

        One that never reached Channex, or is already gone from it, is just
        forgotten. The others are taken down on their own job, see
        ``channex_remove``, queued once: a removal Channex refuses stays failed
        until someone runs it again.
        """
        if rate_plan.external_id not in remote:
            rate_plan.unlink()
            return 0
        if rate_plan.channex_to_remove:
            return 0
        rate_plan.channex_to_remove = True
        rate_plan.with_delay(
            identity_key=identity_exact,
            description=_("Channex: remove rate plan %s") % rate_plan.display_name,
        ).channex_remove()
        return 1
