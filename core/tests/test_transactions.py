from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import create_product


class TransactionBusinessIsolationTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.product_a = create_product(
            business=cls.business_a,
            status=cls.active_status,
            title="Producto A",
            stock=20,
        )

        cls.product_b = create_product(
            business=cls.business_b,
            status=cls.active_status,
            title="Producto B",
            stock=20,
        )