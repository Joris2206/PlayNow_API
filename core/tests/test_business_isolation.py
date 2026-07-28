from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Business, EntityStatus, Product, User


class ProductBusinessIsolationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_status = EntityStatus.objects.create(
            name="Activo"
        )

        cls.user_a = User.objects.create_user(
            email="usuario.a@playnow.test",
            full_name="Usuario A",
            password="TestPassword123!",
            role="admin",
        )

        cls.user_b = User.objects.create_user(
            email="usuario.b@playnow.test",
            full_name="Usuario B",
            password="TestPassword123!",
            role="admin",
        )

        cls.business_a = Business.objects.create(
            user=cls.user_a,
            business_name="Negocio A",
            description="Negocio del usuario A",
            currency="NIO",
            status=cls.active_status,
        )

        cls.business_b = Business.objects.create(
            user=cls.user_b,
            business_name="Negocio B",
            description="Negocio del usuario B",
            currency="NIO",
            status=cls.active_status,
        )

        cls.product_a = Product.objects.create(
            business=cls.business_a,
            title="Producto A",
            description="Producto del negocio A",
            base_price="100.00",
            base_cost="60.00",
            stock=10,
            is_visible=True,
            status=cls.active_status,
        )

        cls.product_b = Product.objects.create(
            business=cls.business_b,
            title="Producto B",
            description="Producto del negocio B",
            base_price="200.00",
            base_cost="120.00",
            stock=20,
            is_visible=True,
            status=cls.active_status,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user_a)

    def test_user_only_lists_products_from_own_business(self):
        response = self.client.get("/api/products/")

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        response_text = str(response.data)

        self.assertIn(
            str(self.product_a.public_id),
            response_text,
        )

        self.assertNotIn(
            str(self.product_b.public_id),
            response_text,
        )

    def test_user_cannot_retrieve_product_from_other_business(self):
        response = self.client.get(
            f"/api/products/{self.product_b.public_id}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )