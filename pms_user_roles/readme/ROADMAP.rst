* The catalog is a stable set of names, not a mapping of today's permissions.
  ``pms`` defines three groups (user, manager and call center), so the
  reception, revenue and administrative roles are identical inside the PMS:
  they only differ by the groups of other applications that each installation
  adds. Telling them apart needs new groups and access rights in ``pms``.
* Assigning a catalog role to an existing user replaces all their groups. A
  PMS manager given *PMS / Reception* loses the manager rights. Review the
  groups of each user before moving them to roles.
* ``res.users.pms_api_user_role``, used by the front end, is not derived from
  the roles yet.
* With ``base_user_role_company`` the recompute runs on every login and
  session information request; the safety nets are no-ops for users that need
  no change, but this combination is not covered by tests.
