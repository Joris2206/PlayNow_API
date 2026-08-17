from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status

from core.models import (
    BusinessMembership,
    StockMovement,
    Transaction,
    TransactionDetail,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_business,
    create_customer,
    create_employee,
    create_membership,
    create_payment_method,
    create_product,
    create_role_user,
    create_user,
)
from core.tests.helpers import get_response_results


class CurrentUserEmployeeContractTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user, cls.admin_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.cashier_user, cls.cashier_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller_user, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )

        cls.cashier_second_business = create_business(
            user=cls.user_b,
            status=cls.active_status,
            business_name="Negocio secundario del cajero",
            create_owner_membership=False,
        )
        cls.cashier_second_employee = create_employee(
            business=cls.cashier_second_business,
            status=cls.active_status,
            full_name="Cajero en segundo negocio",
        )
        create_membership(
            user=cls.cashier_user,
            business=cls.cashier_second_business,
            role=BusinessMembership.ROLE_SELLER,
            employee=cls.cashier_second_employee,
        )

    def _get_me(self, user):
        self.authenticate_as(user)
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_me_returns_nullable_employee_public_id_for_each_role(self):
        cases = (
            (self.user_a, self.business_a, None),
            (self.admin_user, self.business_a, self.admin_employee),
            (self.cashier_user, self.business_a, self.cashier_employee),
            (self.seller_user, self.business_a, self.seller_employee),
        )

        for user, business, employee in cases:
            with self.subTest(role=user.email):
                data = self._get_me(user)
                membership = next(
                    item
                    for item in data["memberships"]
                    if item["business_public_id"] == str(business.public_id)
                )
                expected = str(employee.public_id) if employee else None
                self.assertEqual(membership["employee_public_id"], expected)
                self.assertNotIn("id", data)
                self.assertNotIn("id", membership)

    def test_me_resolves_employee_independently_for_each_business(self):
        data = self._get_me(self.cashier_user)
        by_business = {
            item["business_public_id"]: item["employee_public_id"]
            for item in data["memberships"]
        }

        self.assertEqual(
            by_business[str(self.business_a.public_id)],
            str(self.cashier_employee.public_id),
        )
        self.assertEqual(
            by_business[str(self.cashier_second_business.public_id)],
            str(self.cashier_second_employee.public_id),
        )

    def test_membership_rejects_employee_from_another_business(self):
        user = create_user(email="invalid.membership@playnow.test")
        foreign_employee = create_employee(
            business=self.business_b,
            status=self.active_status,
        )

        with self.assertRaises(DjangoValidationError):
            BusinessMembership.objects.create(
                user=user,
                business=self.business_a,
                role=BusinessMembership.ROLE_SELLER,
                employee=foreign_employee,
            )


class EmployeeSelectionContractTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user, cls.admin_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.cashier_user, cls.cashier_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller_user, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.foreign_employee = create_employee(
            business=cls.business_b,
            status=cls.active_status,
            full_name="Empleado extranjero",
        )
        cls.no_membership_user = create_user(
            email="no.membership.employees@playnow.test"
        )

        type(cls.cashier_employee).objects.filter(
            pk=cls.cashier_employee.pk
        ).update(
            phone="555-0100",
            email="private.cashier@playnow.test",
        )
        cls.cashier_employee.refresh_from_db()

    def _list(self, user, business=None):
        self.authenticate_as(user)
        return self.client.get(
            "/api/employees/",
            {
                "business_public_id": str(
                    (business or self.business_a).public_id
                )
            },
        )

    def test_owner_and_admin_keep_full_employee_read_contract(self):
        for user in (self.user_a, self.admin_user):
            with self.subTest(user=user.email):
                response = self._list(user)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                item = next(
                    row
                    for row in get_response_results(response)
                    if row["public_id"] == str(self.cashier_employee.public_id)
                )
                self.assertIn("phone", item)
                self.assertIn("email", item)
                self.assertIn("business_public_id", item)

    def test_cashier_and_seller_receive_only_selector_fields(self):
        expected_fields = {"public_id", "full_name", "position"}

        for user in (self.cashier_user, self.seller_user):
            with self.subTest(user=user.email):
                response = self._list(user)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                rows = get_response_results(response)
                self.assertTrue(rows)
                self.assertTrue(all(set(row) == expected_fields for row in rows))
                self.assertNotIn(
                    str(self.foreign_employee.public_id),
                    {row["public_id"] for row in rows},
                )

                retrieve = self.client.get(
                    f"/api/employees/{self.cashier_employee.public_id}/"
                )
                self.assertEqual(retrieve.status_code, status.HTTP_200_OK)
                self.assertEqual(set(retrieve.data), expected_fields)

    def test_operational_roles_cannot_search_private_employee_fields(self):
        self.authenticate_as(self.cashier_user)

        private_search = self.client.get(
            "/api/employees/",
            {
                "business_public_id": str(self.business_a.public_id),
                "search": self.cashier_employee.email,
            },
        )
        self.assertEqual(private_search.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response_results(private_search), [])

        name_search = self.client.get(
            "/api/employees/",
            {
                "business_public_id": str(self.business_a.public_id),
                "search": self.cashier_employee.full_name,
            },
        )
        self.assertEqual(name_search.status_code, status.HTTP_200_OK)
        self.assertIn(
            str(self.cashier_employee.public_id),
            {row["public_id"] for row in get_response_results(name_search)},
        )

    def test_cashier_and_seller_cannot_write_employees(self):
        create_payload = {
            "business_public_id": str(self.business_a.public_id),
            "full_name": "Intento no autorizado",
            "position": "Vendedor",
        }
        full_update_payload = {
            "business_public_id": str(self.business_a.public_id),
            "full_name": self.cashier_employee.full_name,
            "phone": self.cashier_employee.phone,
            "email": self.cashier_employee.email,
            "position": self.cashier_employee.position,
            "status_public_id": str(self.active_status.public_id),
        }
        detail_url = f"/api/employees/{self.cashier_employee.public_id}/"

        for user in (self.cashier_user, self.seller_user):
            with self.subTest(user=user.email, method="post"):
                self.authenticate_as(user)
                response = self.client.post(
                    "/api/employees/", create_payload, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

            for method, payload in (
                ("put", full_update_payload),
                ("patch", {"position": "Gerente"}),
                ("delete", None),
            ):
                with self.subTest(user=user.email, method=method):
                    self.authenticate_as(user)
                    request = getattr(self.client, method)
                    if payload is None:
                        response = request(detail_url)
                    else:
                        response = request(detail_url, payload, format="json")
                    self.assertEqual(
                        response.status_code,
                        status.HTTP_403_FORBIDDEN,
                    )

    def test_cross_business_and_no_membership_users_cannot_read_employees(self):
        self.authenticate_as(self.seller_user)
        foreign_retrieve = self.client.get(
            f"/api/employees/{self.foreign_employee.public_id}/"
        )
        self.assertEqual(foreign_retrieve.status_code, status.HTTP_404_NOT_FOUND)

        foreign_list = self._list(self.seller_user, self.business_b)
        self.assertEqual(foreign_list.status_code, status.HTTP_403_FORBIDDEN)

        no_membership_list = self._list(self.no_membership_user)
        self.assertEqual(
            no_membership_list.status_code,
            status.HTTP_403_FORBIDDEN,
        )


class TransactionEmployeeAuthorizationTests(BusinessIsolationTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user, cls.admin_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.cashier_user, cls.cashier_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller_user, cls.seller_employee, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.foreign_employee = create_employee(
            business=cls.business_b,
            status=cls.active_status,
        )
        cls.no_membership_user = create_user(
            email="no.membership.sales@playnow.test"
        )
        cls.customer = create_customer(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.payment_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
        )
        cls.product = create_product(
            business=cls.business_a,
            status=cls.active_status,
            stock=20,
            base_price=Decimal("100.00"),
        )

    def _sale_payload(self, employee=None):
        return {
            "business_public_id": str(self.business_a.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(
                (employee or self.seller_employee).public_id
            ),
            "payment_method_public_id": str(self.payment_method.public_id),
            "type": "sale",
            "details": [
                {
                    "product_public_id": str(self.product.public_id),
                    "quantity": 1,
                }
            ],
        }

    def test_all_sales_roles_can_attribute_sale_to_same_business_employee(self):
        for user in (
            self.user_a,
            self.admin_user,
            self.cashier_user,
            self.seller_user,
        ):
            with self.subTest(user=user.email):
                self.authenticate_as(user)
                response = self.client.post(
                    "/api/transactions/",
                    self._sale_payload(self.seller_employee),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                transaction = Transaction.objects.get(
                    public_id=response.data["public_id"]
                )
                self.assertEqual(transaction.employee, self.seller_employee)
                self.assertEqual(transaction.created_by, user)

    def test_client_cannot_falsify_created_by(self):
        self.authenticate_as(self.cashier_user)
        payload = self._sale_payload()
        payload["created_by"] = str(self.user_b.public_id)
        payload["created_by_email"] = self.user_b.email

        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction = Transaction.objects.get(public_id=response.data["public_id"])
        self.assertEqual(transaction.created_by, self.cashier_user)
        self.assertEqual(response.data["created_by_email"], self.cashier_user.email)

    def test_cross_business_employee_rejection_has_no_partial_effects(self):
        self.authenticate_as(self.cashier_user)
        initial_stock = self.product.stock
        counts_before = (
            Transaction.objects.count(),
            TransactionDetail.objects.count(),
            StockMovement.objects.count(),
        )

        response = self.client.post(
            "/api/transactions/",
            self._sale_payload(self.foreign_employee),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)
        self.assertEqual(
            (
                Transaction.objects.count(),
                TransactionDetail.objects.count(),
                StockMovement.objects.count(),
            ),
            counts_before,
        )

    def test_nonexistent_employee_is_rejected_without_inventory_effects(self):
        self.authenticate_as(self.cashier_user)
        initial_stock = self.product.stock
        payload = self._sale_payload()
        payload["employee_public_id"] = str(uuid4())

        response = self.client.post(
            "/api/transactions/", payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("employee_public_id", response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionDetail.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_user_without_membership_cannot_create_sale(self):
        self.authenticate_as(self.no_membership_user)
        initial_stock = self.product.stock

        response = self.client.post(
            "/api/transactions/", self._sale_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionDetail.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_valid_sale_updates_stock_and_creates_one_movement(self):
        self.authenticate_as(self.cashier_user)
        initial_stock = self.product.stock

        response = self.client.post(
            "/api/transactions/", self._sale_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transaction = Transaction.objects.get(public_id=response.data["public_id"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 1)
        self.assertEqual(transaction.details.count(), 1)
        self.assertEqual(transaction.stock_movements.count(), 1)
        movement = transaction.stock_movements.get()
        self.assertEqual(movement.product, self.product)
        self.assertEqual(movement.quantity, -1)
