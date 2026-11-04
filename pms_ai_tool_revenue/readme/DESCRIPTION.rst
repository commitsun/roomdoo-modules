Revenue tools for the PMS AI server (``pms_ai_tool``), served over MCP. All of
them are scoped to the properties of the user and return aggregated figures
(no guest data) with the definitions used in every answer:

* ``pms_revenue_kpis``: room nights sold and available, occupancy, room revenue
  without VAT, ADR and RevPAR per property, compared with the same dates last
  year.
* ``pms_pickup``: room nights and revenue of the reservations created in a
  booking window, by stay month and property, with the cancellations of the
  window and the net pickup.
* ``pms_pace_vs_last_year``: what is on the books now for some stay dates
  against last year's same dates at the same lead time.
* ``pms_channel_mix``: share of room nights and revenue by sale channel or by
  agency, compared with last year.
* ``pms_partner_assign_salesperson`` (changes data, two-step confirmation):
  assigns the salesperson of customer companies and agencies.
