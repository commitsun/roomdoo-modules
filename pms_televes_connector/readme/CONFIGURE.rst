#. Go to **PMS ‣ Properties** and open the property form.
#. Open the **Televes** tab and enable *Enable Televes Integration*.
#. Fill in the connection parameters:

   * **Televes URL**: base URL of the PMS Adapter API
     (e.g. ``http://atv3demo.arantia.com:8094``).
   * **Televes Base Path**: API base path
     (default ``/pms-adapter-backend-service``).
   * **Televes PMS User** and **Televes PMS Password**: credentials provided
     by Televes/Arantia.

#. Go to **PMS ‣ Rooms**, open each room form and set the
   **Televes Room Number** field to the integer room number configured in
   the ATV3 system (e.g. ``5000``).
   This field is only visible when the property has Televes enabled.
