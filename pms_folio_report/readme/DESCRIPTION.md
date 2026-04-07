Adds a printable report to the **PMS Folio** model, accessible from the
**Print** menu on any folio list or form view:

- **Booking Report (Excel)**: 3-tab `.xlsx` file generated via `report_xlsx`:
  - *Summary* – one block per folio with a grey header row, internal comments
    and the list of its reservations.
  - *Reservations* – flat table with one row per folio.
  - *Room Detail* – flat table with one row per reservation; rows are
    colour-coded by folio for visual grouping.
