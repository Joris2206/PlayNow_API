from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import Transaction
from core.services.debt_payments import register_debt_payment
from core.serializers import (
    DebtPaymentSerializer,
    DebtSerializer,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_customer,
    create_debt,
    create_debt_payment,
    create_payment_method,
    create_supplier,
    create_transaction,
)
from core.tests.helpers import (
    get_public_ids,
    get_response_results,
)


class DebtBusinessIsolationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tx_a = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            customer=create_customer(
                business=cls.business_a,
                status=cls.active_status,
                full_name="Cliente A",
            ),
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.customer_b = create_customer(
            business=cls.business_b,
            status=cls.active_status,
            full_name="Cliente B",
        )
        cls.supplier_b = create_supplier(
            business=cls.business_b,
            status=cls.active_status,
            name="Proveedor B",
        )
        cls.tx_b = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            customer=cls.customer_b,
            is_debt=True,
            total_value=Decimal("200.00"),
        )
        cls.supplier_tx_b = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            supplier=cls.supplier_b,
            transaction_type="purchase",
            is_debt=True,
            total_value=Decimal("300.00"),
        )
        cls.debt_a = create_debt(transaction=cls.tx_a)
        cls.debt_b = create_debt(transaction=cls.tx_b)
        cls.supplier_debt_b = create_debt(
            transaction=cls.supplier_tx_b
        )

    def test_user_only_lists_own_debts(self):
        response = self.client.get(
            "/api/debts/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = get_response_results(response)
        self.assertEqual(
            {item["public_id"] for item in results},
            {str(self.debt_a.public_id)},
        )
        self.assertEqual(
            str(results[0]["customer_public_id"]),
            str(self.tx_a.customer.public_id),
        )
        self.assertNotEqual(
            str(results[0]["customer_public_id"]),
            str(self.customer_b.public_id),
        )
        self.assertIsNone(results[0]["supplier_public_id"])
        serialized_results = str(results)
        self.assertNotIn(str(self.customer_b.public_id), serialized_results)
        self.assertNotIn(str(self.supplier_b.public_id), serialized_results)

    def test_user_cannot_retrieve_foreign_debt(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=f"/api/debts/{self.debt_b.public_id}/"
        )

    def test_debt_is_read_only(self):
        self.assert_method_not_allowed(method="post", endpoint="/api/debts/", payload={})
        self.assert_method_not_allowed(
            method="patch",
            endpoint=f"/api/debts/{self.debt_a.public_id}/",
            payload={"paid_amount": "10.00"},
        )
        self.assert_method_not_allowed(
            method="delete",
            endpoint=f"/api/debts/{self.debt_a.public_id}/",
        )


class DebtPaymentBusinessIsolationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.method_a = create_payment_method(
            business=cls.business_a, status=cls.active_status
        )
        cls.method_b = create_payment_method(
            business=cls.business_b, status=cls.active_status
        )
        cls.tx_a = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.tx_b = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.debt_a = create_debt(transaction=cls.tx_a)
        cls.debt_b = create_debt(transaction=cls.tx_b)
        cls.payment_a = create_debt_payment(
            debt=cls.debt_a, payment_method=cls.method_a, amount=Decimal("20.00")
        )
        cls.payment_b = create_debt_payment(
            debt=cls.debt_b, payment_method=cls.method_b, amount=Decimal("20.00")
        )

    def test_user_only_lists_own_debt_payments(self):
        self.assert_list_contains_only_owned_object(
            endpoint="/api/debt-payments/",
            owned_object=self.payment_a,
            foreign_object=self.payment_b,
        )

    def test_user_cannot_retrieve_foreign_debt_payment(self):
        self.assert_cannot_retrieve_foreign_object(
            endpoint=f"/api/debt-payments/{self.payment_b.public_id}/"
        )

    def test_create_payment_updates_debt(self):
        response = self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(self.debt_a.public_id),
                "amount": "30.00",
                "payment_date": "2026-08-04",
                "payment_method_public_id": str(self.method_a.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.debt_a.refresh_from_db()
        self.assertEqual(self.debt_a.paid_amount, Decimal("30.00"))

    def test_payment_above_remaining_balance_is_rejected(self):
        response = self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(self.debt_a.public_id),
                "amount": "101.00",
                "payment_date": "2026-08-04",
                "payment_method_public_id": str(self.method_a.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_debt_payment_cannot_be_updated_or_deleted(self):
        self.assert_method_not_allowed(
            method="patch",
            endpoint=f"/api/debt-payments/{self.payment_a.public_id}/",
            payload={"amount": "5.00"},
        )
        self.assert_method_not_allowed(
            method="delete",
            endpoint=f"/api/debt-payments/{self.payment_a.public_id}/",
        )


class DebtRepresentationAndFilterTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="Cliente financiero",
        )
        cls.second_customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
            full_name="Segundo cliente",
        )
        cls.supplier = create_supplier(
            business=cls.business_a,
            status=cls.active_status,
            name="Proveedor financiero",
        )
        cls.payment_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )

        cls.sale_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            customer=cls.customer,
            transaction_type="sale",
            total_value=Decimal("100.00"),
            is_debt=True,
        )
        cls.sale_debt = create_debt(
            transaction=cls.sale_tx,
            total_amount=Decimal("100.00"),
        )

        cls.settled_sale_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            customer=cls.second_customer,
            transaction_type="sale",
            total_value=Decimal("80.00"),
            is_debt=True,
        )
        cls.settled_sale_debt = create_debt(
            transaction=cls.settled_sale_tx,
            total_amount=Decimal("80.00"),
        )
        register_debt_payment(
            debt_id=cls.settled_sale_debt.pk,
            amount=Decimal("80.00"),
            payment_date=date.today(),
            payment_method_id=cls.payment_method.pk,
            actor=cls.user_a,
        )
        cls.settled_sale_debt.refresh_from_db()
        cls.settled_sale_tx.refresh_from_db()

        cls.purchase_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            supplier=cls.supplier,
            transaction_type="purchase",
            total_value=Decimal("200.00"),
            is_debt=True,
        )
        cls.purchase_debt = create_debt(
            transaction=cls.purchase_tx,
            total_amount=Decimal("200.00"),
        )
        register_debt_payment(
            debt_id=cls.purchase_debt.pk,
            amount=Decimal("50.00"),
            payment_date=date.today(),
            payment_method_id=cls.payment_method.pk,
            actor=cls.user_a,
        )
        cls.purchase_debt.refresh_from_db()
        cls.purchase_tx.refresh_from_db()

        cls.expense_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            transaction_type="expense",
            total_value=Decimal("30.00"),
            is_debt=True,
        )
        cls.expense_debt = create_debt(
            transaction=cls.expense_tx,
            total_amount=Decimal("30.00"),
        )

        cls.foreign_tx = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            transaction_type="sale",
            total_value=Decimal("500.00"),
            is_debt=True,
        )
        cls.foreign_debt = create_debt(
            transaction=cls.foreign_tx,
            total_amount=Decimal("500.00"),
        )

    def _list(self, **params):
        query = {
            "business_public_id": str(
                self.business_a.public_id
            ),
        }
        query.update(params)

        return self.client.get(
            "/api/debts/",
            query,
        )

    def test_list_and_retrieve_expose_derived_fields(self):
        response = self._list()

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        debts = {
            item["public_id"]: item
            for item in get_response_results(response)
        }

        expected_business_id = str(
            self.business_a.public_id
        )

        for debt in debts.values():
            self.assertEqual(
                str(debt["business_public_id"]),
                expected_business_id,
            )

        self.assertEqual(
            debts[str(self.sale_debt.public_id)][
                "direction"
            ],
            "receivable",
        )
        self.assertEqual(
            debts[str(self.purchase_debt.public_id)][
                "direction"
            ],
            "payable",
        )
        sale = debts[str(self.sale_debt.public_id)]
        purchase = debts[str(self.purchase_debt.public_id)]
        settled = debts[str(self.settled_sale_debt.public_id)]

        self.assertEqual(
            str(sale["customer_public_id"]),
            str(self.customer.public_id),
        )
        self.assertIsNone(sale["supplier_public_id"])
        self.assertEqual(
            str(purchase["supplier_public_id"]),
            str(self.supplier.public_id),
        )
        self.assertIsNone(purchase["customer_public_id"])
        self.assertEqual(
            sale["transaction_status_name"],
            self.active_status.name,
        )
        self.assertEqual(sale["payment_status"], "pending")
        self.assertEqual(purchase["payment_status"], "partial")
        self.assertEqual(settled["payment_status"], "paid")
        self.assertTrue(settled["is_settled"])
        self.assertIsNone(
            debts[str(self.expense_debt.public_id)][
                "direction"
            ]
        )

        self.assertEqual(
            Decimal(
                debts[str(self.sale_debt.public_id)][
                    "outstanding_amount"
                ]
            ),
            Decimal("100.00"),
        )
        self.assertEqual(
            Decimal(
                debts[str(self.purchase_debt.public_id)][
                    "outstanding_amount"
                ]
            ),
            Decimal("150.00"),
        )
        self.assertEqual(
            Decimal(
                debts[
                    str(self.settled_sale_debt.public_id)
                ]["outstanding_amount"]
            ),
            Decimal("0.00"),
        )

        retrieve_response = self.client.get(
            "/api/debts/"
            f"{self.purchase_debt.public_id}/"
        )

        self.assertEqual(
            retrieve_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            str(retrieve_response.data[
                "business_public_id"
            ]),
            expected_business_id,
        )
        self.assertEqual(
            retrieve_response.data["direction"],
            "payable",
        )
        self.assertEqual(
            str(retrieve_response.data["supplier_public_id"]),
            str(self.supplier.public_id),
        )
        self.assertIsNone(
            retrieve_response.data["customer_public_id"]
        )
        self.assertEqual(
            retrieve_response.data["transaction_status_name"],
            self.active_status.name,
        )
        self.assertEqual(
            retrieve_response.data["payment_status"],
            "partial",
        )
        self.assertEqual(
            Decimal(
                retrieve_response.data[
                    "outstanding_amount"
                ]
            ),
            Decimal("150.00"),
        )

        sale_retrieve_response = self.client.get(
            "/api/debts/"
            f"{self.sale_debt.public_id}/"
        )
        self.assertEqual(
            sale_retrieve_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            str(sale_retrieve_response.data["customer_public_id"]),
            str(self.customer.public_id),
        )
        self.assertIsNone(
            sale_retrieve_response.data["supplier_public_id"]
        )
        self.assertEqual(
            sale_retrieve_response.data["transaction_status_name"],
            self.active_status.name,
        )
        self.assertEqual(
            sale_retrieve_response.data["payment_status"],
            "pending",
        )

    def test_derived_fields_are_read_only(self):
        fields = DebtSerializer().fields

        for field_name in (
            "business_public_id",
            "customer_public_id",
            "supplier_public_id",
            "transaction_status_name",
            "payment_status",
            "direction",
            "outstanding_amount",
        ):
            self.assertTrue(
                fields[field_name].read_only
            )

        self.assertTrue(fields["customer_public_id"].allow_null)
        self.assertTrue(fields["supplier_public_id"].allow_null)
        self.assertFalse(fields["transaction_status_name"].allow_null)
        self.assertFalse(fields["payment_status"].allow_null)
        self.assertEqual(
            set(fields["payment_status"].choices),
            {value for value, _ in Transaction.PAYMENT_STATUSES},
        )

        payment_fields = (
            DebtPaymentSerializer().fields
        )
        self.assertTrue(
            payment_fields[
                "business_public_id"
            ].read_only
        )

    def test_cancelled_transaction_remains_visible_with_counterparty(self):
        response = self.client.delete(
            f"/api/transactions/{self.sale_tx.public_id}/"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        debt_response = self.client.get(
            f"/api/debts/{self.sale_debt.public_id}/"
        )
        self.assertEqual(debt_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            debt_response.data["transaction_status_name"],
            self.void_status.name,
        )
        self.assertEqual(
            str(debt_response.data["customer_public_id"]),
            str(self.customer.public_id),
        )
        self.assertIsNone(debt_response.data["supplier_public_id"])
        self.assertEqual(debt_response.data["direction"], "receivable")

    def test_list_query_count_does_not_grow_per_transaction_relation(self):
        query = {
            "business_public_id": str(self.business_a.public_id),
            "page_size": 1,
        }
        with CaptureQueriesContext(connection) as single_queries:
            single_response = self.client.get("/api/debts/", query)
        self.assertEqual(single_response.status_code, status.HTTP_200_OK)

        query["page_size"] = 100
        with CaptureQueriesContext(connection) as multiple_queries:
            multiple_response = self.client.get("/api/debts/", query)
        self.assertEqual(multiple_response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(get_response_results(multiple_response)), 1)
        self.assertEqual(len(multiple_queries), len(single_queries))

    def test_transaction_type_filter_uses_model_choices(self):
        sale_response = self._list(
            transaction_type="sale"
        )
        purchase_response = self._list(
            transaction_type="purchase"
        )

        self.assertEqual(
            get_public_ids(sale_response),
            {
                str(self.sale_debt.public_id),
                str(self.settled_sale_debt.public_id),
            },
        )
        self.assertEqual(
            get_public_ids(purchase_response),
            {
                str(self.purchase_debt.public_id),
            },
        )

        invalid_response = self._list(
            transaction_type="refund"
        )
        self.assertEqual(
            invalid_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "transaction_type",
            invalid_response.data,
        )

    def test_is_settled_filter(self):
        settled_response = self._list(
            is_settled="true"
        )
        unsettled_response = self._list(
            is_settled="false"
        )

        self.assertEqual(
            get_public_ids(settled_response),
            {
                str(
                    self.settled_sale_debt.public_id
                ),
            },
        )
        self.assertEqual(
            get_public_ids(unsettled_response),
            {
                str(self.sale_debt.public_id),
                str(self.purchase_debt.public_id),
                str(self.expense_debt.public_id),
            },
        )

    def test_customer_and_supplier_filters(self):
        customer_response = self._list(
            customer_public_id=str(
                self.customer.public_id
            )
        )
        supplier_response = self._list(
            supplier_public_id=str(
                self.supplier.public_id
            )
        )

        self.assertEqual(
            get_public_ids(customer_response),
            {str(self.sale_debt.public_id)},
        )
        self.assertEqual(
            get_public_ids(supplier_response),
            {str(self.purchase_debt.public_id)},
        )

    def test_transaction_filter_and_combined_filters(self):
        transaction_response = self._list(
            transaction_public_id=str(
                self.purchase_tx.public_id
            )
        )

        self.assertEqual(
            get_public_ids(transaction_response),
            {str(self.purchase_debt.public_id)},
        )

        combined_response = self._list(
            transaction_type="sale",
            is_settled="false",
            customer_public_id=str(
                self.customer.public_id
            ),
            transaction_public_id=str(
                self.sale_tx.public_id
            ),
        )

        self.assertEqual(
            get_public_ids(combined_response),
            {str(self.sale_debt.public_id)},
        )

    def test_foreign_parent_filter_returns_empty(self):
        response = self._list(
            transaction_public_id=str(
                self.foreign_tx.public_id
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            get_response_results(response),
            [],
        )

    def test_debt_endpoint_remains_read_only(self):
        self.assert_method_not_allowed(
            method="post",
            endpoint="/api/debts/",
            payload={
                "business_public_id": str(
                    self.business_a.public_id
                ),
                "direction": "payable",
                "outstanding_amount": "1.00",
            },
        )
        self.assert_method_not_allowed(
            method="patch",
            endpoint=(
                "/api/debts/"
                f"{self.sale_debt.public_id}/"
            ),
            payload={
                "direction": "payable",
                "outstanding_amount": "1.00",
            },
        )


class DebtPaymentRepresentationAndFilterTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.foreign_method = create_payment_method(
            business=cls.business_b,
            status=cls.active_status,
        )

        cls.first_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("100.00"),
        )
        cls.first_debt = create_debt(
            transaction=cls.first_tx,
            total_amount=Decimal("100.00"),
        )
        cls.first_payment = create_debt_payment(
            debt=cls.first_debt,
            payment_method=cls.method,
            amount=Decimal("10.00"),
        )
        cls.second_payment = create_debt_payment(
            debt=cls.first_debt,
            payment_method=cls.method,
            amount=Decimal("30.00"),
        )

        cls.second_tx = create_transaction(
            business=cls.business_a,
            created_by=cls.user_a,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("200.00"),
        )
        cls.second_debt = create_debt(
            transaction=cls.second_tx,
            total_amount=Decimal("200.00"),
        )
        cls.other_payment = create_debt_payment(
            debt=cls.second_debt,
            payment_method=cls.method,
            amount=Decimal("20.00"),
        )

        cls.foreign_tx = create_transaction(
            business=cls.business_b,
            created_by=cls.user_b,
            status=cls.active_status,
            is_debt=True,
            total_value=Decimal("500.00"),
        )
        cls.foreign_debt = create_debt(
            transaction=cls.foreign_tx,
            total_amount=Decimal("500.00"),
        )
        cls.foreign_payment = create_debt_payment(
            debt=cls.foreign_debt,
            payment_method=cls.foreign_method,
            amount=Decimal("50.00"),
        )

    def _list(self, **params):
        query = {
            "business_public_id": str(
                self.business_a.public_id
            ),
        }
        query.update(params)

        return self.client.get(
            "/api/debt-payments/",
            query,
        )

    def test_list_and_retrieve_expose_business_public_id(self):
        list_response = self._list()

        self.assertEqual(
            list_response.status_code,
            status.HTTP_200_OK,
        )

        expected_business_id = str(
            self.business_a.public_id
        )

        for payment in get_response_results(
            list_response
        ):
            self.assertEqual(
                str(payment["business_public_id"]),
                expected_business_id,
            )

        retrieve_response = self.client.get(
            "/api/debt-payments/"
            f"{self.first_payment.public_id}/"
        )

        self.assertEqual(
            retrieve_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            str(retrieve_response.data[
                "business_public_id"
            ]),
            expected_business_id,
        )

    def test_debt_parent_filter_returns_only_its_payments(self):
        response = self._list(
            debt_public_id=str(
                self.first_debt.public_id
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            get_public_ids(response),
            {
                str(self.first_payment.public_id),
                str(self.second_payment.public_id),
            },
        )

    def test_foreign_debt_parent_returns_empty(self):
        response = self._list(
            debt_public_id=str(
                self.foreign_debt.public_id
            )
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            get_response_results(response),
            [],
        )

    def test_list_without_parent_keeps_normal_behavior(self):
        response = self._list()

        self.assertEqual(
            get_public_ids(response),
            {
                str(self.first_payment.public_id),
                str(self.second_payment.public_id),
                str(self.other_payment.public_id),
            },
        )
        self.assertNotIn(
            str(self.foreign_payment.public_id),
            get_public_ids(response),
        )

    def test_ordering_and_pagination_are_preserved(self):
        ordering_response = self._list(
            ordering="amount"
        )
        amounts = [
            Decimal(item["amount"])
            for item in get_response_results(
                ordering_response
            )
        ]

        self.assertEqual(
            amounts,
            sorted(amounts),
        )

        pagination_response = self._list(
            page_size=1
        )

        self.assertEqual(
            pagination_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            pagination_response.data["count"],
            3,
        )
        self.assertEqual(
            len(pagination_response.data["results"]),
            1,
        )

    def test_existing_post_contract_does_not_require_business(self):
        response = self.client.post(
            "/api/debt-payments/",
            {
                "debt_public_id": str(
                    self.first_debt.public_id
                ),
                "amount": "5.00",
                "payment_date": "2026-08-18",
                "payment_method_public_id": str(
                    self.method.public_id
                ),
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            msg=response.data,
        )
        self.assertEqual(
            str(response.data["business_public_id"]),
            str(self.business_a.public_id),
        )


class FinancialPhaseOneOpenApiTests(
    BusinessIsolationTestCase
):
    def test_schema_advertises_debt_filters(self):
        schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )

        debt_parameters = {
            parameter["name"]
            for parameter in schema["paths"][
                "/api/debts/"
            ]["get"].get("parameters", [])
        }

        self.assertTrue({
            "business_public_id",
            "transaction_type",
            "is_settled",
            "customer_public_id",
            "supplier_public_id",
            "transaction_public_id",
        }.issubset(debt_parameters))

        payment_parameters = {
            parameter["name"]
            for parameter in schema["paths"][
                "/api/debt-payments/"
            ]["get"].get("parameters", [])
        }

        self.assertIn(
            "debt_public_id",
            payment_parameters,
        )

    def test_schema_documents_derived_debt_fields(self):
        schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )
        debt_schema = schema["components"][
            "schemas"
        ]["Debt"]

        properties = debt_schema["properties"]

        self.assertTrue(
            properties["business_public_id"][
                "readOnly"
            ]
        )
        self.assertTrue(
            properties["direction"]["readOnly"]
        )
        self.assertTrue(
            properties["direction"]["nullable"]
        )
        self.assertEqual(
            set(
                schema["components"]["schemas"][
                    properties["direction"]["allOf"][
                        0
                    ]["$ref"].rsplit("/", 1)[-1]
                ]["enum"]
            ),
            {"receivable", "payable"},
        )
        self.assertTrue(
            properties["outstanding_amount"][
                "readOnly"
            ]
        )
        for field_name in (
            "customer_public_id",
            "supplier_public_id",
        ):
            field = properties[field_name]
            self.assertEqual(field["type"], "string")
            self.assertEqual(field["format"], "uuid")
            self.assertTrue(field["nullable"])
            self.assertTrue(field["readOnly"])

        transaction_status = properties[
            "transaction_status_name"
        ]
        self.assertEqual(transaction_status["type"], "string")
        self.assertTrue(transaction_status["readOnly"])
        self.assertNotIn("nullable", transaction_status)

        payment_status = properties["payment_status"]
        self.assertTrue(payment_status["readOnly"])
        self.assertNotIn("nullable", payment_status)
        enum_component = schema["components"]["schemas"][
            payment_status["allOf"][0]["$ref"].rsplit("/", 1)[-1]
        ]
        self.assertEqual(
            set(enum_component["enum"]),
            {value for value, _ in Transaction.PAYMENT_STATUSES},
        )

        self.assertNotIn(
            "DebtRequest",
            schema["components"]["schemas"],
        )
