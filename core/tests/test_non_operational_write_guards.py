from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase

from core.models import (
    Business,
    BusinessMembership,
    Customer,
    Debt,
    DebtPayment,
    Employee,
    PaymentMethod,
    Product,
    ProductCategory,
    StockMovement,
    Supplier,
    Transaction,
)
from core.serializers import ProductCategorySerializer
from core.services.debt_payments import register_debt_payment
from core.tests.factories import (
    create_business,
    create_debt,
    create_payment_method,
    create_status,
    create_transaction,
    create_user,
)
from core.views import ProductCategoryViewSet


class NonOperationalBusinessWriteGuardTests(APITestCase):
    flows = (
        "category",
        "product",
        "employee",
        "customer",
        "supplier",
        "payment_method",
        "debt_payment",
    )

    target_models = {
        "category": ProductCategory,
        "product": Product,
        "employee": Employee,
        "customer": Customer,
        "supplier": Supplier,
        "payment_method": PaymentMethod,
        "debt_payment": DebtPayment,
    }

    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.inactive_status = create_status("Inactivo")
        cls.deleted_status = create_status("Eliminado")
        cls.unknown_status = create_status("Estado desconocido")
        cls.owner = create_user(email="write-guard-owner@playnow.test")
        cls.platform = create_user(
            email="write-guard-platform@playnow.test",
            is_superuser=True,
        )

        cls.businesses = {}
        cls.resources = {}
        for label, business_status in (
            ("active", cls.active_status),
            ("inactive", cls.inactive_status),
            ("deleted", cls.deleted_status),
            ("unknown", cls.unknown_status),
        ):
            business = create_business(
                user=cls.owner,
                status=business_status,
                business_name=f"Write Guard {label}",
            )
            payment_method = create_payment_method(
                business=business,
                status=cls.active_status,
                name=f"Write Guard Cash {label}",
                method_type=PaymentMethod.TYPE_CASH,
            )
            transaction = create_transaction(
                business=business,
                created_by=cls.owner,
                status=cls.active_status,
                total_value=Decimal("100.00"),
                is_debt=True,
            )
            debt = create_debt(
                transaction=transaction,
                total_amount=Decimal("100.00"),
            )
            cls.businesses[label] = business
            cls.resources[business.pk] = {
                "payment_method": payment_method,
                "transaction": transaction,
                "debt": debt,
            }

    def _payload(self, flow, business):
        suffix = uuid4().hex
        common = {
            "business_public_id": str(business.public_id),
        }
        if flow == "category":
            return "/api/categories/", {
                **common,
                "name": f"Category {suffix}",
            }
        if flow == "product":
            return "/api/products/", {
                **common,
                "title": f"Product {suffix}",
                "description": "",
                "image_url": "",
                "base_price": "20.00",
                "base_cost": "10.00",
                "stock": 3,
                "is_visible": True,
            }
        if flow == "employee":
            return "/api/employees/", {
                **common,
                "full_name": f"Employee {suffix}",
                "phone": "",
                "email": "",
                "position": "Tester",
            }
        if flow == "customer":
            return "/api/customers/", {
                **common,
                "full_name": f"Customer {suffix}",
                "phone": "555-0101",
                "email": "",
            }
        if flow == "supplier":
            return "/api/suppliers/", {
                **common,
                "name": f"Supplier {suffix}",
                "phone": "555-0102",
                "email": "",
            }
        if flow == "payment_method":
            return "/api/payment-methods/", {
                **common,
                "name": f"Payment method {suffix}",
                "method_type": PaymentMethod.TYPE_CARD,
            }

        resources = self.resources[business.pk]
        return "/api/debt-payments/", {
            "debt_public_id": str(resources["debt"].public_id),
            "amount": "10.00",
            "payment_date": str(date.today()),
            "payment_method_public_id": str(
                resources["payment_method"].public_id
            ),
        }

    def _state_snapshot(self, business):
        resources = self.resources[business.pk]
        models = (
            Business,
            BusinessMembership,
            ProductCategory,
            Product,
            Employee,
            Customer,
            Supplier,
            PaymentMethod,
            Transaction,
            Debt,
            DebtPayment,
            StockMovement,
        )
        return {
            "counts": {
                model.__name__: model.objects.count()
                for model in models
            },
            "business": Business.objects.filter(pk=business.pk).values().get(),
            "debt": Debt.objects.filter(
                pk=resources["debt"].pk
            ).values().get(),
            "transaction": Transaction.objects.filter(
                pk=resources["transaction"].pk
            ).values().get(),
            "payment_method": PaymentMethod.objects.filter(
                pk=resources["payment_method"].pk
            ).values().get(),
        }

    def _post(self, *, actor, flow, business):
        self.client.force_authenticate(user=actor)
        endpoint, payload = self._payload(flow, business)
        return self.client.post(endpoint, payload, format="json")

    def test_platform_admin_post_matrix_by_business_status(self):
        for business_label, expected_status in (
            ("active", status.HTTP_201_CREATED),
            ("inactive", status.HTTP_404_NOT_FOUND),
            ("deleted", status.HTTP_404_NOT_FOUND),
            ("unknown", status.HTTP_404_NOT_FOUND),
        ):
            business = self.businesses[business_label]
            for flow in self.flows:
                with self.subTest(business=business_label, flow=flow):
                    before = self._state_snapshot(business)
                    target_model = self.target_models[flow]
                    target_count = target_model.objects.count()
                    with patch("core.api.views.base.log_action") as audit:
                        response = self._post(
                            actor=self.platform,
                            flow=flow,
                            business=business,
                        )
                    self.assertEqual(
                        response.status_code,
                        expected_status,
                        msg=getattr(response, "data", None),
                    )
                    if expected_status == status.HTTP_201_CREATED:
                        self.assertEqual(
                            target_model.objects.count(),
                            target_count + 1,
                        )
                        audit.assert_called_once()
                    else:
                        self.assertEqual(self._state_snapshot(business), before)
                        audit.assert_not_called()

    def test_local_owner_keeps_active_behavior_and_no_inactive_bypass(self):
        for business_label, expected_status in (
            ("active", status.HTTP_201_CREATED),
            ("inactive", status.HTTP_404_NOT_FOUND),
        ):
            business = self.businesses[business_label]
            for flow in self.flows:
                with self.subTest(business=business_label, flow=flow):
                    before = self._state_snapshot(business)
                    response = self._post(
                        actor=self.owner,
                        flow=flow,
                        business=business,
                    )
                    self.assertEqual(
                        response.status_code,
                        expected_status,
                        msg=getattr(response, "data", None),
                    )
                    if expected_status != status.HTTP_201_CREATED:
                        self.assertEqual(self._state_snapshot(business), before)

    def test_debt_payment_rejection_rolls_back_every_related_record(self):
        for business_label in ("inactive", "deleted", "unknown"):
            business = self.businesses[business_label]
            with self.subTest(business=business_label):
                before = self._state_snapshot(business)
                with patch("core.api.views.base.log_action") as audit:
                    response = self._post(
                        actor=self.platform,
                        flow="debt_payment",
                        business=business,
                    )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                    msg=getattr(response, "data", None),
                )
                self.assertEqual(self._state_snapshot(business), before)
                audit.assert_not_called()

    def test_debt_payment_service_revalidates_business_under_transaction(self):
        for business_label in ("inactive", "deleted", "unknown"):
            business = self.businesses[business_label]
            resources = self.resources[business.pk]
            with self.subTest(business=business_label):
                before = self._state_snapshot(business)
                with self.assertRaises(NotFound):
                    register_debt_payment(
                        debt_id=resources["debt"].pk,
                        amount=Decimal("10.00"),
                        payment_date=date.today(),
                        payment_method_id=resources["payment_method"].pk,
                        actor=self.platform,
                        observed_remaining_amount=Decimal("100.00"),
                    )
                self.assertEqual(self._state_snapshot(business), before)

    def test_business_relation_validates_status_before_superuser_bypass(self):
        business = self.businesses["inactive"]
        serializer = ProductCategorySerializer(data={
            "business_public_id": str(business.public_id),
            "name": "Must not be created",
        })
        serializer.is_valid(raise_exception=True)
        view = ProductCategoryViewSet()
        view.request = SimpleNamespace(user=self.platform)

        with patch.object(
            view,
            "_user_can_access_business",
            wraps=view._user_can_access_business,
        ) as membership_or_admin_check:
            with self.assertRaises(NotFound):
                view._validate_business_relation(serializer)

        membership_or_admin_check.assert_not_called()
        self.assertFalse(
            ProductCategory.objects.filter(
                business=business,
                name="Must not be created",
            ).exists()
        )
