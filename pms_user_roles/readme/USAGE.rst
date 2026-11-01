**Roles**

In *Settings > Users & Companies > Roles*, access rights administrators
configure the roles. Two options are added:

* *Assignable*: profile managers may assign the role. A role that grants
  *Settings*, *Access Rights* or *Profile manager* cannot be assignable.
* *All properties*: holders get every active property and the companies of
  those properties. The role must grant *Multi Companies*. What the option
  adds is removed when the user no longer holds any role with it; properties
  and companies the user had before are kept.

None of the catalog roles is assignable out of the box.

**Profile managers**

Give the *Profile manager* group to the people in charge of user profiles.
They get a *Roles > Role assignments* menu where they add, change and remove
role lines, with these limits:

* only roles flagged as assignable;
* only active internal users who are not administrators nor profile managers,
  and never themselves;
* a user whose permissions are still set by hand (no role yet) cannot be
  given a role that would take permissions away: an access rights
  administrator migrates those users.

Each operation is logged with the profile manager, the role and the user.

**Assigning roles replaces groups**

A user with at least one role line gets exactly the groups of their enabled
roles: groups set by hand are removed on the next recompute. Ticking the
group of a role in the *Access Rights* tab does not assign the role; roles are
assigned through role lines only.
