On each property that must report to the Ertzaintza instead of the SES:

#. Go to *PMS > Configuration > Properties* and open the property.
#. In the "Guest information sending settings" section, set *Institution*
   to *Ertzaintza (A19 Registro Hotelero)*.
#. Set the *Lessor code*: the CIF of the establishment owner, as assigned
   by the Ertzaintza (10 characters maximum).
#. Set the *Establishment code*: the code assigned to the establishment
   by the Ertzaintza (10 characters maximum).
#. Upload the owner's signing certificate (``.pfx``) under *Accounting >
   Configuration > AEAT > Certificates* and press *Obtain keys*, or
   select an existing certificate in the *Ertzaintza Certificate* field
   of the property. When left empty, the active AEAT certificate of the
   company is used (the same one used for Verifactu/SII).

   The web service authenticates by certificate only: the user and
   password of the establishment work in the web portal, not here. It has
   to be a certificate issued by one of the authorities the Ertzaintza
   accepts (Izenpe, FNMT, Camerfirma...) and its tax identifier has to be
   either the owner's or that of a third party the owner has registered as
   an authorised user. Using the owner's own certificate, the one already
   loaded for the tax agency, avoids that registration altogether.
#. Choose the *Ertzaintza Environment*: *Pre-production (tests)* while
   testing, *Production* once the Ertzaintza has validated the
   integration.
#. In pre-production only, untick *Verify TLS certificate*: its chain is
   an internal Ertzaintza CA that is not publicly trusted. Production
   always verifies the certificate regardless of this setting.

The property shows a warning listing anything still missing before it is
ready to send reports to the Ertzaintza.
