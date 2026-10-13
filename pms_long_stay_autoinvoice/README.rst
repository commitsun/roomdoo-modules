================================
PMS Long Stay Autoinvoice Bridge
================================

This bridge module connects ``pms_long_stay`` and ``pms_autoinvoice`` so
the monthly billing of residence/long-stay tenants follows the residence
rule:

* **Long-stay service line** (the "pernocta as a service" automatically
  created by ``pms_long_stay`` for each monthly segment, carrying the
  monthly fee 1000-1600 €): autoinvoiced on **day 1 of the month** the
  reservation checks in. This bills the period in advance — on Feb 1 the
  tenant is charged for February in full.

* **Other services** (medical visits, laundry, …) consumed inside a
  long-stay reservation: deferred to **day 1 of the month after** the
  service line's date. So a service consumed on Mar 20 is invoiced on
  Apr 1, together with the April long-stay segment.

* **Pernocta line** (the reservation_line, priced 0 € on long-stay):
  never autoinvoiced.

* **Standard (non long-stay) reservations**: standard
  ``pms_autoinvoice`` behaviour, unchanged.

The module is ``auto_install = True``, so it activates the moment both
dependencies are installed.

Installation
============

Installs automatically when both ``pms_long_stay`` and ``pms_autoinvoice``
are present. A ``post_init_hook`` recomputes ``autoinvoice_date`` on all
existing long-stay folio sale lines so the new rule kicks in immediately
for live tenants.

Usage
=====

In the property settings, set:

* ``default_invoicing_policy = 'month_day'``
* ``invoicing_month_day = 1``
* ``margin_days_autoinvoice = 0``

After that, the daily ``pms.property.autoinvoicing()`` cron will pick up
the right lines on day 1 of each month and emit one invoice per resident
folio.

Limitations
===========

* Depends on the operator (or another flow) setting the correct
  ``reservation_id`` on each ``pms.service`` so the bridge can pick up
  the matching long-stay reservation.
* Does not cover mid-month withdrawals with penalty rules.

Credits
=======

* Roomdoo — Commit [Sun]
