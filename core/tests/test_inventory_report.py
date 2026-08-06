from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

from rest_framework import status

from core.models import (
    BusinessMembership,
    StockMovement,
)
from core.tests.base import (
    BusinessIsolationTestCase,
)
from core.tests.factories import (
    create_product,
    create_role_user,
    create_stock_movement,
    create_variant,
    create_variant_type,
)


class InventoryReportTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        (
            cls.admin_user,
            cls.admin_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=(
                BusinessMembership
                .ROLE_ADMIN
            ),
            status=cls.active_status,
        )

        (
            cls.inventory_user,
            cls.inventory_employee,
            _,
        ) = create_role_user(
            business=cls.business_a,
            role=(
                BusinessMembership
                .ROLE_INVENTORY
            ),
            status=cls.active_status,
        )

        cls.product_without_variants = (
            create_product(
                business=cls.business_a,
                status=cls.active_status,
                title="Producto simple",
                stock=17,
            )
        )

        cls.product_with_variants = (
            create_product(
                business=cls.business_a,
                status=cls.active_status,
                title="Camisa",
                stock=0,
            )
        )

        cls.variant_type = (
            create_variant_type(
                product=(
                    cls.product_with_variants
                ),
                status=cls.active_status,
                name="Talla",
            )
        )

        cls.variant_small = create_variant(
            variant_type=cls.variant_type,
            status=cls.active_status,
            label="S",
            stock=8,
        )

        cls.variant_large = create_variant(
            variant_type=cls.variant_type,
            status=cls.active_status,
            label="L",
            stock=4,
        )

        cls.foreign_product = create_product(
            business=cls.business_b,
            status=cls.active_status,
            title="Producto extranjero",
            stock=500,
        )

    def setUp(self):
        self.authenticate_as(
            self.admin_user
        )

    def _get_summary(
        self,
        *,
        business=None,
        product=None,
        variant=None,
        date_from="2026-08-01",
        date_to="2026-08-31",
    ):
        business = (
            business
            or self.business_a
        )

        params = {
            "business_public_id": str(
                business.public_id
            ),
            "date_from": date_from,
            "date_to": date_to,
        }

        if product is not None:
            params["product_public_id"] = str(
                product.public_id
            )

        if variant is not None:
            params["variant_public_id"] = str(
                variant.public_id
            )

        return self.client.get(
            "/api/reports/inventory-summary/",
            params,
        )

    def test_simple_product_reconstructs_period_stock(
        self,
    ):
        self.product_without_variants.stock = 10
        self.product_without_variants.save(
            update_fields=["stock"]
        )

        # Stock inicial: 10
        # Entrada: +10
        # Venta: -4
        # Ajuste positivo: +2
        # Ajuste negativo: -1
        # Movimiento neto: +7
        # Stock actual y cierre: 17

        create_stock_movement(
            product=self.product_without_variants,
            created_by=self.inventory_user,
            movement_type="entry",
            quantity=10,
            created_at=datetime(
                2026,
                8,
                5,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_stock_movement(
            product=self.product_without_variants,
            created_by=self.inventory_user,
            movement_type="sale",
            quantity=-4,
            created_at=datetime(
                2026,
                8,
                10,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_stock_movement(
            product=self.product_without_variants,
            created_by=self.inventory_user,
            movement_type="adjustment",
            quantity=2,
            created_at=datetime(
                2026,
                8,
                15,
                12,
                tzinfo=timezone.utc,
            ),
        )

        create_stock_movement(
            product=self.product_without_variants,
            created_by=self.inventory_user,
            movement_type="adjustment",
            quantity=-1,
            created_at=datetime(
                2026,
                8,
                20,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_summary(
            product=self.product_without_variants
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            len(response.data["results"]),
            1,
        )

        row = response.data["results"][0]

        self.assertEqual(row["opening_stock"], 10)
        self.assertEqual(row["entries"], 10)
        self.assertEqual(row["sales"], 4)
        self.assertEqual(
            row["positive_adjustments"],
            2,
        )
        self.assertEqual(
            row["negative_adjustments"],
            1,
        )
        self.assertEqual(row["net_movement"], 7)
        self.assertEqual(row["closing_stock"], 17)
        self.assertEqual(row["current_stock"], 17)
        self.assertEqual(row["movements_count"], 4)

    def test_movements_after_period_are_removed_from_historical_closing_stock(
        self,
    ):
        # Agosto termina con stock 10.
        # En septiembre entra +7.
        # Stock actual = 17.

        self.product_without_variants.stock = 10
        self.product_without_variants.save(
            update_fields=["stock"]
        )

        create_stock_movement(
            product=self.product_without_variants,
            created_by=self.inventory_user,
            movement_type="entry",
            quantity=7,
            created_at=datetime(
                2026,
                9,
                2,
                12,
                tzinfo=timezone.utc,
            ),
        )

        response = self._get_summary(
            product=self.product_without_variants
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        row = response.data["results"][0]

        self.assertEqual(
            row["current_stock"],
            17,
        )

        self.assertEqual(
            row["closing_stock"],
            10,
        )

        self.assertEqual(
            row["opening_stock"],
            10,
        )

        self.assertEqual(
            row["net_movement"],
            0,
        )
    
    def test_product_with_variants_returns_one_row_per_variant(
        self,
    ):
        response = self._get_summary(
            product=(
                self.product_with_variants
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            len(response.data["results"]),
            2,
        )

        returned_variants = {
            row["variant"]["label"]:
            row["current_stock"]
            for row in response.data[
                "results"
            ]
        }

        self.assertEqual(
            returned_variants["S"],
            8,
        )

        self.assertEqual(
            returned_variants["L"],
            4,
        )

        self.assertEqual(
            response.data["totals"][
                "current_stock"
            ],
            12,
        )

    def test_variant_filter_returns_only_requested_variant(
        self,
    ):
        response = self._get_summary(
            product=(
                self.product_with_variants
            ),
            variant=self.variant_small,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.assertEqual(
            len(response.data["results"]),
            1,
        )

        self.assertEqual(
            response.data["results"][0][
                "variant"
            ]["public_id"],
            str(
                self.variant_small.public_id
            ),
        )

    def test_variant_requires_product_filter(
        self,
    ):
        response = self._get_summary(
            variant=self.variant_small,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            msg=response.data,
        )

    def test_foreign_product_returns_not_found(
        self,
    ):
        response = self._get_summary(
            product=self.foreign_product,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_foreign_business_report_is_forbidden(
        self,
    ):
        response = self._get_summary(
            business=self.business_b,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_invalid_date_range_is_rejected(
        self,
    ):
        response = self._get_summary(
            date_from="2026-08-31",
            date_to="2026-08-01",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_inventory_role_can_read_report(
        self,
    ):
        self.authenticate_as(
            self.inventory_user
        )

        response = self._get_summary()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )