Lets reception keep the payment card of a folio and see it when it has to be
charged, behind a card password per property.

* Cards added in Roomdoo are sealed with a key pair of the property. Its
  private key is encrypted with the card password, so neither the database
  nor the server can open the cards on their own.
* Cards a channel manager keeps are never stored: they are fetched from it
  every time someone asks to see them. A bridge module per channel manager
  provides the fetch.
* Every attempt to see, add, edit or delete a card is logged, and three wrong
  passwords in a row lock the folio for five minutes.

The REST endpoints live under ``/api/folios/<id>/payment-card``.
