import json
from functools import partial

from fastapi import status
from requests import Response

from odoo.addons.fastapi.dependencies import fastapi_endpoint
from odoo.addons.fastapi.tests.common import FastAPITransactionCase


class CommonTestRoomdooApi(FastAPITransactionCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        jwt_validator = cls.env["auth.jwt.validator"].search([("name", "=", "api_pms")])
        jwt_validator.cookie_secure = False
        cls.pms_fastapi_app = cls.env.ref("pms_fastapi.pms_fastapi_endpoint")
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.default_fastapi_app = cls.pms_fastapi_app._get_app()
        cls.default_fastapi_dependency_overrides = {
            fastapi_endpoint: partial(lambda a: a, cls.pms_fastapi_app)
        }
        cls.default_fastapi_odoo_env = cls.env
        cls.default_fastapi_running_user = cls.pms_fastapi_app.user_id
        cls.test_user = cls.env["res.users"].create(
            {
                "name": "PMS api test",
                "login": "test_pms_api",
                "password": "supersecret",
                "email": "test@example.org",
            }
        )
        cls.test_availability_plan = cls.env["pms.availability.plan"].create(
            {"name": "Availability Plan 1"}
        )
        cls.test_pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Pricelist 1",
                "availability_plan_id": cls.test_availability_plan.id,
            }
        )
        cls.test_company = cls.env["res.company"].create(
            {
                "name": "Company 1",
            }
        )
        cls.test_property = cls.env["pms.property"].create(
            {
                "name": "Property 1",
                "company_id": cls.test_company.id,
                "default_pricelist_id": cls.test_pricelist.id,
                "user_ids": [(6, 0, [cls.test_user.id])],
            }
        )

    def _login(self, test_client, password="supersecret"):
        response: Response = test_client.post(
            "/login",
            content=json.dumps(
                {
                    "username": "test_pms_api",
                    "password": password,
                }
            ),
        )
        self.assertEqual(
            response.status_code, status.HTTP_204_NO_CONTENT, response.text
        )
        return response
