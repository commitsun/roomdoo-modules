from odoo import SUPERUSER_ID, api


def post_init_hook(cr, registry):
    """Refresh ``autoinvoice_date`` on existing folio sale lines bound to
    long-stay reservations.

    The compute's ``@api.depends`` is re-declared in this module to include
    new triggers (reservation type, product flag, service line date). For
    already-stored rows we need an explicit invalidate+recompute because
    Odoo only re-runs computes when a dependency actually mutates, not when
    the dependency *list* changes.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env["folio.sale.line"].search(
        [("reservation_id.reservation_type", "=", "long_stay")]
    )
    if not lines:
        return
    env.add_to_compute(
        env["folio.sale.line"]._fields["autoinvoice_date"], lines
    )
    lines.recompute()
