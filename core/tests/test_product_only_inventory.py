import importlib
from unittest.mock import patch

from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import (
    BusinessMembership,
    Product,
    StockMovement,
    TransactionDetail,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_payment_method,
    create_product,
    create_role_user,
)
from core.services.inventory import record_stock_movement


class ProductOnlyInventoryTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cashier, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        _, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.payment_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )

    def setUp(self):
        self.product = create_product(
            business=self.business_a,
            status=self.active_status,
            title="Camiseta negra / M",
            stock=10,
        )
        self.authenticate_as(self.cashier)

    def _sale_payload(self, product=None, quantity=3):
        return {
            "business_public_id": str(self.business_a.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(self.seller_employee.public_id),
            "payment_method_public_id": str(self.payment_method.public_id),
            "type": "sale",
            "details": [{
                "product_public_id": str(
                    (product or self.product).public_id
                ),
                "quantity": quantity,
            }],
        }

    def test_individual_product_is_sold_and_stock_changes_once(self):
        response = self.client.post(
            "/api/transactions/",
            self._sale_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 7)
        self.assertEqual(
            StockMovement.objects.filter(
                transaction__public_id=response.data["public_id"],
            ).count(),
            1,
        )
        detail = response.data["details"][0]
        self.assertNotIn("variant_public_id", detail)
        self.assertNotIn("variant_type_public_id", detail)

    def test_sale_rejects_product_from_another_business(self):
        foreign_product = create_product(
            business=self.business_b,
            status=self.active_status,
            stock=10,
        )

        response = self.client.post(
            "/api/transactions/",
            self._sale_payload(product=foreign_product),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        foreign_product.refresh_from_db()
        self.assertEqual(foreign_product.stock, 10)

    def test_positive_and_negative_adjustments_update_product(self):
        record_stock_movement(
            product=self.product,
            created_by=self.user_a,
            movement_type="adjustment",
            quantity=5,
        )
        record_stock_movement(
            product=self.product,
            created_by=self.user_a,
            movement_type="adjustment",
            quantity=-2,
        )

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 13)

    def test_inventory_models_reference_only_product(self):
        transaction_detail_fields = {
            field.name for field in TransactionDetail._meta.get_fields()
        }
        stock_movement_fields = {
            field.name for field in StockMovement._meta.get_fields()
        }

        self.assertIn("product", transaction_detail_fields)
        self.assertIn("product", stock_movement_fields)
        self.assertNotIn("variant", transaction_detail_fields)
        self.assertNotIn("variant", stock_movement_fields)

    def test_product_model_is_the_only_inventory_item_model(self):
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 10)
        self.assertEqual(self.product.title, "Camiseta negra / M")

    def test_product_stock_cannot_be_patched_directly(self):
        response = self.client.patch(
            f"/api/products/{self.product.public_id}/",
            {"stock": 999},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(
            StockMovement.objects.filter(product=self.product).exists()
        )

    def test_movement_failure_rolls_back_stock_change(self):
        with patch(
            "core.services.inventory.StockMovement.objects.create",
            side_effect=RuntimeError("movement insert failed"),
        ):
            with self.assertRaisesMessage(
                RuntimeError,
                "movement insert failed",
            ):
                record_stock_movement(
                    product=self.product,
                    created_by=self.user_a,
                    movement_type="adjustment",
                    quantity=5,
                )

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)


class ProductOnlyInventorySchemaTests(BusinessIsolationTestCase):
    def test_openapi_has_no_variant_paths_schemas_or_fields(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)

        self.assertFalse(
            any("variant" in path.casefold() for path in schema["paths"])
        )
        self.assertFalse(
            any(
                "variant" in name.casefold()
                for name in schema["components"]["schemas"]
            )
        )

        for schema_name in (
            "TransactionDetail",
            "TransactionDetailRequest",
            "StockMovement",
        ):
            properties = schema["components"]["schemas"][schema_name][
                "properties"
            ]
            self.assertNotIn("variant_public_id", properties)
            self.assertNotIn("variant_type_public_id", properties)


class VariantRemovalMigrationTests(SimpleTestCase):
    migration = importlib.import_module(
        "core.migrations.0002_remove_productvariant_status_and_more"
    )

    def test_zero_product_stock_can_be_consolidated(self):
        self.assertEqual(
            self.migration.resolve_product_stock(
                product_id=1,
                product_stock=0,
                variant_stock=12,
            ),
            12,
        )

    def test_matching_nonzero_stocks_are_still_ambiguous(self):
        with self.assertRaisesMessage(RuntimeError, "stock propio no nulo"):
            self.migration.resolve_product_stock(
                product_id=1,
                product_stock=12,
                variant_stock=12,
            )

    def test_conflicting_product_and_variant_stock_stops_migration(self):
        with self.assertRaisesMessage(
            RuntimeError,
            "No es posible determinar",
        ):
            self.migration.resolve_product_stock(
                product_id=1,
                product_stock=4,
                variant_stock=12,
            )

    def test_consolidation_does_not_touch_products_without_variants(self):
        class Query:
            def filter(self, **kwargs):
                return self

            def exclude(self, **kwargs):
                return self

            def count(self):
                return 0

        class VariantRows:
            def values(self, *fields):
                return self

            def annotate(self, **kwargs):
                return self

            def iterator(self):
                return iter([{
                    "variant_type__product_id": 1,
                    "total_stock": 5,
                }])

        class ProductRecord:
            def __init__(self, pk, stock):
                self.pk = pk
                self.stock = stock

        products = {
            1: ProductRecord(1, 0),
            2: ProductRecord(2, 9),
        }

        class ProductManager:
            selected_pk = None

            def get(self, pk):
                return products[pk]

            def filter(self, pk):
                self.selected_pk = pk
                return self

            def update(self, **values):
                products[self.selected_pk].stock = values["stock"]

        class FakeApps:
            models = {
                "Product": type(
                    "Product",
                    (),
                    {"objects": ProductManager()},
                ),
                "ProductVariant": type(
                    "ProductVariant",
                    (),
                    {"objects": VariantRows()},
                ),
                "StockMovement": type(
                    "StockMovement",
                    (),
                    {"objects": Query()},
                ),
                "TransactionDetail": type(
                    "TransactionDetail",
                    (),
                    {"objects": Query()},
                ),
            }

            def get_model(self, app_label, model_name):
                return self.models[model_name]

        self.migration.consolidate_variant_inventory(
            FakeApps(),
            schema_editor=None,
        )

        self.assertEqual(products[1].stock, 5)
        self.assertEqual(products[2].stock, 9)
