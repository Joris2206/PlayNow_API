from django.db import connection
from django.test.utils import CaptureQueriesContext
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import BusinessMembership, User
from core.tests.factories import (
    create_business,
    create_membership,
    create_status,
    create_user,
)
from core.tests.helpers import get_response_results


class BusinessCapabilitiesAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.inactive = create_status("Inactivo")
        cls.deleted = create_status("Eliminado")
        cls.platform = create_user(
            email="capabilities-platform@playnow.test",
            is_superuser=True,
        )
        cls.owner = create_user(email="capabilities-owner@playnow.test")
        cls.restricted_owner = create_user(
            email="capabilities-restricted-owner@playnow.test"
        )
        cls.admin = create_user(email="capabilities-admin@playnow.test")
        cls.cashier = create_user(email="capabilities-cashier@playnow.test")
        cls.inactive_member = create_user(
            email="capabilities-inactive-member@playnow.test"
        )
        cls.outsider = create_user(email="capabilities-outsider@playnow.test")
        cls.inactive_user = create_user(
            email="capabilities-inactive-user@playnow.test"
        )

        cls.active_business = create_business(
            user=cls.owner,
            status=cls.active,
            business_name="Capabilities Active",
        )
        cls.inactive_business = create_business(
            user=cls.owner,
            status=cls.inactive,
            business_name="Capabilities Inactive",
        )
        cls.deleted_business = create_business(
            user=cls.owner,
            status=cls.deleted,
            business_name="Capabilities Deleted",
        )
        cls.restricted_business = create_business(
            user=cls.restricted_owner,
            status=cls.active,
            business_name="Capabilities Restricted",
        )

        create_membership(
            user=cls.admin,
            business=cls.active_business,
            role=BusinessMembership.ROLE_ADMIN,
        )
        create_membership(
            user=cls.cashier,
            business=cls.active_business,
            role=BusinessMembership.ROLE_CASHIER,
        )
        create_membership(
            user=cls.inactive_member,
            business=cls.restricted_business,
            role=BusinessMembership.ROLE_OWNER,
            is_active=False,
        )
        create_membership(
            user=cls.inactive_user,
            business=cls.restricted_business,
            role=BusinessMembership.ROLE_OWNER,
        )
        User.objects.filter(pk=cls.inactive_user.pk).update(is_active=False)

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    @staticmethod
    def expected(*, edit=False, suspend=False, reactivate=False, archive=False):
        return {
            "can_edit": edit,
            "can_suspend": suspend,
            "can_reactivate": reactivate,
            "can_archive": archive,
        }

    def detail(self, business):
        return self.client.get(f"/api/businesses/{business.public_id}/")

    def listed_businesses(self):
        response = self.client.get("/api/businesses/", {"page_size": 200})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {
            row["public_id"]: row
            for row in get_response_results(response)
        }

    def test_platform_capabilities_cover_active_inactive_and_deleted(self):
        self.authenticate(self.platform)
        expected = {
            str(self.active_business.public_id): self.expected(
                edit=True,
                suspend=True,
                archive=True,
            ),
            str(self.inactive_business.public_id): self.expected(
                edit=True,
                reactivate=True,
                archive=True,
            ),
            str(self.deleted_business.public_id): self.expected(),
        }
        listing = self.listed_businesses()
        for business in (
            self.active_business,
            self.inactive_business,
            self.deleted_business,
        ):
            with self.subTest(status=business.status.name):
                public_id = str(business.public_id)
                self.assertEqual(listing[public_id]["capabilities"], expected[public_id])
                detail = self.detail(business)
                self.assertEqual(detail.status_code, status.HTTP_200_OK)
                self.assertEqual(detail.data["capabilities"], expected[public_id])

    def test_local_roles_and_visibility_use_effective_membership(self):
        cases = (
            (
                self.owner,
                {
                    str(self.active_business.public_id): self.expected(
                        edit=True,
                        suspend=True,
                    ),
                    str(self.inactive_business.public_id): self.expected(
                        edit=True,
                        reactivate=True,
                    ),
                },
            ),
            (
                self.admin,
                {
                    str(self.active_business.public_id): self.expected(edit=True),
                },
            ),
            (
                self.cashier,
                {
                    str(self.active_business.public_id): self.expected(),
                },
            ),
            (self.inactive_member, {}),
            (self.inactive_user, {}),
            (self.outsider, {}),
        )
        for actor, expected in cases:
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                listing = self.listed_businesses()
                self.assertEqual(
                    {
                        public_id: row["capabilities"]
                        for public_id, row in listing.items()
                    },
                    expected,
                )
                for public_id, capabilities in expected.items():
                    detail = self.client.get(f"/api/businesses/{public_id}/")
                    self.assertEqual(detail.status_code, status.HTTP_200_OK)
                    self.assertEqual(detail.data["capabilities"], capabilities)

    def test_true_capabilities_match_real_write_authorization(self):
        self.authenticate(self.owner)
        active_detail = self.detail(self.active_business)
        self.assertTrue(active_detail.data["capabilities"]["can_edit"])
        self.assertTrue(active_detail.data["capabilities"]["can_suspend"])
        self.assertEqual(
            self.client.patch(
                f"/api/businesses/{self.active_business.public_id}/",
                {"description": "Edited by effective owner"},
                format="json",
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(
                f"/api/businesses/{self.active_business.public_id}/suspend/",
                {},
                format="json",
            ).status_code,
            status.HTTP_200_OK,
        )

        inactive_detail = self.detail(self.inactive_business)
        self.assertTrue(inactive_detail.data["capabilities"]["can_edit"])
        self.assertTrue(inactive_detail.data["capabilities"]["can_reactivate"])
        self.assertEqual(
            self.client.patch(
                f"/api/businesses/{self.inactive_business.public_id}/",
                {"description": "Edited while suspended"},
                format="json",
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(
                f"/api/businesses/{self.inactive_business.public_id}/reactivate/",
                {},
                format="json",
            ).status_code,
            status.HTTP_200_OK,
        )

        self.authenticate(self.platform)
        archive_detail = self.detail(self.restricted_business)
        self.assertTrue(archive_detail.data["capabilities"]["can_archive"])
        self.assertEqual(
            self.client.delete(
                f"/api/businesses/{self.restricted_business.public_id}/"
            ).status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_capabilities_are_rejected_in_create_put_and_patch(self):
        self.authenticate(self.owner)
        capabilities = self.expected()
        create_response = self.client.post(
            "/api/businesses/",
            {
                "business_name": "Rejected Capabilities",
                "description": "",
                "currency": "NIO",
                "capabilities": capabilities,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("capabilities", create_response.data)

        url = f"/api/businesses/{self.active_business.public_id}/"
        put_response = self.client.put(
            url,
            {
                "business_name": self.active_business.business_name,
                "description": "",
                "currency": "NIO",
                "capabilities": capabilities,
            },
            format="json",
        )
        patch_response = self.client.patch(
            url,
            {"capabilities": capabilities},
            format="json",
        )
        for response in (put_response, patch_response):
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("capabilities", response.data)

    def test_list_query_count_is_stable_as_business_count_grows(self):
        self.authenticate(self.platform)
        with CaptureQueriesContext(connection) as initial_queries:
            response = self.client.get("/api/businesses/", {"page_size": 200})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        for number in range(6):
            create_business(
                user=self.owner,
                status=self.active,
                business_name=f"Capabilities Query {number}",
            )

        with CaptureQueriesContext(connection) as expanded_queries:
            response = self.client.get("/api/businesses/", {"page_size": 200})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(expanded_queries), len(initial_queries))

    def test_openapi_exposes_response_only_required_capabilities(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        administration = schema["components"]["schemas"][
            "BusinessAdministration"
        ]
        capabilities_property = administration["properties"]["capabilities"]
        self.assertTrue(capabilities_property["readOnly"])
        capabilities = schema["components"]["schemas"][
            "BusinessCapabilities"
        ]
        names = {
            "can_edit",
            "can_suspend",
            "can_reactivate",
            "can_archive",
        }
        self.assertEqual(set(capabilities["properties"]), names)
        self.assertEqual(set(capabilities["required"]), names)
        self.assertTrue(
            all(
                field["type"] == "boolean" and field["readOnly"]
                for field in capabilities["properties"].values()
            )
        )
        for name, component in schema["components"]["schemas"].items():
            if name.endswith("Request"):
                self.assertNotIn("capabilities", component.get("properties", {}))
