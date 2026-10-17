# Copyright 2026 Roomdoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

SERVICE_PRODUCT_DOMAIN = "[('type', '=', 'service'), ('is_pms_available', '=', True)]"


class ChannelWubookBackend(models.Model):
    _inherit = "channel.wubook.backend"

    extras_import_start = fields.Datetime(
        string="Import Extras From",
        help="Folios created before this moment never get channel extras: "
        "staff added them by hand. Leave it empty to not import channel "
        "extras in this backend.",
    )
    extra_fallback_product_id = fields.Many2one(
        comodel_name="product.product",
        string="Fallback Extras Product",
        domain=SERVICE_PRODUCT_DOMAIN,
        check_pms_properties=True,
        help="Product of the channel extras that have no product of their "
        "own, so the folio total matches what the guest pays. Leave it empty "
        "to leave those extras pending and report them on the folio.",
    )
    extra_ids = fields.One2many(
        comodel_name="channel.wubook.backend.extra",
        inverse_name="backend_id",
        string="Channel Extras",
        context={"active_test": False},
    )

    def _channel_extras_enabled(self, id_channel, folio=None):
        """Whether the extras of the bookings of ``id_channel`` are imported.

        That needs the backend to have a start date and the channel to import
        extras (OTA mapping of the backend type), and only folios created
        from that date get them. ``folio`` is the folio of the booking, none
        when it is being created now. The cheapest checks go first: most
        backends import no extras at all.
        """
        self.ensure_one()
        start = self.extras_import_start
        if not start or not id_channel:
            return False
        if not any(
            ota.import_extras
            for ota in self.backend_type_id.child_id.ota_ids
            if ota.wubook_ota == id_channel
        ):
            return False
        return ((folio and folio.create_date) or fields.Datetime.now()) >= start


class ChannelWubookBackendExtra(models.Model):
    _name = "channel.wubook.backend.extra"
    _description = "Channel Wubook Backend Extra Map"
    _order = "backend_id, name, id"
    _check_pms_properties_auto = True

    backend_id = fields.Many2one(
        comodel_name="channel.wubook.backend",
        string="Backend",
        required=True,
        ondelete="cascade",
        index=True,
    )
    pms_property_id = fields.Many2one(
        related="backend_id.pms_property_id",
        store=True,
    )
    name = fields.Char(
        string="Extra Name",
        required=True,
        help="Name of the extra exactly as the channel sends it.",
    )
    normalized_name = fields.Char(
        compute="_compute_normalized_name",
        store=True,
        precompute=True,
        index=True,
        help="Key the bookings are matched with: the name without case, "
        "accents nor punctuation.",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        domain=SERVICE_PRODUCT_DOMAIN,
        check_pms_properties=True,
        help="Service the extra is imported as. Leave it empty to keep the "
        "extra pending: it is imported with the fallback product of the "
        "backend, if any, and reported on the folio otherwise.",
    )
    action = fields.Selection(
        selection=[("service", "Import as Service"), ("ignore", "Ignore")],
        required=True,
        default="service",
        help="Ignore the extras that must not reach the folio, e.g. those "
        "the channel lists at 0 as part of a package.",
    )
    active = fields.Boolean(default=True)
    service_count = fields.Integer(
        string="Imported Services",
        compute="_compute_service_count",
    )

    _sql_constraints = [
        (
            "backend_normalized_name_uniq",
            "unique(backend_id, normalized_name)",
            "This extra is already mapped in this backend.",
        ),
    ]

    @api.depends("name")
    def _compute_normalized_name(self):
        # Not imported at the top: loading the pms_folio package from here
        # would define its models before the rest of the module's ones.
        from ..pms_folio.channel_extras import normalize_extra_name

        for record in self:
            record.normalized_name = normalize_extra_name(record.name)

    def _compute_service_count(self):
        saved = self.filtered("id")
        groups = (
            self.env["pms.service"]
            .sudo()
            .read_group(
                [("wubook_extra_id", "in", saved.ids)],
                ["wubook_extra_id"],
                ["wubook_extra_id"],
            )
        )
        counts = {
            group["wubook_extra_id"][0]: group["wubook_extra_id_count"]
            for group in groups
        }
        for record in self:
            record.service_count = counts.get(record.id, 0)

    def write(self, vals):
        # Hotel managers edit the rows of their hotels, they must not hand
        # them to the backend of another hotel (record rules are only checked
        # on the rows as they were before the write)
        if (
            "backend_id" in vals
            and not self.env.su
            and not self.env.user.has_group("connector.group_connector_manager")
            and any(record.backend_id.id != vals["backend_id"] for record in self)
        ):
            raise AccessError(
                _(
                    "Only connector managers can move a channel extra to another "
                    "backend."
                )
            )
        return super().write(vals)

    @api.model
    def _get_for_import(self, backend, extra):
        """Return the map row of ``extra``, created as pending when unknown.

        :param extra: parsed extra (see ``parse_wubook_extras``).
        """
        rows = self.sudo().with_context(active_test=False)
        domain = [
            ("backend_id", "=", backend.id),
            ("normalized_name", "=", extra["key"]),
        ]
        row = rows.search(domain, limit=1)
        if row:
            return row
        # Flushed out of the try: only the row below may hit the constraint
        self.env.cr.flush()
        try:
            with self.env.cr.savepoint():
                return rows.create({"backend_id": backend.id, "name": extra["name"]})
        except psycopg2.IntegrityError:
            # Another import created it meanwhile
            return rows.search(domain, limit=1)

    def _pending_folio_bindings(self):
        """Folio bindings still waiting for this extra (pending or failed)."""
        self.ensure_one()
        bindings = self.env["channel.wubook.pms.folio"].sudo()
        bindings.flush_model(["backend_id", "channel_extras_state"])
        self.env.cr.execute(
            """
            SELECT id
            FROM channel_wubook_pms_folio
            WHERE backend_id = %s
                AND channel_extras_state -> 'items' -> %s ->> 'state'
                    IN ('pending', 'failed')
            ORDER BY id
            """,
            (self.backend_id.id, self.normalized_name),
        )
        return bindings.browse([row[0] for row in self.env.cr.fetchall()]).filtered(
            lambda binding: binding.state != "cancel"
        )

    def action_apply_to_pending_folios(self):
        """Import this extra on the folios that are still waiting for it.

        It works on the extras stored with the last import of each folio, so
        nothing is asked to Wubook.
        """
        self.check_access_rights("write")
        self.check_access_rule("write")
        updated = total = 0
        for record in self:
            for binding in record._pending_folio_bindings():
                total += 1
                folio = binding.odoo_id
                if (
                    binding._channel_extras_to_sync(folio)
                    and binding._sync_channel_extras_safe(folio)
                    and binding._channel_extras_entry_state(record.normalized_name)
                    == "applied"
                ):
                    updated += 1
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Channel Extras"),
                "message": _(
                    "%(updated)s of %(total)s pending folios updated.",
                    updated=updated,
                    total=total,
                ),
                "type": "success" if updated else "warning",
                "sticky": False,
            },
        }
