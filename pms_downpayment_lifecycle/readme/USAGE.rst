Nothing here is done inline, with one deliberate exception. Invoicing a stay,
giving money back and cancelling a booking are operational acts, and none of
them can fail because of what an old invoice looks like: they are marked and
enqueued, and the accounting work runs in its own job. The exception is
re-issuing a down payment after its amount changed, which *is* the operation
being asked for: if it cannot be done the change is refused, so the folio is
never left quietly short of an invoice.

Which down payment belongs to which invoice is decided, never guessed. A down
payment is taken up only when this invoice is plainly the one it belongs to:
what is standing has to fit within what the invoice bills, and the folio has to
have nothing left to invoice. As soon as either fails -- more standing than this
invoice bills, or another invoice still to come -- **nothing is posted** and the
case is left for review with the figures written on it. Associating money by
guesswork is worse than asking.

A job that cannot complete leaves its record in **Manual review** with the
reason written on it, in the chatter and in the pending list. The usual reasons:

* The down payment was issued to an identified third party.
* Either invoice does not have exactly one customer line, which happens with
  instalment payment terms and makes the balance to move ambiguous.
* Nothing was actually collected against the down payment.
* More was given back than was ever invoiced as a down payment; the part that
  rectifies nothing is left unapplied rather than invented.
* More is standing as down payments than this invoice bills, or the folio still
  has lines to invoice, so which invoice the down payment belongs to is not
  settled.
* An older draft refund of the same journal blocks posting the credit note.
  ``account_invoice_constraint_chronology`` refuses it, so the message names
  the drafts to validate or cancel first.

Amounts are read where they are true. What is moved between customers is what
was **actually collected**, not what was invoiced: moving the full amount of a
down payment that was only half paid would credit the new customer money nobody
ever paid. What is **rectified**, on the other hand, is measured on the invoiced
total, because a credit note is a fiscal document and it rectifies what was
billed. Anything already rectified is discounted from it, which is what keeps
two refunds from rectifying the same euro twice.

Worth knowing: the credit note inherits the down payment's property, so a down
payment created outside the wizard without a coherent ``pms_property_id`` makes
the operation fail on the multi-property check.

Two consequences of the design that are worth stating plainly. A final invoice
that absorbed a transfer ends up settled against a miscellaneous entry, so the
original payment never appears among its reconciled payments — for anyone
reviewing a folio in detail, that looks odd until it is explained. And when the
down payment is larger than the final invoice, the whole balance is still moved:
the customer of the final invoice is left with a credit in their favour, which
is real money they paid.
