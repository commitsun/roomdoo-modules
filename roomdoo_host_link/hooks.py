from odoo import SUPERUSER_ID, api


def post_init_hook(cr, registry):
    """Adopt the technical user the operations tools already use, or create it."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["roomdoo.host.link"]._ensure_technical_user()
