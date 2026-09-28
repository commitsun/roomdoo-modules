A down payment is invoiced long before the stay it belongs to is over, and by
then almost anything can still change: the guest turns out to be someone else,
part of the money is given back, the booking is cancelled, or the amount
collected was simply wrong. What ``pms`` does in each of those cases either
rewrites a period that has already been filed or destroys the invoice without
issuing a replacement.

This module takes over the four of them. In all of them the rule is the same:
**the payment is never unreconciled from its down payment**, and nothing is
posted into a closed period. Whatever has to change is done with a credit note
dated in an open period, which is how it is settled in law and the only way it
survives an audit.

**An invoice that never discounted the down payment.** pms discounts a down
payment by putting a line in the invoice that points back at it, and it does so
when the invoice is built. An invoice drafted before the down payment existed --
a proforma left pending for weeks, which is the common case -- carries no such
line, so validating it bills the stay in full and the down payment stays
standing. The module reads the discount off the invoice itself: what the invoice
already applied is settled business and is never touched, and what it did not
apply is rectified, with its credit note settled against the invoice. Nothing is
ever subtracted twice.

**A different customer on the final invoice.** ``pms`` skips the down payment
while building the final invoice and bills the stay in full, leaving the down
payment dangling on the other customer's account — normally the anonymous one,
because at the time of collection there was no guest data yet. The module
rectifies the down payment and posts an entry moving the balance from one
customer account to the other.

Only the branch where the down payment was issued to the anonymous customer is
automated. When it was issued to an identified third party the case is flagged
for accounting instead: rectifying VAT charged to a real customer without
refunding them is a different operation, and one that art. 89.Cinco LIVA does
not settle the same way.

**A refund.** Today a refund is billed as a brand new credit note off the folio
wizard: a document with no ``reversed_entry_id``, which rectifies nothing and
leaves the original down payment standing for its full amount. The module makes
it a partial rectification of the down payment it actually gives back, for
exactly the amount returned.

**A cancellation.** What is left of the down payment is rectified in full. The
credit note is left open, in favour of the customer: the stay will not be
invoiced, so nothing of the folio absorbs it. The cancellation penalty — which
``pms`` already adds to the folio as a service line — is billed as its own
document and takes its part.

**A change of amount.** ``pms`` reverses the down payment with ``cancel=True``,
which unreconciles the payment and can rewrite a closed period, or unlinks it
outright, and in neither case does it issue a replacement: the folio is left
short of an invoice nobody notices is missing. The module rectifies it instead,
settles the credit note against it, and issues the down payment again for the
amount the payment ended up having.
