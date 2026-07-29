from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import create_product


class StockMovementMethodTests(
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

    def test_stock_movement_post_is_not_allowed(self):
        self.assert_method_not_allowed(
            method="post",
            endpoint="/api/stock-movements/",
            payload={
                "product": self.product_a.pk,
                "type": "entry",
                "quantity": 10,
                "note": "Intento manual",
            },
        )

    def test_stock_movement_put_is_not_allowed(self):
        response = self.client.put(
            "/api/stock-movements/uuid-inexistente/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_stock_movement_patch_is_not_allowed(self):
        response = self.client.patch(
            "/api/stock-movements/uuid-inexistente/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_stock_movement_delete_is_not_allowed(self):
        response = self.client.delete(
            "/api/stock-movements/uuid-inexistente/",
        )

        self.assertEqual(
            response.status_code,
            405,
        )