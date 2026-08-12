from rest_framework import status

from core.models import Customer, PaymentMethod, Supplier
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import create_customer, create_payment_method, create_supplier


class DirectBusinessIsolationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.customer_a = create_customer(business=cls.business_a, status=cls.active_status)
        cls.customer_b = create_customer(business=cls.business_b, status=cls.active_status)
        cls.supplier_a = create_supplier(business=cls.business_a, status=cls.active_status)
        cls.supplier_b = create_supplier(business=cls.business_b, status=cls.active_status)
        cls.method_a = create_payment_method(business=cls.business_a, status=cls.active_status)
        cls.method_b = create_payment_method(business=cls.business_b, status=cls.active_status)

    def test_customer_supplier_and_payment_method_lists_are_isolated(self):
        cases = [
            ("/api/customers/", self.customer_a, self.customer_b),
            ("/api/suppliers/", self.supplier_a, self.supplier_b),
            ("/api/payment-methods/", self.method_a, self.method_b),
        ]
        for endpoint, owned, foreign in cases:
            with self.subTest(endpoint=endpoint):
                self.assert_list_contains_only_owned_object(
                    endpoint=endpoint,
                    owned_object=owned,
                    foreign_object=foreign,
                )

    def test_cannot_create_related_resources_in_foreign_business(
        self,
    ):
        cases = [
            (
                "/api/customers/",
                {
                    "business_public_id": str(
                        self.business_b.public_id
                    ),
                    "full_name": "Cliente infiltrado",
                    "phone": "88888888",
                    "email": "cliente.infiltrado@test.com",
                },
                lambda: Customer.objects.filter(
                    full_name="Cliente infiltrado",
                ).exists(),
            ),
            (
                "/api/suppliers/",
                {
                    "business_public_id": str(
                        self.business_b.public_id
                    ),
                    "name": "Proveedor infiltrado",
                    "phone": "87777777",
                    "email": "proveedor.infiltrado@test.com",
                },
                lambda: Supplier.objects.filter(
                    name="Proveedor infiltrado",
                ).exists(),
            ),
            (
                "/api/payment-methods/",
                {
                    "business_public_id": str(
                        self.business_b.public_id
                    ),
                    "name": "Método infiltrado",
                },
                lambda: PaymentMethod.objects.filter(
                    name="Método infiltrado",
                ).exists(),
            ),
        ]

        for endpoint, payload, exists in cases:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(
                    endpoint,
                    payload,
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                    msg=(
                        f"{endpoint} devolvió "
                        f"{response.status_code}: "
                        f"{response.data}"
                    ),
                )

                self.assertFalse(exists())
