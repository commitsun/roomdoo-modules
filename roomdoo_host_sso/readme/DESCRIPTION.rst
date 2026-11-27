Lets the Roomdoo team into this instance from host, without passwords.

From the card of an instance in host, a person of the team enters the back
office or the app. Host signs a token that lives a minute and opens one door
of one instance, once. This instance checks it against the public keys host
publishes and gives the person a user of their own: created the first time,
reactivated the next ones, with the groups of the profile host sends and all
the hotels and companies. When the access ends, a cron archives the user,
which logs the person out of both the back office and the app.

The users host creates never log in any other way: not with a password, not
with an API key and not with a sign-up link. An existing internal user of
the instance with the same login is never taken over.

A person of the team may already have a portal user of their own here, with
their team login (to use the app as a hotel does). Host takes that user over
for the access instead of refusing it: it notes down what the portal user
has, makes it the internal user of the access and, when the access ends,
gives it back as it was, with its hotels, companies and its own password.
While the access lasts, it only comes in through host. Only portal users
whose login is in the domains of ``roomdoo_host_sso.adoptable_domains`` are
taken over. Their entries do not count
as logins of the instance, which measure the customer's use of it: host keeps
its own record of who came in and until when.
