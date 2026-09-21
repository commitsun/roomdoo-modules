This module reports travellers and reservations to the Ertzaintza (Basque
Country police) through its "A19 Registro Hotelero" SOAP service, as an
alternative to the Spanish national SES service already supported by
``pms_l10n_es``. Establishments in the Basque Country must report to the
Ertzaintza, not to SES, so a property reports to one authority or the
other, never to both.

The data is the one of the RD 933/2021 that Roomdoo already collects at
check-in, so nothing changes in the check-in screens. What the module adds
is:

* a new ``ertzaintza`` value in the per-property institution selection,
  with its configuration (establishment code, lessor code, certificate,
  environment);
* a communication queue and audit log (``pms.ertzaintza.communication``)
  holding the XML, the signed request and the answer of every reservation
  (``RH``) and traveller report (``PV``);
* the same triggers as the SES integration: a reservation queues its
  report when it is created, and the traveller report becomes sendable
  once every guest of the reservation is on board;
* three scheduled actions, inactive by default, that send the pending
  communications;
* a "Download XML" action that produces the same document as a file, so
  that the establishment can upload it by hand in the Ertzaintza portal
  while a production certificate is not authorised yet.

Requests are signed with WS-Security (X.509 BinarySecurityToken over the
SOAP Body, the Timestamp and the token itself) using the certificate of
the establishment owner, the same one already uploaded for Verifactu or
the SII.
