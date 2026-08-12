from rest_framework import status

from core.models import Product
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_product,
    create_user,
)
from core.tests.helpers import get_public_ids


class ProductBusinessIsolationTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.product_a = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto A",
        )

        cls.product_b = create_product(
            business=cls.business_b,
            status=cls.active_status,
            title="Producto B",
        )

    def test_user_only_lists_products_from_own_business(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/products/",
            owned_object=self.product_a,
            foreign_object=self.product_b,
        )

    def test_user_cannot_retrieve_product_from_other_business(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=(
                f"/api/products/"
                f"{self.product_b.public_id}/"
            )
        )

    def test_user_cannot_update_product_from_other_business(self):
        self.assert_cannot_update_foreign_object(
            endpoint=(
                f"/api/products/"
                f"{self.product_b.public_id}/"
            ),
            payload={
                "title": "Producto manipulado",
            },
        )

        self.product_b.refresh_from_db()

        self.assertEqual(
            self.product_b.title,
            "Producto B",
        )

    def test_user_cannot_delete_product_from_other_business(self):
        self.assert_cannot_delete_foreign_object(
            endpoint=(
                f"/api/products/"
                f"{self.product_b.public_id}/"
            )
        )

        self.assertTrue(
            Product.objects.filter(
                pk=self.product_b.pk,
            ).exists()
        )

    def test_user_cannot_create_product_in_other_business(self):
        response = self.client.post(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_b.public_id
                ),
                "title": "Producto infiltrado",
                "description": "",
                "image_url": "",
                "base_price": "50.00",
                "base_cost": "30.00",
                "stock": 5,
                "is_visible": True,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.assertFalse(
            Product.objects.filter(
                title="Producto infiltrado",
            ).exists()
        )

    def test_user_cannot_move_product_to_other_business(self):
        response = self.client.patch(
            (
                f"/api/products/"
                f"{self.product_a.public_id}/"
            ),
            {
                "business_public_id": str(
                    self.business_b.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.product_a.refresh_from_db()

        self.assertEqual(
            self.product_a.business_id,
            self.business_a.pk,
        )

    def test_superuser_can_list_products_from_requested_business(self):
        superuser = create_user(
            email="platform.admin@playnow.test",
            full_name="Administrador de plataforma",
            is_superuser=True,
        )

        self.authenticate_as(
            superuser
        )

        response = self.client.get(
            "/api/products/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        returned_ids = get_public_ids(
            response
        )

        self.assertIn(
            str(self.product_a.public_id),
            returned_ids,
        )

        self.assertNotIn(
            str(self.product_b.public_id),
            returned_ids,
        )
