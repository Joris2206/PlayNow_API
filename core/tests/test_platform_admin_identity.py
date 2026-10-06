from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.api.serializers.auth import (
    ChangePasswordSerializer,
    PasswordResetConfirmSerializer,
    RegisterSerializer,
)
from core.api.serializers.current_user import CurrentUserSerializer
from core.models import Business, BusinessMembership, User
from core.serializers import (
    BusinessMembershipSerializer,
    BusinessMembershipUpdateSerializer,
    BusinessSerializer,
    EmployeeAccessCreateSerializer,
    UserSerializer,
)
from core.tests.factories import (
    create_business,
    create_membership,
    create_role_user,
    create_status,
    create_user,
)
from core.tests.helpers import get_response_results


class PlatformAdminCurrentUserTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.owner = create_user(
            email="platform-owner@playnow.test",
            full_name="Business Owner",
        )
        cls.business = create_business(
            user=cls.owner,
            status=cls.active_status,
            business_name="Owner Business",
        )
        cls.admin, _, cls.admin_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active_status,
            email="platform-business-admin@playnow.test",
        )
        cls.superuser = create_user(
            email="platform-admin@playnow.test",
            full_name="Platform Admin",
            is_superuser=True,
        )

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def get_me(self, user):
        self.authenticate_as(user)
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_business_owner_and_admin_are_not_platform_admins(self):
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                data = self.get_me(user)
                self.assertIs(data["is_superuser"], False)

    def test_superuser_without_memberships_has_global_identity(self):
        data = self.get_me(self.superuser)

        self.assertIs(data["is_superuser"], True)
        self.assertEqual(data["memberships"], [])

    def test_superuser_with_real_membership_keeps_it(self):
        membership = create_membership(
            user=self.superuser,
            business=self.business,
            role=BusinessMembership.ROLE_VIEWER,
        )

        data = self.get_me(self.superuser)

        self.assertIs(data["is_superuser"], True)
        self.assertEqual(len(data["memberships"]), 1)
        self.assertEqual(
            data["memberships"][0]["membership_public_id"],
            str(membership.public_id),
        )

    def test_changing_membership_role_does_not_change_platform_identity(self):
        self.admin_membership.role = BusinessMembership.ROLE_VIEWER
        self.admin_membership.save(update_fields=["role", "updated_at"])

        data = self.get_me(self.admin)

        self.assertIs(data["is_superuser"], False)
        self.assertEqual(data["memberships"][0]["role"], "viewer")
        self.admin.refresh_from_db()
        self.assertIs(self.admin.is_superuser, False)

    def test_creating_business_owner_membership_does_not_promote_user(self):
        self.authenticate_as(self.owner)

        response = self.client.post(
            "/api/businesses/",
            {
                "business_name": "Second Owner Business",
                "description": "",
                "currency": "NIO",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_business = Business.objects.get(
            public_id=response.data["public_id"]
        )
        membership = BusinessMembership.objects.get(
            user=self.owner,
            business=created_business,
        )
        self.assertEqual(membership.role, BusinessMembership.ROLE_OWNER)
        self.owner.refresh_from_db()
        self.assertIs(self.owner.is_superuser, False)

    def test_current_user_platform_flag_is_boolean_and_read_only(self):
        field = CurrentUserSerializer().fields["is_superuser"]

        self.assertTrue(field.read_only)
        self.assertEqual(field.__class__.__name__, "BooleanField")


class PlatformAdminMassAssignmentTests(APITestCase):
    sensitive_fields = {
        "is_superuser",
        "is_staff",
        "groups",
        "user_permissions",
    }

    def test_public_serializers_do_not_expose_global_privileges_as_writable(self):
        serializers = (
            RegisterSerializer(),
            ChangePasswordSerializer(),
            PasswordResetConfirmSerializer(),
            UserSerializer(),
            EmployeeAccessCreateSerializer(),
            BusinessSerializer(),
            BusinessMembershipSerializer(),
            BusinessMembershipUpdateSerializer(),
            CurrentUserSerializer(),
        )

        for serializer in serializers:
            with self.subTest(serializer=serializer.__class__.__name__):
                writable_fields = {
                    name
                    for name, field in serializer.fields.items()
                    if not field.read_only
                }
                self.assertTrue(
                    writable_fields.isdisjoint(self.sensitive_fields)
                )

    def test_registration_payload_cannot_set_global_privileges(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "payload-escalation@playnow.test",
                "full_name": "Payload Escalation",
                "password": "SecurePassword123!",
                "is_superuser": True,
                "is_staff": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="payload-escalation@playnow.test")
        self.assertIs(user.is_superuser, False)
        self.assertIs(user.is_staff, False)


class PlatformAdminBusinessIsolationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active_status = create_status("Activo")
        cls.owner_a = create_user(email="business-owner-a@playnow.test")
        cls.owner_b = create_user(email="business-owner-b@playnow.test")
        cls.business_a = create_business(
            user=cls.owner_a,
            status=cls.active_status,
            business_name="Business A",
        )
        cls.business_b = create_business(
            user=cls.owner_b,
            status=cls.active_status,
            business_name="Business B",
        )
        cls.superuser = create_user(
            email="global-business-admin@playnow.test",
            is_superuser=True,
        )

    def authenticate_as(self, user):
        self.client.force_authenticate(user=user)

    def test_anonymous_business_list_is_unauthorized(self):
        response = self.client.get("/api/businesses/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_normal_user_lists_only_active_membership_businesses(self):
        self.authenticate_as(self.owner_a)

        response = self.client.get(
            "/api/businesses/",
            {"business_public_id": str(self.business_b.public_id)},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {
            item["public_id"] for item in get_response_results(response)
        }
        self.assertEqual(returned_ids, {str(self.business_a.public_id)})

    def test_normal_user_cannot_read_or_mutate_foreign_business(self):
        self.authenticate_as(self.owner_a)
        endpoint = f"/api/businesses/{self.business_b.public_id}/"

        retrieve = self.client.get(endpoint)
        patch = self.client.patch(
            endpoint,
            {"business_name": "Compromised"},
            format="json",
        )
        delete = self.client.delete(endpoint)

        self.assertEqual(retrieve.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(patch.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(delete.status_code, status.HTTP_404_NOT_FOUND)
        self.business_b.refresh_from_db()
        self.assertEqual(self.business_b.business_name, "Business B")

    def test_superuser_without_memberships_lists_and_retrieves_globally(self):
        self.authenticate_as(self.superuser)

        listing = self.client.get("/api/businesses/", {"page_size": 200})
        detail = self.client.get(
            f"/api/businesses/{self.business_b.public_id}/"
        )

        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        returned_ids = {
            item["public_id"] for item in get_response_results(listing)
        }
        self.assertEqual(
            returned_ids,
            {
                str(self.business_a.public_id),
                str(self.business_b.public_id),
            },
        )

    def test_revoking_superuser_removes_bypass_with_existing_jwt(self):
        access_token = RefreshToken.for_user(self.superuser).access_token
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {access_token}"
        )

        before = self.client.get("/api/businesses/", {"page_size": 200})
        self.assertEqual(before.status_code, status.HTTP_200_OK)
        self.assertEqual(len(get_response_results(before)), 2)

        User.objects.filter(pk=self.superuser.pk).update(is_superuser=False)

        after = self.client.get("/api/businesses/", {"page_size": 200})

        self.assertEqual(after.status_code, status.HTTP_200_OK)
        self.assertEqual(get_response_results(after), [])
        self.superuser.refresh_from_db()
        self.assertIs(self.superuser.is_superuser, False)
