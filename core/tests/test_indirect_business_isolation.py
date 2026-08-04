from rest_framework import status

from core.models import (
    ProductVariant,
    ProductVariantType,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_product,
    create_variant,
    create_variant_type,
)


class ProductVariantTypeIsolationTests(
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

        cls.variant_type_a = create_variant_type(
            product=cls.product_a,
            status=cls.active_status,
            name="Talla",
        )

        cls.variant_type_b = create_variant_type(
            product=cls.product_b,
            status=cls.active_status,
            name="Color",
        )

    def test_user_only_lists_own_variant_types(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/variant-types/",
            owned_object=self.variant_type_a,
            foreign_object=self.variant_type_b,
        )

    def test_user_cannot_retrieve_foreign_variant_type(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=(
                "/api/variant-types/"
                f"{self.variant_type_b.public_id}/"
            )
        )

    def test_user_cannot_update_foreign_variant_type(self):
        self.assert_cannot_update_foreign_object(
            endpoint=(
                "/api/variant-types/"
                f"{self.variant_type_b.public_id}/"
            ),
            payload={
                "name": "Tipo alterado",
            },
        )

        self.variant_type_b.refresh_from_db()

        self.assertEqual(
            self.variant_type_b.name,
            "Color",
        )

    def test_user_cannot_delete_foreign_variant_type(self):
        self.assert_cannot_delete_foreign_object(
            endpoint=(
                "/api/variant-types/"
                f"{self.variant_type_b.public_id}/"
            )
        )

        self.assertTrue(
            ProductVariantType.objects.filter(
                pk=self.variant_type_b.pk
            ).exists()
        )

    def test_user_cannot_create_type_for_foreign_product(self):
        response = self.client.post(
            "/api/variant-types/",
            {
                "product": str(
                    self.product_b.public_id
                ),
                "name": "Material",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.assertFalse(
            ProductVariantType.objects.filter(
                product=self.product_b,
                name="Material",
            ).exists()
        )

    def test_user_cannot_move_variant_type_to_foreign_product(self):
        response = self.client.patch(
            (
                "/api/variant-types/"
                f"{self.variant_type_a.public_id}/"
            ),
            {
                "product": str(
                    self.product_b.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.variant_type_a.refresh_from_db()

        self.assertEqual(
            self.variant_type_a.product_id,
            self.product_a.pk,
        )


class ProductVariantIsolationTests(
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

        cls.variant_type_a = create_variant_type(
            product=cls.product_a,
            status=cls.active_status,
            name="Talla",
        )

        cls.variant_type_b = create_variant_type(
            product=cls.product_b,
            status=cls.active_status,
            name="Color",
        )

        cls.variant_a = create_variant(
            variant_type=cls.variant_type_a,
            status=cls.active_status,
            label="Grande",
        )

        cls.variant_b = create_variant(
            variant_type=cls.variant_type_b,
            status=cls.active_status,
            label="Rojo",
        )

    def test_user_only_lists_own_variants(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/variants/",
            owned_object=self.variant_a,
            foreign_object=self.variant_b,
        )

    def test_user_cannot_retrieve_foreign_variant(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=(
                "/api/variants/"
                f"{self.variant_b.public_id}/"
            )
        )

    def test_user_cannot_update_foreign_variant(self):
        self.assert_cannot_update_foreign_object(
            endpoint=(
                "/api/variants/"
                f"{self.variant_b.public_id}/"
            ),
            payload={
                "label": "Manipulado",
            },
        )

        self.variant_b.refresh_from_db()

        self.assertEqual(
            self.variant_b.label,
            "Rojo",
        )

    def test_user_cannot_delete_foreign_variant(self):
        self.assert_cannot_delete_foreign_object(
            endpoint=(
                "/api/variants/"
                f"{self.variant_b.public_id}/"
            )
        )

        self.assertTrue(
            ProductVariant.objects.filter(
                pk=self.variant_b.pk
            ).exists()
        )

    def test_user_cannot_create_variant_for_foreign_type(self):
        response = self.client.post(
            "/api/variants/",
            {
                "variant_type": str(
                    self.variant_type_b.public_id
                ),
                "label": "Azul",
                "additional_price": "10.00",
                "stock": 5,
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.assertFalse(
            ProductVariant.objects.filter(
                variant_type=self.variant_type_b,
                label="Azul",
            ).exists()
        )

    def test_user_cannot_move_variant_to_foreign_type(self):
        response = self.client.patch(
            (
                "/api/variants/"
                f"{self.variant_a.public_id}/"
            ),
            {
                "variant_type": str(
                    self.variant_type_b.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.variant_a.refresh_from_db()

        self.assertEqual(
            self.variant_a.variant_type_id,
            self.variant_type_a.pk,
        )