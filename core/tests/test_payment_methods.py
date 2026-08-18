from drf_spectacular.generators import SchemaGenerator
from rest_framework import status

from core.models import (
    BusinessMembership,
    PaymentMethod,
)
from core.tests.base import BusinessIsolationTestCase
from core.tests.factories import (
    create_payment_method,
    create_role_user,
    create_status,
    create_user,
)
from core.tests.helpers import (
    get_public_ids,
    get_response_results,
)


class PaymentMethodContractTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Efectivo",
            method_type=PaymentMethod.TYPE_CASH,
        )

        cls.other_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Otro",
            method_type=PaymentMethod.TYPE_OTHER,
        )

    def _payload(self, **overrides):
        payload = {
            "business_public_id": str(
                self.business_a.public_id
            ),
            "name": "Nuevo método",
            "method_type": (
                PaymentMethod.TYPE_TRANSFER
            ),
        }
        payload.update(overrides)
        return payload

    def test_list_and_retrieve_expose_method_type(self):
        list_response = self.client.get(
            "/api/payment-methods/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertEqual(
            list_response.status_code,
            status.HTTP_200_OK,
        )

        listed_method = next(
            item
            for item in get_response_results(
                list_response
            )
            if item["public_id"]
            == str(self.method.public_id)
        )

        self.assertEqual(
            listed_method["method_type"],
            PaymentMethod.TYPE_CASH,
        )

        retrieve_response = self.client.get(
            "/api/payment-methods/"
            f"{self.method.public_id}/"
        )

        self.assertEqual(
            retrieve_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            retrieve_response.data["method_type"],
            PaymentMethod.TYPE_CASH,
        )

    def test_post_accepts_every_model_choice(self):
        for index, (method_type, _) in enumerate(
            PaymentMethod.METHOD_TYPES,
            start=1,
        ):
            with self.subTest(method_type=method_type):
                response = self.client.post(
                    "/api/payment-methods/",
                    self._payload(
                        name=f"Método {index}",
                        method_type=method_type,
                    ),
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )
                self.assertEqual(
                    response.data["method_type"],
                    method_type,
                )

    def test_post_requires_valid_method_type(self):
        missing_payload = self._payload(
            name="Sin tipo",
        )
        missing_payload.pop("method_type")

        missing_response = self.client.post(
            "/api/payment-methods/",
            missing_payload,
            format="json",
        )

        self.assertEqual(
            missing_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "method_type",
            missing_response.data,
        )

        invalid_response = self.client.post(
            "/api/payment-methods/",
            self._payload(
                name="Tipo inválido",
                method_type="crypto",
            ),
            format="json",
        )

        self.assertEqual(
            invalid_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "method_type",
            invalid_response.data,
        )

    def test_put_requires_and_updates_method_type(self):
        endpoint = (
            "/api/payment-methods/"
            f"{self.method.public_id}/"
        )

        response = self.client.put(
            endpoint,
            self._payload(
                name="Efectivo actualizado",
                method_type=PaymentMethod.TYPE_CARD,
            ),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            msg=response.data,
        )

        self.method.refresh_from_db()
        self.assertEqual(
            self.method.method_type,
            PaymentMethod.TYPE_CARD,
        )

        missing_payload = self._payload(
            name="Sin tipo en PUT",
        )
        missing_payload.pop("method_type")

        missing_response = self.client.put(
            endpoint,
            missing_payload,
            format="json",
        )

        self.assertEqual(
            missing_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn(
            "method_type",
            missing_response.data,
        )

    def test_patch_updates_or_preserves_method_type(self):
        endpoint = (
            "/api/payment-methods/"
            f"{self.method.public_id}/"
        )

        update_response = self.client.patch(
            endpoint,
            {
                "method_type": (
                    PaymentMethod.TYPE_TRANSFER
                ),
            },
            format="json",
        )

        self.assertEqual(
            update_response.status_code,
            status.HTTP_200_OK,
        )

        preserve_response = self.client.patch(
            endpoint,
            {
                "name": "Transferencia principal",
            },
            format="json",
        )

        self.assertEqual(
            preserve_response.status_code,
            status.HTTP_200_OK,
        )

        self.method.refresh_from_db()
        self.assertEqual(
            self.method.method_type,
            PaymentMethod.TYPE_TRANSFER,
        )

    def test_existing_other_value_is_preserved(self):
        response = self.client.get(
            "/api/payment-methods/"
            f"{self.other_method.public_id}/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            response.data["method_type"],
            PaymentMethod.TYPE_OTHER,
        )

    def test_cannot_create_method_for_foreign_business(self):
        response = self.client.post(
            "/api/payment-methods/",
            self._payload(
                business_public_id=str(
                    self.business_b.public_id
                ),
                name="Método ajeno",
            ),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertFalse(
            PaymentMethod.objects.filter(
                business=self.business_b,
                name="Método ajeno",
            ).exists()
        )

    def test_schema_documents_required_model_choices(self):
        schema = SchemaGenerator().get_schema(
            request=None,
            public=True,
        )
        request_schema = schema["components"][
            "schemas"
        ]["PaymentMethodRequest"]
        response_schema = schema["components"][
            "schemas"
        ]["PaymentMethod"]

        self.assertIn(
            "method_type",
            request_schema["required"],
        )
        self.assertEqual(
            set(
                schema["components"]["schemas"][
                    request_schema["properties"][
                        "method_type"
                    ]["$ref"].rsplit("/", 1)[-1]
                ]["enum"]
            ),
            {
                method_type
                for method_type, _
                in PaymentMethod.METHOD_TYPES
            },
        )
        self.assertIn(
            "method_type",
            response_schema["properties"],
        )


class PaymentMethodRoleVisibilityTests(
    BusinessIsolationTestCase
):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.inactive_status = create_status(
            "Inactivo"
        )
        cls.deleted_status = create_status(
            "Eliminado"
        )

        cls.active_method = create_payment_method(
            business=cls.business_a,
            status=cls.active_status,
            name="Activo",
            method_type=PaymentMethod.TYPE_CASH,
        )
        cls.inactive_method = create_payment_method(
            business=cls.business_a,
            status=cls.inactive_status,
            name="Inactivo",
        )
        cls.deleted_method = create_payment_method(
            business=cls.business_a,
            status=cls.deleted_status,
            name="Eliminado",
        )
        cls.foreign_method = create_payment_method(
            business=cls.business_b,
            status=cls.active_status,
            name="Activo ajeno",
        )

        cls.admin_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
        )
        cls.cashier_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_CASHIER,
            status=cls.active_status,
        )
        cls.seller_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_SELLER,
            status=cls.active_status,
        )
        cls.inventory_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_INVENTORY,
            status=cls.active_status,
        )
        cls.viewer_user, _, _ = create_role_user(
            business=cls.business_a,
            role=BusinessMembership.ROLE_VIEWER,
            status=cls.active_status,
        )
        cls.superuser = create_user(
            email="superuser.payment@playnow.test",
            full_name="Superuser Payment",
            is_superuser=True,
        )

    def _list(self, user, **params):
        self.authenticate_as(user)

        query = {
            "business_public_id": str(
                self.business_a.public_id
            ),
        }
        query.update(params)

        return self.client.get(
            "/api/payment-methods/",
            query,
        )

    def test_administrative_roles_see_every_status(self):
        expected = {
            str(self.active_method.public_id),
            str(self.inactive_method.public_id),
            str(self.deleted_method.public_id),
        }

        for user in (
            self.user_a,
            self.admin_user,
            self.viewer_user,
        ):
            with self.subTest(user=user.email):
                response = self._list(user)

                self.assertEqual(
                    response.status_code,
                    status.HTTP_200_OK,
                )
                self.assertEqual(
                    get_public_ids(response),
                    expected,
                )

    def test_operational_roles_only_see_and_retrieve_active(self):
        for user in (
            self.cashier_user,
            self.seller_user,
            self.inventory_user,
        ):
            with self.subTest(user=user.email):
                response = self._list(user)

                self.assertEqual(
                    get_public_ids(response),
                    {
                        str(
                            self.active_method.public_id
                        ),
                    },
                )

                active_response = self.client.get(
                    "/api/payment-methods/"
                    f"{self.active_method.public_id}/"
                )
                self.assertEqual(
                    active_response.status_code,
                    status.HTTP_200_OK,
                )

                for hidden_method in (
                    self.inactive_method,
                    self.deleted_method,
                ):
                    hidden_response = self.client.get(
                        "/api/payment-methods/"
                        f"{hidden_method.public_id}/"
                    )
                    self.assertEqual(
                        hidden_response.status_code,
                        status.HTTP_404_NOT_FOUND,
                    )

    def test_status_filter_cannot_bypass_operational_visibility(self):
        response = self._list(
            self.inventory_user,
            status_public_id=str(
                self.inactive_status.public_id
            ),
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            get_response_results(response),
            [],
        )

    def test_inventory_cannot_write_payment_methods(self):
        self.authenticate_as(
            self.inventory_user
        )
        endpoint = (
            "/api/payment-methods/"
            f"{self.active_method.public_id}/"
        )
        payload = {
            "business_public_id": str(
                self.business_a.public_id
            ),
            "name": "Inventario no autorizado",
            "method_type": PaymentMethod.TYPE_CARD,
        }

        responses = (
            self.client.post(
                "/api/payment-methods/",
                payload,
                format="json",
            ),
            self.client.put(
                endpoint,
                payload,
                format="json",
            ),
            self.client.patch(
                endpoint,
                {"name": "No autorizado"},
                format="json",
            ),
            self.client.delete(endpoint),
        )

        for response in responses:
            self.assertEqual(
                response.status_code,
                status.HTTP_403_FORBIDDEN,
            )

    def test_owner_and_admin_keep_write_access(self):
        for index, user in enumerate(
            (self.user_a, self.admin_user),
            start=1,
        ):
            with self.subTest(user=user.email):
                self.authenticate_as(user)

                response = self.client.post(
                    "/api/payment-methods/",
                    {
                        "business_public_id": str(
                            self.business_a.public_id
                        ),
                        "name": f"Administrado {index}",
                        "method_type": (
                            PaymentMethod.TYPE_CARD
                        ),
                    },
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_201_CREATED,
                    msg=response.data,
                )

    def test_no_role_sees_foreign_business_method(self):
        for user in (
            self.user_a,
            self.admin_user,
            self.cashier_user,
            self.seller_user,
            self.inventory_user,
            self.viewer_user,
        ):
            with self.subTest(user=user.email):
                self.authenticate_as(user)

                response = self.client.get(
                    "/api/payment-methods/"
                    f"{self.foreign_method.public_id}/"
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )

    def test_superuser_keeps_global_access(self):
        self.authenticate_as(self.superuser)

        retrieve_response = self.client.get(
            "/api/payment-methods/"
            f"{self.foreign_method.public_id}/"
        )

        self.assertEqual(
            retrieve_response.status_code,
            status.HTTP_200_OK,
        )

        list_response = self.client.get(
            "/api/payment-methods/",
            {
                "business_public_id": str(
                    self.business_a.public_id
                ),
            },
        )

        self.assertEqual(
            get_public_ids(list_response),
            {
                str(self.active_method.public_id),
                str(self.inactive_method.public_id),
                str(self.deleted_method.public_id),
            },
        )
