from fastapi import status

from odoo.addons.pms_fastapi.tests.common import CommonTestPmsApi


class TestSearchTextGuard(CommonTestPmsApi):
    """Free-text search length guard (PmsApiRouter + SearchText marker).

    These exercise the behaviour the module adds on top of FastAPI: marked
    free-text params shorter than the minimum are rejected with an RFC 9457
    problem+json, while empty/long values and unmarked params pass through.
    """

    def _assert_too_short(self, response, field):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.text
        )
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        body = response.json()
        self.assertEqual(body["type"], "/errors/search-text-too-short")
        self.assertEqual(body["status"], 400)
        self.assertEqual(body["field"], field)
        self.assertEqual(body["minLength"], 3)

    def test_short_text_is_rejected(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies", params={"name": "ab"})
            self._assert_too_short(response, "name")

    def test_short_global_search_is_rejected(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies", params={"globalSearch": "ab"})
            self._assert_too_short(response, "globalSearch")

    def test_long_enough_text_passes(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies", params={"name": "abc"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_empty_text_is_no_filter(self):
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies", params={"name": ""})
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_unmarked_param_is_not_guarded(self):
        # `countries` is a list filter, not marked as free-text search.
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/agencies", params={"countries": "ES"})
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)

    def test_guard_covers_annotated_style_schema(self):
        # FolioSearch marks fields with Annotated[SearchText, Query(...)].
        with self._create_test_client() as test_client:
            self._login(test_client)
            response = test_client.get("/folios", params={"room": "ab"})
            self._assert_too_short(response, "room")
