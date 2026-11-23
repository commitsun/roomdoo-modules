Keeps the Roomdoo host registry of this instance current, in real time.

The instance tells host, as it happens, when hotels are created, changed or
deleted, when internal users change, when feature flags change and when the
versions of its main modules change after a deploy. Every hour it also sends
how it is used: number of hotels, last reservation, last price change and last
login (the technical user's own logins are not counted).

Events are queued with ``queue_job``, so a slow or absent host never blocks
anyone working in the PMS; they are retried while host is unreachable, carry a
unique id and are signed with a secret that belongs to this instance.

The module also provides the technical user Roomdoo operates the instance
with (it adopts it if it already exists), and the group that lets it switch
feature flags on behalf of host.
