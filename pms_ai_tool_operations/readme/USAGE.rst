Every tool is declared in *AI > AI Tool* and works on the properties of the
user that owns the MCP key. Dates default to the timezone of the user.

* ``pms_daily_movements``: ``date`` (today) and ``property_ids``.
* ``pms_occupancy_outlook``: ``date_from`` (today), ``days`` (14, up to 31) and
  ``property_ids``.
* ``pms_board_services_forecast``: ``date_from`` (tomorrow), ``date_to`` (the
  same day, up to 14 days in all) and ``property_ids``.
* ``pms_guest_data_pending``: ``date_from`` (today), ``days`` (3, up to 7) and
  ``property_ids``.
* ``pms_folio_reception_note``: ``folio`` (code or id) and ``note``. It is a
  write tool: set the users allowed to run it and tick *Write enabled* in the
  tool, as well as the system parameter ``pms_ai_tool.write_enabled``. Call it
  with ``confirm=false`` to get the preview and the token, and again with
  ``confirm=true`` and the token to post the note.

Pre-reservations (not confirmed) and cancelled reservations are never counted;
out of service blocks are only counted as blocked rooms.
