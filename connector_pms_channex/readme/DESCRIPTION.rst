Connector for the `Channex.io <https://channex.io>`_ channel manager.

Channex has no booking engine of its own: it connects a property to OTAs. Odoo
is the source of truth for the master data, which it creates on Channex through
the API and tracks by UUID.

Designed to coexist with another channel manager in the same instance and in the
same hotel, which is what ``connector_pms`` provides.
