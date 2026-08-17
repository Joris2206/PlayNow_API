from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import create_category
from core.tests.helpers import get_public_ids


class ProductCategorySearchTests(BusinessIsolationTestCase):
    def test_category_list_searches_by_name_within_business(self):
        matching = create_category(
            business=self.business_a,
            status=self.active_status,
            name="Bebidas frías",
        )
        create_category(
            business=self.business_a,
            status=self.active_status,
            name="Comida preparada",
        )
        foreign_matching = create_category(
            business=self.business_b,
            status=self.active_status,
            name="Bebidas calientes",
        )

        response = self.client.get(
            "/api/categories/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "search": "bebidas",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )
        returned_ids = get_public_ids(response)
        self.assertEqual(
            returned_ids,
            {str(matching.public_id)},
        )
        self.assertNotIn(
            str(foreign_matching.public_id),
            returned_ids,
        )


class SearchFilterOpenApiTests(SimpleTestCase):
    SEARCHABLE_COLLECTION_PATHS = {
        "/api/businesses/",
        "/api/categories/",
        "/api/products/",
        "/api/employees/",
        "/api/customers/",
        "/api/suppliers/",
        "/api/payment-methods/",
        "/api/transactions/",
        "/api/notifications/",
        "/api/reminders/",
        "/api/goals/",
        "/api/stock-movements/",
        "/api/public/categories/",
        "/api/public/products/",
    }

    NON_SEARCHABLE_COLLECTION_PATHS = {
        "/api/statuses/",
        "/api/debts/",
        "/api/debt-payments/",
        "/api/budgets/",
        "/api/goal-progress/",
        "/api/commission-plans/",
        "/api/commission-settlements/",
        "/api/cash-registers/",
        "/api/cash-movements/",
        "/api/monthly-closures/",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )

    def _parameter_names(self, path):
        return {
            parameter["name"]
            for parameter in self.schema["paths"][path]["get"].get(
                "parameters",
                [],
            )
        }

    def test_searchable_lists_document_search(self):
        for path in self.SEARCHABLE_COLLECTION_PATHS:
            with self.subTest(path=path):
                self.assertIn(
                    "search",
                    self._parameter_names(path),
                )

    def test_non_searchable_lists_do_not_document_search(self):
        for path in self.NON_SEARCHABLE_COLLECTION_PATHS:
            with self.subTest(path=path):
                self.assertNotIn(
                    "search",
                    self._parameter_names(path),
                )
