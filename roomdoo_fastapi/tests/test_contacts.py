from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestRoomdooApi


class TestContactsEndpoints(CommonTestRoomdooApi):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Create a partner with a unique address
        cls.test_partner = cls.env["res.partner"].create(
            {
                "firstname": "john",
                "lastname": "doe",
                "street": "123 Main St",
                "city": "Anytown",
                "zip": "12345",
                "country_id": cls.env.ref("base.us").id,
            }
        )

    def test_create_partner_with_residence_address(self):
        """Test creating a contact with a separate residence address."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            create_data = {
                "firstname": "jane",
                "lastname": "smith",
                "street": "123 Main St",
                "city": "Anytown",
                "zip": "12345",
                "country": self.env.ref("base.us").id,
                "residenceStreet": "456 Elm St",
                "residenceCity": "Othertown",
                "residenceZip": "67890",
                "residenceCountry": self.env.ref("base.us").id,
                "contactType": "person",
            }
            response = test_client.post(
                "/contacts",
                json=create_data,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            contact_id = response.json().get("id")

            self.env.invalidate_all()
            created_partner = self.env["res.partner"].browse(contact_id)
            residence_partner = created_partner.residence_partner_id
            self.assertNotEqual(residence_partner.id, created_partner.id)
            self.assertEqual(residence_partner.street, "456 Elm St")
            self.assertEqual(residence_partner.city, "Othertown")
            self.assertEqual(residence_partner.zip, "67890")
            self.assertEqual(created_partner.street, "123 Main St")
            self.assertEqual(created_partner.city, "Anytown")
            self.assertEqual(created_partner.zip, "12345")

    def test_create_partner_same_address(self):
        """Test creating a contact with the same unique and residence address."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            create_data = {
                "firstname": "alice",
                "lastname": "johnson",
                "street": "789 Oak St",
                "city": "Sometown",
                "zip": "54321",
                "country": self.env.ref("base.us").id,
                "residenceStreet": "789 Oak St",
                "residenceCity": "Sometown",
                "residenceZip": "54321",
                "residenceCountry": self.env.ref("base.us").id,
                "contactType": "person",
            }
            response = test_client.post(
                "/contacts",
                json=create_data,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            contact_id = response.json().get("id")

            self.env.invalidate_all()
            created_partner = self.env["res.partner"].browse(contact_id)
            residence_partner = created_partner.residence_partner_id
            self.assertEqual(residence_partner.id, created_partner.id)
            self.assertEqual(residence_partner.street, "789 Oak St")
            self.assertEqual(residence_partner.city, "Sometown")
            self.assertEqual(residence_partner.zip, "54321")

    def test_update_partner_same_address(self):
        """Test updating a contact's unique address."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            contact_id = self.test_partner.id
            update_data = {
                "street": "456 Elm St",
                "city": "Othertown",
                "zip": "67890",
                "country": self.env.ref("base.us").id,
                "residenceStreet": "456 Elm St",
                "residenceCity": "Othertown",
                "residenceZip": "67890",
                "residenceCountry": self.env.ref("base.us").id,
            }
            response = test_client.patch(
                f"/contacts/{contact_id}",
                json=update_data,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            self.env.invalidate_all()
            updated_partner = self.env["res.partner"].browse(contact_id)
            residence_partner = updated_partner.residence_partner_id
            self.assertEqual(residence_partner.id, updated_partner.id)
            self.assertEqual(residence_partner.street, "456 Elm St")
            self.assertEqual(residence_partner.city, "Othertown")
            self.assertEqual(residence_partner.zip, "67890")

    def test_partner_residence_address(self):
        """Test updating a contact's residence address."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            contact_id = self.test_partner.id
            update_data = {
                "residenceStreet": "456 Elm St",
                "residenceCity": "Othertown",
                "residenceZip": "67890",
                "residenceCountry": self.env.ref("base.us").id,
            }
            response = test_client.patch(
                f"/contacts/{contact_id}",
                json=update_data,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            self.env.invalidate_all()
            # Verify that a new residence address partner was created
            updated_partner = self.env["res.partner"].browse(contact_id)
            residence_partner = updated_partner.residence_partner_id
            self.assertNotEqual(residence_partner.id, updated_partner.id)
            self.assertEqual(residence_partner.street, "456 Elm St")
            self.assertEqual(residence_partner.city, "Othertown")
            self.assertEqual(residence_partner.zip, "67890")

    def test_contact_count(self):
        """Test the contact count endpoint."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/contacts-count")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            count_from_endpoint = response.json()
            self.assertIsInstance(count_from_endpoint, int)

            # Call contacts endpoint to get actual count
            response = test_client.get("/contacts")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            contacts_count = response.json().get("count")
            self.assertEqual(count_from_endpoint, contacts_count)

    def test_customer_count(self):
        """Test the customer count endpoint."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/customers-count")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            count_from_endpoint = response.json()
            self.assertIsInstance(count_from_endpoint, int)

            # Call customers endpoint to get actual count
            response = test_client.get("/customers")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            customers_count = response.json().get("count")
            self.assertEqual(count_from_endpoint, customers_count)

    def test_supplier_count(self):
        """Test the supplier count endpoint."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/suppliers-count")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            count_from_endpoint = response.json()
            self.assertIsInstance(count_from_endpoint, int)

            # Call suppliers endpoint to get actual count
            response = test_client.get("/suppliers")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            suppliers_count = response.json().get("count")
            self.assertEqual(count_from_endpoint, suppliers_count)

    def test_guests_count(self):
        """Test the guests count endpoint."""
        with self._create_test_client() as test_client:
            response = self._login(test_client)
            response = test_client.get("/guests-count")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            count_from_endpoint = response.json()
            self.assertIsInstance(count_from_endpoint, int)

            # Call guests endpoint to get actual count
            response = test_client.get("/guests")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            guests_count = response.json().get("count")
            self.assertEqual(count_from_endpoint, guests_count)
