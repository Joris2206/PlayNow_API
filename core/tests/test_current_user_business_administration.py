from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APITestCase

from core.api.serializers.current_user import CurrentUserSerializer
from core.models import BusinessMembership
from core.tests.factories import (
    create_business,
    create_membership,
    create_status,
    create_user,
)


class CurrentUserBusinessAdministrationAccessTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.inactive = create_status("Inactivo")
        cls.deleted = create_status("Eliminado")
        cls.business_creator = create_user(
            email="me-business-creator@playnow.test"
        )

    def create_business_with_status(self, status_obj, name):
        return create_business(
            user=self.business_creator,
            status=status_obj,
            business_name=name,
            create_owner_membership=False,
        )

    def grant(self, user, *, role, status_obj, is_active=True, name):
        business = self.create_business_with_status(status_obj, name)
        membership = create_membership(
            user=user,
            business=business,
            role=role,
            is_active=is_active,
        )
        return business, membership

    def get_me(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_platform_admin_without_membership_has_access(self):
        platform_admin = create_user(
            email="me-platform-admin@playnow.test",
            is_superuser=True,
        )

        data = self.get_me(platform_admin)

        self.assertIs(data["can_access_business_administration"], True)
        self.assertEqual(data["memberships"], [])

    def test_owner_of_active_business_has_access(self):
        owner = create_user(email="me-active-owner@playnow.test")
        self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.active,
            name="Active Owner Business",
        )

        self.assertIs(
            self.get_me(owner)["can_access_business_administration"],
            True,
        )

    def test_owner_of_only_inactive_business_has_access_without_membership_context(self):
        owner = create_user(email="me-inactive-owner@playnow.test")
        self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.inactive,
            name="Inactive Owner Business",
        )

        data = self.get_me(owner)

        self.assertIs(data["can_access_business_administration"], True)
        self.assertEqual(data["memberships"], [])

    def test_owner_of_only_deleted_business_has_no_access(self):
        owner = create_user(email="me-deleted-owner@playnow.test")
        self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.deleted,
            name="Deleted Owner Business",
        )

        self.assertIs(
            self.get_me(owner)["can_access_business_administration"],
            False,
        )

    def test_admin_of_active_business_has_access(self):
        admin = create_user(email="me-active-admin@playnow.test")
        self.grant(
            admin,
            role=BusinessMembership.ROLE_ADMIN,
            status_obj=self.active,
            name="Active Admin Business",
        )

        self.assertIs(
            self.get_me(admin)["can_access_business_administration"],
            True,
        )

    def test_admin_of_only_inactive_business_has_no_access(self):
        admin = create_user(email="me-inactive-admin@playnow.test")
        self.grant(
            admin,
            role=BusinessMembership.ROLE_ADMIN,
            status_obj=self.inactive,
            name="Inactive Admin Business",
        )

        data = self.get_me(admin)

        self.assertIs(data["can_access_business_administration"], False)
        self.assertEqual(data["memberships"], [])

    def test_cashier_of_active_business_has_no_access(self):
        cashier = create_user(email="me-active-cashier@playnow.test")
        self.grant(
            cashier,
            role=BusinessMembership.ROLE_CASHIER,
            status_obj=self.active,
            name="Active Cashier Business",
        )

        self.assertIs(
            self.get_me(cashier)["can_access_business_administration"],
            False,
        )

    def test_inactive_owner_or_admin_membership_has_no_access(self):
        for role in (
            BusinessMembership.ROLE_OWNER,
            BusinessMembership.ROLE_ADMIN,
        ):
            with self.subTest(role=role):
                user = create_user(email=f"me-inactive-{role}@playnow.test")
                self.grant(
                    user,
                    role=role,
                    status_obj=self.active,
                    is_active=False,
                    name=f"Inactive {role} Membership",
                )

                self.assertIs(
                    self.get_me(user)["can_access_business_administration"],
                    False,
                )

    def test_one_valid_administrative_membership_is_sufficient(self):
        user = create_user(email="me-multiple-memberships@playnow.test")
        self.grant(
            user,
            role=BusinessMembership.ROLE_VIEWER,
            status_obj=self.active,
            name="Viewer Business",
        )
        self.grant(
            user,
            role=BusinessMembership.ROLE_ADMIN,
            status_obj=self.active,
            name="Administrative Business",
        )

        self.assertIs(
            self.get_me(user)["can_access_business_administration"],
            True,
        )

    def test_user_without_memberships_has_no_access(self):
        user = create_user(email="me-no-memberships@playnow.test")

        data = self.get_me(user)

        self.assertIs(data["can_access_business_administration"], False)
        self.assertEqual(data["memberships"], [])

    def test_inactive_and_deleted_business_memberships_remain_hidden(self):
        owner = create_user(email="me-hidden-memberships@playnow.test")
        active_business, _ = self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.active,
            name="Visible Active Business",
        )
        self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.inactive,
            name="Hidden Inactive Business",
        )
        self.grant(
            owner,
            role=BusinessMembership.ROLE_OWNER,
            status_obj=self.deleted,
            name="Hidden Deleted Business",
        )

        data = self.get_me(owner)

        self.assertEqual(
            [item["business_public_id"] for item in data["memberships"]],
            [str(active_business.public_id)],
        )
        self.assertIs(data["can_access_business_administration"], True)

    def test_openapi_exposes_read_only_boolean_without_request_body(self):
        field = CurrentUserSerializer().fields[
            "can_access_business_administration"
        ]
        self.assertTrue(field.read_only)
        self.assertEqual(field.__class__.__name__, "BooleanField")

        schema = SchemaGenerator().get_schema(request=None, public=True)
        operation = schema["paths"]["/api/me/"]["get"]
        self.assertNotIn("requestBody", operation)
        component = schema["components"]["schemas"]["CurrentUser"]
        property_schema = component["properties"][
            "can_access_business_administration"
        ]
        self.assertEqual(property_schema["type"], "boolean")
        self.assertTrue(property_schema["readOnly"])

