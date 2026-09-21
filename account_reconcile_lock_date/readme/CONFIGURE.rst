Go to **Settings > Accounting**, section **Invoicing**, and set
**Reconciliation lock date**. The setting is stored per company.

It is a scope selector:

* **Do not block** -- the guard is disabled. This is the default, so installing
  the module changes nothing until somebody opts in.
* **Down payment invoices only** -- a reconciliation is guarded as soon as
  *either* matched entry is a down payment invoice.
* **Every reconciliation** -- all of them are guarded.

The date used is the company's own accounting lock date, resolved as
``max(period_lock_date, fiscalyear_lock_date)``.

This is **not** ``_get_user_fiscal_lock_date()`` on purpose. That method
replaces the date with ``fiscalyear_lock_date`` for users holding *Accounting /
Adviser* instead of taking the maximum, which would make the month-end close
invisible to the very people doing the closing, and it resolves against the
acting user, which is the superuser under any ``sudo()`` call.

Scope
~~~~~

**Down payment invoices only** delegates to ``account.move._is_downpayment()``.
That is a hook of ``account`` which returns ``False`` on its own and is
overridden by ``sale`` (invoices whose sale order lines are all down payments)
and, on a PMS install, by ``pms`` (invoices whose folio lines are all down
payments). On a database with neither of them installed this scope behaves
exactly like *Do not block*.

Because the predicate has ``all()`` semantics, a **mixed final invoice** that
deducts a down payment is *not* itself a down payment invoice and is therefore
out of scope. Override ``account.move._is_reconcile_lock_in_scope()`` to change
what the guard considers in scope -- that method is the extension point and it
is the only thing another module needs to touch.

Bypass
~~~~~~

Two escapes, both explicit:

* The group **Accounting / Skip reconciliation lock date**. Grant it to the
  accountant who has to fix something and remove it afterwards. Note that
  ``sudo()`` does **not** lift the guard -- group membership is checked against
  the acting user -- so scheduled actions running as OdooBot are blocked like
  anybody else. That is deliberate, but it means enabling a scope other than
  *Do not block* will make any automated flow that undoes reconciliations of
  closed periods fail loudly.
* The context key ``bypass_reconcile_lock_date``, for migration scripts. The
  guard also sets it itself before delegating to ``super()``, so the core's own
  reversal of cash basis and exchange difference entries -- which loops back
  into ``unlink()`` -- does not deadlock against it.
