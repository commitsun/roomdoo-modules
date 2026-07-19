from roomdoo_locks_salto import SaltoProvider

from odoo import _, api, fields, models


class LockVendor(models.Model):
    _inherit = "lock.vendor"

    vendor_type = fields.Selection(
        selection_add=[("salto", "Salto KS")],
        ondelete={"salto": "cascade"},
    )

    @api.model
    def _pin_confirm_key_defaults(self):
        res = super()._pin_confirm_key_defaults()
        # Salto keypads validate the PIN with the Enter key (↵, U+21B5).
        res["salto"] = "↵"
        return res

    salto_env = fields.Selection(
        selection=[("prod", "Production"), ("acc", "Acceptance")],
        string="Salto Environment",
        default="prod",
        help="Salto KS environment this hotel's account operates on. "
        "Acceptance is Salto's sandbox; production is the live system.",
    )
    salto_username = fields.Char(string="Salto Username")
    salto_password = fields.Char(string="Salto Password")
    salto_site_id = fields.Char(
        string="Salto Site ID",
        help="ID of the Salto KS site this vendor configuration operates on.",
    )
    salto_role_ids = fields.One2many(
        comodel_name="salto.role",
        inverse_name="vendor_id",
        string="Available Salto Roles",
        help="Roles fetched from the Salto KS site. Refresh them with the "
        "'Fetch Salto roles' button.",
    )
    salto_role_id = fields.Many2one(
        comodel_name="salto.role",
        string="Guest Role",
        domain="[('vendor_id', '=', id)]",
        ondelete="restrict",
        help="Role assigned to each guest user created for an access grant. "
        "Use the basic 'User' role (only opens doors); never an admin role. "
        "Populate the list with 'Fetch Salto roles' first.",
    )

    def get_connector(self):
        self.ensure_one()
        if self.vendor_type == "salto":
            # client_id/secret identify Roomdoo's Salto KS API app and live in
            # the environment; the hotel's own account (username/password) and
            # its site/role live on the record.
            kwargs = {
                "clientId": self._required_env("SALTO_CLIENT_ID"),
                "clientSecret": self._required_env("SALTO_CLIENT_SECRET"),
                "username": self.salto_username,
                "password": self.salto_password,
                "siteId": self.salto_site_id,
                # The connector wants the Salto-side id, not our record id.
                # Empty (no role picked yet) is fine for listing roles; the
                # library guards grant_access when it's missing.
                "role_id": self.salto_role_id.salto_id or None,
                "env": self.salto_env or "prod",
                # Salto stores time-schedule datetimes as naive wall-clock and
                # the site's IQ enforces them in the hotel's local timezone, not
                # UTC. The caller (lock.code) puts that timezone on the context
                # so the connector can localize the UTC window before sending.
                "time_zone": self.env.context.get("smartlock_local_tz"),
            }
            # Salto is user-centric: the guest's name and email go on the site
            # user it creates, and a readable label on the access group, so the
            # hotel can tell whose credential is whose in the Salto KS app.
            # ``_sync_create`` puts the reservation on the context for exactly
            # this; other operations (modify/revoke) don't need it and other
            # vendors never read it.
            reservation = self.env.context.get("smartlock_grant_reservation")
            if reservation:
                kwargs.update(self._salto_guest_kwargs(reservation))
            return SaltoProvider(**kwargs)
        return super().get_connector()

    def _salto_guest_kwargs(self, reservation):
        """Map a reservation onto the Salto guest-identity constructor params.

        Salto wants a first/last name; ``partner_name`` is a single string, so
        we split on the first space (everything after it is the last name) and
        fall back to a generic first name when it is empty. The reservation's
        ``name`` labels the access group for traceability.

        Salto requires a non-empty last name too: single-word names (or none)
        leave it blank and the API rejects the user ("Last name must be set"),
        so fall back to a neutral placeholder.

        The guest email is deliberately **not** sent: Salto KS emails the
        address on file (digital-key invitations and the like), and the PIN flow
        must stay silent — guest communication is a separate concern owned
        elsewhere. Leaving it empty keeps Salto from contacting the guest."""
        first, _sep, last = (reservation.partner_name or "").strip().partition(" ")
        return {
            "guest_first_name": first or "Guest",
            "guest_last_name": last or "-",
            "access_group_name": reservation.name or "Roomdoo Access",
        }

    def action_fetch_salto_roles(self):
        """Pull the site's roles from Salto into ``salto_role_ids`` so the
        operator can pick the guest role. Idempotent: refreshes names of roles
        already known and adds new ones. Needs no role configured yet."""
        self.ensure_one()
        roles = self.get_connector().list_roles()
        known = {role.salto_id: role for role in self.salto_role_ids}
        for role in roles:
            salto_id = role.get("id")
            if not salto_id:
                continue
            vals = {"name": role.get("name") or salto_id}
            if salto_id in known:
                known[salto_id].write(vals)
            else:
                self.env["salto.role"].create(
                    {"vendor_id": self.id, "salto_id": salto_id, **vals}
                )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Fetched %s Salto role(s).") % len(roles),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
