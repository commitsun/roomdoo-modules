Adds a *Helpdesk / Support agent* role to the catalog of ``pms_user_roles``
when the Odoo Enterprise helpdesk is installed: internal user plus helpdesk
user, and nothing from the PMS, accounting or other applications.

The ``_enterprise`` suffix is deliberate: this module depends on the
``helpdesk`` module of Odoo Enterprise, not on the community ``helpdesk_mgmt``,
which defines a model and groups with the same names. It is installed
automatically when both dependencies are present.
