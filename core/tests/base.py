from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.factories import create_business, create_status, create_user
from core.tests.helpers import get_public_ids


class BusinessIsolationTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.void_status = create_status("Anulado")

        cls.user_a = create_user(email="owner.a@playnow.test", full_name="Propietario A")
        cls.user_b = create_user(email="owner.b@playnow.test", full_name="Propietario B")

        cls.business_a = create_business(
            user=cls.user_a,
            status=cls.active_status,
            business_name="Negocio A",
        )
        cls.business_b = create_business(
            user=cls.user_b,
            status=cls.active_status,
            business_name="Negocio B",
        )

    def setUp(self):
        self.authenticate_as(self.user_a)

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def assert_list_contains_only_owned_object(self, *, endpoint, owned_object, foreign_object):
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = get_public_ids(response)
        self.assertIn(str(owned_object.public_id), returned_ids)
        self.assertNotIn(str(foreign_object.public_id), returned_ids)

    def assert_cannot_retrieve_foreign_object(self, *, endpoint):
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def assert_cannot_update_foreign_object(self, *, endpoint, payload):
        response = self.client.patch(endpoint, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def assert_cannot_delete_foreign_object(self, *, endpoint):
        response = self.client.delete(endpoint)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def assert_method_not_allowed(self, *, method, endpoint, payload=None):
        request_method = getattr(self.client, method.lower())
        response = request_method(endpoint, payload or {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
