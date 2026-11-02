The booking engine charges with the Redsys providers of the property:

* a provider with the Bizum payment method is offered as ``bizum``, and one
  with cards as ``card``;
* the gateway sends the guest back to the booking engine, not to the payment
  pages of Odoo;
* a payment already done is not undone by a later notification of the same
  order, as the Redsys module would otherwise write any state it receives.
