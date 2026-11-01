Builds the management of PMS users on top of the user roles of
``base_user_role``:

* A catalog of roles for the usual PMS profiles: base internal user,
  reception, revenue, administrative, manager and call center.
* Profile managers: users who assign the roles flagged as *assignable* to
  internal users without being access rights administrators.
* An *All properties* option on roles, for teams that work across every
  property (support, for instance): holders get every active property and its
  company, and lose them again when they no longer hold the role.
* Safety nets around the role recompute of ``base_user_role``:

  * a user whose roles grant no user type stays an internal user instead of
    silently becoming a share user without groups;
  * a role line created on its own (from the role assignments menu, data files
    or RPC) is applied right away;
  * a daily job reports users that hold the group of a role without a role
    line, which the recompute never updates.
