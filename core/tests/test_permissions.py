from rest_framework import status
from rest_framework.test import APITestCase

from core.models import BusinessMembership
from core.tests.factories import (
    create_business, create_customer, create_payment_method, create_product,
    create_role_user, create_status, create_user,
)

class RolePermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.owner = create_user(email="owner@roles.test", full_name="Owner")
        cls.business = create_business(user=cls.owner, status=cls.active_status)
        cls.customer = create_customer(business=cls.business, status=cls.active_status)
        cls.method = create_payment_method(business=cls.business, status=cls.active_status)
        cls.product = create_product(business=cls.business, status=cls.active_status, stock=30)

        cls.admin, cls.admin_employee, _ = create_role_user(
            business=cls.business, role=BusinessMembership.ROLE_ADMIN, status=cls.active_status,
        )
        cls.cashier, cls.cashier_employee, _ = create_role_user(
            business=cls.business, role=BusinessMembership.ROLE_CASHIER, status=cls.active_status,
        )
        cls.seller, cls.seller_employee, _ = create_role_user(
            business=cls.business, role=BusinessMembership.ROLE_SELLER, status=cls.active_status,
        )
        cls.inventory, cls.inventory_employee, _ = create_role_user(
            business=cls.business, role=BusinessMembership.ROLE_INVENTORY, status=cls.active_status,
        )
        cls.viewer, cls.viewer_employee, _ = create_role_user(
            business=cls.business, role=BusinessMembership.ROLE_VIEWER, status=cls.active_status,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def product_payload(self, title):
        return {
            "business_public_id": str(
                self.business.public_id
            ),
            "title": title,
            "description": "",
            "image_url": "",
            "base_price": "100.00",
            "base_cost": "60.00",
            "stock": 5,
            "is_visible": True,
        }

    def sale_payload(self, employee):
        return {
            "business_public_id": str(self.business.public_id),
            "customer_public_id": str(self.customer.public_id),
            "employee_public_id": str(employee.public_id),
            "payment_method_public_id": str(self.method.public_id),
            "type": "sale",
            "details": [{"product_public_id": str(self.product.public_id), "quantity": 1}],
        }

    def test_owner_admin_inventory_can_create_product(self):
        cases = [
            (self.owner, "Owner product"),
            (self.admin, "Admin product"),
            (self.inventory, "Inventory product"),
        ]
        for user, title in cases:
            with self.subTest(user=user.email):
                self.authenticate(user)
                response = self.client.post(
                    "/api/products/", self.product_payload(title), format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=response.data,)

    def test_cashier_seller_viewer_cannot_create_product(self):
        cases = [self.cashier, self.seller, self.viewer]
        for user in cases:
            with self.subTest(user=user.email):
                self.authenticate(user)
                response = self.client.post(
                    "/api/products/",
                    self.product_payload(f"Forbidden {user.pk}"),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, msg=response.data,)

    def test_cashier_and_seller_can_create_sale(self):
        for user in [self.cashier, self.seller]:
            with self.subTest(user=user.email):
                self.authenticate(user)
                response = self.client.post(
                    "/api/transactions/",
                    self.sale_payload(self.seller_employee),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_inventory_and_viewer_cannot_create_sale(self):
        for user in [self.inventory, self.viewer]:
            with self.subTest(user=user.email):
                self.authenticate(user)
                response = self.client.post(
                    "/api/transactions/",
                    self.sale_payload(self.seller_employee),
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
