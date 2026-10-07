The module is the Basque sibling of the SES integration of ``pms_l10n_es``:
same switch, same triggers, same shape of data. It deliberately does not
reuse ``pms.ses.communication``, because the SES cron searches its queue by
state and entity only and would happily post Basque reports to the Ministry.

Layout
~~~~~~~~

``models/ertzaintza_codes.py``
    Endpoints, namespaces, code tables (document type, gender, payment,
    relationship) and the classification of the service error codes. No Odoo
    import: it is the one place to look when the Ertzaintza publishes a new
    catalogue.

``models/ertzaintza_xml_builder.py``
    Builds the ``alt:peticion`` document and validates it against the
    bundled XSD. Plain functions over recordsets, no database writes, so the
    mapping can be unit tested on its own.

``models/ertzaintza_wsse.py``
    The WS-Security signature (X.509 BinarySecurityToken over the Body, the
    Timestamp and the token itself). Modelled on the FACe signer of
    ``l10n_es_facturae_face``.

``models/ertzaintza_client.py``
    Envelope, transport and reading of the answer. There is no WSDL client:
    the WSDL shipped in the SoapUI project is older than the manual and the
    namespace of the answer is not stable between environments, so the
    envelope is built with lxml and the answer is read by local element
    names.

``models/pms_ertzaintza_communication.py``
    The queue and the audit log: one record per ``comunicacion``, its state
    machine, the scheduled actions and the user actions.

``models/pms_reservation.py`` / ``models/pms_checkin_partner.py``
    The triggers, mirroring the SES ones one to one.

Where it departs from the SES integration, and why
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

*The service is different.* The A19 web service is synchronous, has no
cancellation and no modification operation, and answers "the contract
already exists" when the same reference is sent twice. So there is no
``operation`` field that could only ever hold "A", no batch identifier and
no query-status round trip; instead, a duplicate answer is treated as
success, which is what makes a retry after a timeout safe.

*Missing data is listed, not raised.* The SES builder raises on the first
field it misses and the cron turns that into a Python traceback in a text
field. Here the builder collects every problem, the communication waits in
"incomplete" with the list on it, and the same list is posted in the
reservation chatter when the service rejects the data. A receptionist can
act on it without reading a traceback.

*Retries have an end and a way back.* A communication is retried while it
has fewer than five attempts; "Force send" resets the counter. The SES queue
stops at three attempts while staying in "pending", which looks like a queue
that is still working when it is not.

*The transport is separate from the mapping.* Building the XML, signing it
and posting it are three modules, which is what makes it possible to test
the mapping against the official XSD and the signature against a throwaway
certificate without touching the network.

*Communications are property scoped.* A global record rule restricts them to
the properties of the user, like the reservations they report.

Testing
~~~~~~~~~

``tests/test_real_world_profiles.py`` reproduces the shape of the check-in
data of a real Basque establishment (documented in its docstring) with
invented people. ``tests/test_xml_builder.py`` validates every generated
document against the published XSD, ``tests/test_wsse.py`` checks the three
signed references and verifies the signature, and
``tests/test_communication_flow.py`` drives the state machine with the
answers the service really returns, captured from pre-production.
