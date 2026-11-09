Shows in Roomdoo the payment cards Wubook keeps for the bookings it imports,
without storing them.

* When a booking arrives with a card, the folio records that Wubook keeps one,
  where it came from and, for Booking.com virtual cards, when it can be
  charged and its balance.
* When someone with the card password asks to see it, the card is read from
  Wubook with ``fetch_ccard`` at that moment. Neither the call nor its answer
  are logged, and the card is not kept: only its brand, last digits and
  holder, to show them in the summary.
* Wubook deletes the card a week after the check-out, and allows 40 card
  requests an hour.
