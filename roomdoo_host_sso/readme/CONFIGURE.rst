Nothing to set up beyond ``roomdoo_host_link``: the address of host and the
code of this instance come from its parameters, and the ``roomdoo_host``
token validator follows them.

What each profile may do is in *Settings > Users & Companies > Host access
profiles*. To begin with there is one, *Full access* (code ``full``), the
one host sends for everybody.

The parameter ``roomdoo_host_sso.adoptable_domains`` (comma separated e-mail
domains, ``roomdoo.com`` when unset) says whose portal users host may take
over for an access. Leave it empty to never take one over.
