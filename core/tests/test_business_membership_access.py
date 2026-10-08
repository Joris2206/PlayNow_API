from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import BusinessMembership, Employee, User
from core.services.business_memberships import (
    MembershipDomainError,
    add_existing_member,
)
from core.services import business_memberships as membership_service
from core.tests.factories import (
    create_business,
    create_employee,
    create_membership,
    create_role_user,
    create_status,
    create_user,
)
from core.views import BusinessMembershipViewSet


class BusinessMembershipAccessAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = create_status("Activo")
        cls.inactive_status = create_status("Inactivo")
        cls.owner = create_user(
            email="phase3-owner@playnow.test",
            full_name="Owner Principal",
        )
        cls.business = create_business(
            user=cls.owner,
            status=cls.active,
            business_name="Phase 3 Business",
        )
        cls.owner_membership = BusinessMembership.objects.get(
            business=cls.business,
            user=cls.owner,
        )
        cls.admin, cls.admin_employee, cls.admin_membership = create_role_user(
            business=cls.business,
            role=BusinessMembership.ROLE_ADMIN,
            status=cls.active,
            email="phase3-admin@playnow.test",
            full_name="Ana Administradora",
            position="Gerente",
        )
        role_members = {}
        for role in (
            BusinessMembership.ROLE_CASHIER,
            BusinessMembership.ROLE_SELLER,
            BusinessMembership.ROLE_INVENTORY,
            BusinessMembership.ROLE_VIEWER,
        ):
            role_members[role] = create_role_user(
                business=cls.business,
                role=role,
                status=cls.active,
                email=f"phase3-{role}@playnow.test",
            )
        cls.role_members = role_members
        cls.platform = create_user(
            email="phase3-platform@playnow.test",
            is_superuser=True,
        )
        cls.outsider = create_user(email="phase3-outsider@playnow.test")
        cls.foreign_business = create_business(
            user=cls.outsider,
            status=cls.active,
            business_name="Foreign Business",
        )

    def list_url(self, business=None, **params):
        business = business or self.business
        query = {"business_public_id": str(business.public_id), **params}
        return reverse("business-membership-list") + "?" + "&".join(
            f"{key}={value}" for key, value in query.items()
        )

    def members_url(self, business=None):
        return reverse(
            "business-members",
            kwargs={"public_id": (business or self.business).public_id},
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def assert_user_unchanged(self, user, expected):
        user.refresh_from_db()
        self.assertEqual(
            (
                user.email,
                user.full_name,
                user.role,
                user.is_active,
                user.is_staff,
                user.is_superuser,
                user.password,
            ),
            expected,
        )

    @staticmethod
    def user_snapshot(user):
        return (
            user.email,
            user.full_name,
            user.role,
            user.is_active,
            user.is_staff,
            user.is_superuser,
            user.password,
        )

    def test_canonical_list_is_paginated_read_only_and_nullable(self):
        memberships = list(
            BusinessMembership.objects.filter(business=self.business).order_by("pk")
        )
        shared_created_at = timezone.now()
        BusinessMembership.objects.filter(
            pk__in=[membership.pk for membership in memberships]
        ).update(created_at=shared_created_at)
        expected_ids = [str(membership.public_id) for membership in memberships]

        self.authenticate(self.owner)
        page_one = self.client.get(self.list_url(page_size=3, page=1))
        page_two = self.client.get(self.list_url(page_size=3, page=2))
        self.assertEqual(page_one.status_code, status.HTTP_200_OK)
        self.assertEqual(page_two.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(page_one.data),
            {
                "count",
                "total_pages",
                "current_page",
                "page_size",
                "next",
                "previous",
                "results",
            },
        )
        self.assertEqual(page_one.data["count"], len(memberships))
        self.assertEqual(page_one.data["total_pages"], 2)
        self.assertEqual(page_one.data["current_page"], 1)
        self.assertEqual(page_two.data["current_page"], 2)
        self.assertEqual(page_one.data["page_size"], 3)
        self.assertEqual(page_two.data["page_size"], 3)
        page_one_ids = [item["public_id"] for item in page_one.data["results"]]
        page_two_ids = [item["public_id"] for item in page_two.data["results"]]
        self.assertEqual(page_one_ids, expected_ids[:3])
        self.assertEqual(page_two_ids, expected_ids[3:])
        combined_ids = [*page_one_ids, *page_two_ids]
        self.assertEqual(combined_ids, expected_ids)
        self.assertEqual(len(combined_ids), len(set(combined_ids)))
        self.assertNotIn(str(self.foreign_business.memberships.get().public_id), combined_ids)

        owner_item = next(
            item
            for item in [*page_one.data["results"], *page_two.data["results"]]
            if item["user_email"] == self.owner.email
        )
        self.assertEqual(
            set(owner_item),
            {
                "public_id",
                "user_public_id",
                "user_email",
                "user_full_name",
                "business_public_id",
                "business_name",
                "employee_public_id",
                "employee_name",
                "employee_position",
                "employee_status_public_id",
                "employee_status_name",
                "role",
                "role_display",
                "is_active",
                "created_at",
                "updated_at",
            },
        )
        self.assertIsNone(owner_item["employee_public_id"])
        self.assertIsNone(owner_item["employee_status_public_id"])
        self.assertNotIn("id", owner_item)

    def test_list_permissions_required_parameter_and_anti_enumeration(self):
        self.assertEqual(self.client.get(self.list_url()).status_code, 401)
        for actor in (self.owner, self.admin, self.platform):
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                self.assertEqual(self.client.get(self.list_url()).status_code, 200)
        for _, (actor, _, membership) in self.role_members.items():
            with self.subTest(actor=actor.email):
                self.authenticate(actor)
                self.assertEqual(self.client.get(self.list_url()).status_code, 403)
                self.assertTrue(membership.is_active)

        self.authenticate(self.outsider)
        existing = self.client.get(self.list_url(self.business))
        missing = self.client.get(
            reverse("business-membership-list")
            + f"?business_public_id={uuid4()}"
        )
        self.assertEqual(existing.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(existing.data, missing.data)
        self.assertEqual(
            self.client.get(reverse("business-membership-list")).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                reverse("business-membership-list") + "?business_public_id=nope"
            ).status_code,
            400,
        )

    def test_inactive_manager_cannot_list(self):
        self.admin_membership.is_active = False
        self.admin_membership.save(update_fields=["is_active", "updated_at"])
        self.authenticate(self.admin)
        self.assertEqual(self.client.get(self.list_url()).status_code, 404)

    def test_search_filters_and_safe_ordering_stay_inside_business(self):
        seller, seller_employee, seller_membership = self.role_members[
            BusinessMembership.ROLE_SELLER
        ]
        seller_membership.is_active = False
        seller_membership.save(update_fields=["is_active", "updated_at"])
        self.admin_employee.status = self.inactive_status
        self.admin_employee.save(update_fields=["status", "updated_at"])
        self.admin.full_name = "UserFullNeedle"
        self.admin.save(update_fields=["full_name", "updated_at"])
        self.admin_employee.full_name = "EmployeeNameNeedle"
        self.admin_employee.position = "PositionNeedle"
        self.admin_employee.save(
            update_fields=["full_name", "position", "updated_at"]
        )

        self.authenticate(self.owner)
        all_ids = {
            str(public_id)
            for public_id in BusinessMembership.objects.filter(
                business=self.business
            ).values_list("public_id", flat=True)
        }

        def result_ids(**params):
            response = self.client.get(self.list_url(page_size=200, **params))
            self.assertEqual(response.status_code, 200)
            return {item["public_id"] for item in response.data["results"]}

        self.assertEqual(result_ids(), all_ids)
        self.assertEqual(result_ids(role="admin"), {str(self.admin_membership.public_id)})
        self.assertEqual(result_ids(is_active="false"), {str(seller_membership.public_id)})
        expected_active_status_ids = {
            str(membership.public_id)
            for _, (_, _, membership) in self.role_members.items()
            if membership.pk != self.admin_membership.pk
        }
        self.assertEqual(
            result_ids(employee_status_public_id=self.active.public_id),
            expected_active_status_ids,
        )
        search_cases = {
            "phase3-admin@playnow.test": {str(self.admin_membership.public_id)},
            "UserFullNeedle": {str(self.admin_membership.public_id)},
            "EmployeeNameNeedle": {str(self.admin_membership.public_id)},
            "PositionNeedle": {str(self.admin_membership.public_id)},
            "NoMatchingMembershipTerm": set(),
        }
        for term, expected in search_cases.items():
            with self.subTest(search=term):
                self.assertEqual(result_ids(search=term), expected)

        invalid_status = self.client.get(
            self.list_url(employee_status_public_id="not-a-uuid")
        )
        self.assertEqual(invalid_status.status_code, 400)

    def test_ordering_is_complete_stable_and_safe_across_pages(self):
        memberships = list(
            BusinessMembership.objects.filter(business=self.business)
            .select_related("user")
            .order_by("pk")
        )
        names = ["Bravo", "Alpha", "Alpha", "Charlie", "Bravo", "Delta"]
        for membership, full_name in zip(memberships, names, strict=True):
            User.objects.filter(pk=membership.user_id).update(full_name=full_name)

        def ordered_expected(*, descending=False):
            ordered = sorted(memberships, key=lambda membership: membership.pk)
            return [
                str(membership.public_id)
                for membership in sorted(
                    ordered,
                    key=lambda membership: names[memberships.index(membership)],
                    reverse=descending,
                )
            ]

        self.authenticate(self.owner)
        for ordering, expected in (
            ("user_full_name", ordered_expected()),
            ("-user_full_name", ordered_expected(descending=True)),
        ):
            with self.subTest(ordering=ordering):
                first = self.client.get(
                    self.list_url(ordering=ordering, page_size=3, page=1)
                )
                second = self.client.get(
                    self.list_url(ordering=ordering, page_size=3, page=2)
                )
                actual = [
                    *[item["public_id"] for item in first.data["results"]],
                    *[item["public_id"] for item in second.data["results"]],
                ]
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(set(actual)))

        shared_created_at = timezone.now()
        BusinessMembership.objects.filter(
            pk__in=[membership.pk for membership in memberships]
        ).update(created_at=shared_created_at)
        unsafe = self.client.get(
            self.list_url(ordering="user__is_superuser", page_size=20)
        )
        self.assertEqual(unsafe.status_code, 200)
        self.assertEqual(
            [item["public_id"] for item in unsafe.data["results"]],
            [str(membership.public_id) for membership in memberships],
        )

    def test_serialized_relation_query_count_is_constant(self):
        self.authenticate(self.owner)
        with CaptureQueriesContext(connection) as small:
            response = self.client.get(self.list_url(page_size=1))
            self.assertEqual(response.status_code, 200)
        with CaptureQueriesContext(connection) as large:
            response = self.client.get(self.list_url(page_size=20))
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(small), len(large))

    def test_post_creates_existing_user_without_employee_or_user_changes(self):
        target = create_user(
            email="  phase3-target@playnow.test".strip(),
            full_name="Existing Target",
            role=User.Roles.BUSINESS_OWNER,
        )
        before_users = User.objects.count()
        before_employees = Employee.objects.count()
        self.authenticate(self.owner)
        response = self.client.post(
            self.members_url(),
            {"email": "  PHASE3-TARGET@PLAYNOW.TEST  ", "role": "admin"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        membership = BusinessMembership.objects.get(
            business=self.business,
            user=target,
        )
        self.assertEqual(membership.role, "admin")
        self.assertTrue(membership.is_active)
        self.assertIsNone(membership.employee_id)
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(Employee.objects.count(), before_employees)
        target.refresh_from_db()
        self.assertEqual(target.role, User.Roles.BUSINESS_OWNER)

    def test_post_reactivates_non_owner_and_preserves_employee(self):
        target, employee, membership = self.role_members[
            BusinessMembership.ROLE_SELLER
        ]
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
        membership_pk = membership.pk
        before_count = BusinessMembership.objects.filter(
            business=self.business,
            user=target,
        ).count()
        self.authenticate(self.owner)
        response = self.client.post(
            self.members_url(),
            {"email": target.email, "role": "viewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.pk, membership_pk)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, "viewer")
        self.assertEqual(membership.employee_id, employee.pk)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                user=target,
            ).count(),
            before_count,
        )

    def test_post_role_matrix_conflicts_and_strict_payload(self):
        candidate = create_user(email="phase3-candidate@playnow.test")
        candidate_snapshot = self.user_snapshot(candidate)
        before_memberships = BusinessMembership.objects.count()
        before_employees = Employee.objects.count()
        self.authenticate(self.admin)
        self.assertEqual(
            self.client.post(
                self.members_url(),
                {"email": candidate.email, "role": "admin"},
                format="json",
            ).status_code,
            403,
        )
        self.assert_user_unchanged(candidate, candidate_snapshot)
        self.assertEqual(BusinessMembership.objects.count(), before_memberships)
        self.assertEqual(Employee.objects.count(), before_employees)
        self.assertEqual(
            self.client.post(
                self.members_url(),
                {"email": candidate.email, "role": "cashier"},
                format="json",
            ).status_code,
            201,
        )
        active_membership = BusinessMembership.objects.get(
            business=self.business,
            user=candidate,
        )
        active_snapshot = (
            active_membership.pk,
            active_membership.role,
            active_membership.is_active,
            active_membership.employee_id,
        )
        membership_count = BusinessMembership.objects.count()
        self.assertEqual(
            self.client.post(
                self.members_url(),
                {"email": candidate.email, "role": "viewer"},
                format="json",
            ).status_code,
            409,
        )
        active_membership.refresh_from_db()
        self.assertEqual(
            (
                active_membership.pk,
                active_membership.role,
                active_membership.is_active,
                active_membership.employee_id,
            ),
            active_snapshot,
        )
        self.assert_user_unchanged(candidate, candidate_snapshot)
        self.assertEqual(BusinessMembership.objects.count(), membership_count)
        self.assertEqual(Employee.objects.count(), before_employees)
        self.authenticate(self.owner)
        for payload in (
            {"email": candidate.email, "role": "owner"},
            {"email": candidate.email, "role": "viewer", "is_superuser": True},
            {"email": candidate.email, "role": "viewer", "user_public_id": uuid4()},
        ):
            with self.subTest(payload=payload):
                expected_membership = (
                    active_membership.pk,
                    active_membership.role,
                    active_membership.is_active,
                    active_membership.employee_id,
                )
                self.assertEqual(
                    self.client.post(self.members_url(), payload, format="json").status_code,
                    400,
                )
                active_membership.refresh_from_db()
                self.assertEqual(
                    (
                        active_membership.pk,
                        active_membership.role,
                        active_membership.is_active,
                        active_membership.employee_id,
                    ),
                    expected_membership,
                )
                self.assert_user_unchanged(candidate, candidate_snapshot)
                self.assertEqual(BusinessMembership.objects.count(), membership_count)
                self.assertEqual(Employee.objects.count(), before_employees)

        for role, (actor, _, _) in self.role_members.items():
            with self.subTest(role=role):
                other = create_user(email=f"phase3-denied-{role}@playnow.test")
                other_snapshot = self.user_snapshot(other)
                membership_count = BusinessMembership.objects.count()
                employee_count = Employee.objects.count()
                self.authenticate(actor)
                existing = self.client.post(
                    self.members_url(),
                    {"email": other.email, "role": "viewer"},
                    format="json",
                )
                missing = self.client.post(
                    self.members_url(),
                    {
                        "email": f"phase3-denied-missing-{role}@playnow.test",
                        "role": "viewer",
                    },
                    format="json",
                )
                self.assertEqual(existing.status_code, 403)
                self.assertEqual(existing.data, missing.data)
                self.assert_user_unchanged(other, other_snapshot)
                self.assertFalse(
                    BusinessMembership.objects.filter(
                        business=self.business,
                        user=other,
                    ).exists()
                )
                self.assertEqual(BusinessMembership.objects.count(), membership_count)
                self.assertEqual(Employee.objects.count(), employee_count)

    def test_complete_allowed_assignment_matrix(self):
        actor_roles = (
            (
                self.platform,
                (
                    "admin",
                    "cashier",
                    "seller",
                    "inventory",
                    "viewer",
                ),
            ),
            (
                self.owner,
                (
                    "admin",
                    "cashier",
                    "seller",
                    "inventory",
                    "viewer",
                ),
            ),
            (
                self.admin,
                ("cashier", "seller", "inventory", "viewer"),
            ),
        )
        for actor, roles in actor_roles:
            for assigned_role in roles:
                with self.subTest(actor=actor.email, assigned_role=assigned_role):
                    target = create_user(
                        email=(
                            f"phase3-allowed-{actor.pk}-{assigned_role}"
                            "@playnow.test"
                        )
                    )
                    self.authenticate(actor)
                    response = self.client.post(
                        self.members_url(),
                        {"email": target.email, "role": assigned_role},
                        format="json",
                    )
                    self.assertEqual(response.status_code, 201)
                    membership = BusinessMembership.objects.get(
                        business=self.business,
                        user=target,
                    )
                    self.assertEqual(membership.role, assigned_role)
                    self.assertTrue(membership.is_active)
                    self.assertIsNone(membership.employee_id)

        for actor, rejected_roles in (
            (self.platform, ("owner",)),
            (self.owner, ("owner",)),
            (self.admin, ("admin", "owner")),
        ):
            for rejected_role in rejected_roles:
                with self.subTest(actor=actor.email, rejected_role=rejected_role):
                    target = create_user(
                        email=(
                            f"phase3-rejected-{actor.pk}-{rejected_role}"
                            "@playnow.test"
                        )
                    )
                    target_snapshot = self.user_snapshot(target)
                    before = BusinessMembership.objects.count()
                    before_employees = Employee.objects.count()
                    self.authenticate(actor)
                    response = self.client.post(
                        self.members_url(),
                        {"email": target.email, "role": rejected_role},
                        format="json",
                    )
                    self.assertEqual(
                        response.status_code,
                        403 if actor == self.admin and rejected_role == "admin" else 400,
                    )
                    self.assertEqual(BusinessMembership.objects.count(), before)
                    self.assertEqual(Employee.objects.count(), before_employees)
                    self.assertFalse(
                        BusinessMembership.objects.filter(
                            business=self.business,
                            user=target,
                        ).exists()
                    )
                    self.assert_user_unchanged(target, target_snapshot)

    def test_platform_self_reactivation_preserves_membership_and_employee(self):
        employee = create_employee(
            business=self.business,
            status=self.active,
            full_name="Platform Employee",
        )
        membership = create_membership(
            user=self.platform,
            business=self.business,
            employee=employee,
            role="viewer",
            is_active=False,
        )
        membership_pk = membership.pk
        employee_pk = employee.pk
        self.authenticate(self.platform)
        response = self.client.post(
            self.members_url(),
            {"email": self.platform.email, "role": "seller"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.pk, membership_pk)
        self.assertEqual(membership.employee_id, employee_pk)
        self.assertEqual(membership.role, "seller")
        self.assertTrue(membership.is_active)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.platform,
            ).count(),
            1,
        )

    def test_active_and_inactive_owners_are_immutable_conflicts(self):
        inactive_owner = create_user(email="phase3-inactive-owner@playnow.test")
        employee = create_employee(
            business=self.business,
            status=self.active,
            full_name="Inactive Owner Employee",
        )
        inactive_membership = create_membership(
            user=inactive_owner,
            business=self.business,
            employee=employee,
            role="owner",
            is_active=False,
        )
        before_count = BusinessMembership.objects.count()
        snapshots = {
            membership.pk: (
                membership.role,
                membership.is_active,
                membership.employee_id,
            )
            for membership in (self.owner_membership, inactive_membership)
        }
        self.authenticate(self.platform)
        for target in (self.owner, inactive_owner):
            with self.subTest(target=target.email):
                response = self.client.post(
                    self.members_url(),
                    {"email": target.email, "role": "viewer"},
                    format="json",
                )
                self.assertEqual(response.status_code, 409)
        self.assertEqual(BusinessMembership.objects.count(), before_count)
        for membership in (self.owner_membership, inactive_membership):
            membership.refresh_from_db()
            self.assertEqual(
                (membership.role, membership.is_active, membership.employee_id),
                snapshots[membership.pk],
            )

    def test_target_errors_are_generic_and_authorization_precedes_enumeration(self):
        inactive = create_user(email="phase3-inactive-target@playnow.test")
        inactive.is_active = False
        inactive.save(update_fields=["is_active", "updated_at"])
        self.authenticate(self.owner)
        missing = self.client.post(
            self.members_url(),
            {"email": "missing-phase3@playnow.test", "role": "viewer"},
            format="json",
        )
        inactive_response = self.client.post(
            self.members_url(),
            {"email": inactive.email, "role": "viewer"},
            format="json",
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(inactive_response.status_code, 400)
        self.assertEqual(missing.data, inactive_response.data)

        self.authenticate(self.outsider)
        known = self.client.post(
            self.members_url(),
            {"email": self.admin.email, "role": "viewer"},
            format="json",
        )
        unknown = self.client.post(
            self.members_url(),
            {"email": "unknown-phase3@playnow.test", "role": "viewer"},
            format="json",
        )
        self.assertEqual(known.status_code, 404)
        self.assertEqual(known.data, unknown.data)

        self.authenticate(self.owner)
        foreign = self.client.post(
            self.members_url(self.foreign_business),
            {"email": self.admin.email, "role": "viewer"},
            format="json",
        )
        missing_business = self.client.post(
            reverse(
                "business-members",
                kwargs={"public_id": uuid4()},
            ),
            {"email": self.admin.email, "role": "viewer"},
            format="json",
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.data, missing_business.data)

    def test_post_preauthorization_uses_current_database_actor_state(self):
        def assert_hidden_for_known_and_missing(actor):
            self.authenticate(actor)
            known = self.client.post(
                self.members_url(),
                {"email": self.admin.email, "role": "viewer"},
                format="json",
            )
            missing = self.client.post(
                self.members_url(),
                {
                    "email": "phase3-stale-actor-missing@playnow.test",
                    "role": "viewer",
                },
                format="json",
            )
            self.assertEqual(known.status_code, 404)
            self.assertEqual(known.data, missing.data)

        BusinessMembership.objects.filter(pk=self.owner_membership.pk).update(
            is_active=False
        )
        assert_hidden_for_known_and_missing(self.owner)
        BusinessMembership.objects.filter(pk=self.owner_membership.pk).update(
            is_active=True
        )

        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        assert_hidden_for_known_and_missing(self.owner)
        User.objects.filter(pk=self.owner.pk).update(is_active=True)

        User.objects.filter(pk=self.platform.pk).update(
            is_superuser=False,
            is_staff=False,
        )
        assert_hidden_for_known_and_missing(self.platform)

    def test_owner_memberships_are_protected_and_platform_can_add_self(self):
        self.authenticate(self.platform)
        self.assertEqual(
            self.client.post(
                self.members_url(),
                {"email": self.owner.email, "role": "viewer"},
                format="json",
            ).status_code,
            409,
        )
        response = self.client.post(
            self.members_url(),
            {"email": self.platform.email, "role": "viewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.platform,
                role="viewer",
                employee__isnull=True,
            ).exists()
        )

    def test_legacy_members_get_remains_unpaginated_and_unchanged(self):
        self.authenticate(self.owner)
        response = self.client.get(self.members_url())
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertNotIn("results", response.data)
        self.assertEqual(
            set(response.data[0]),
            {
                "public_id",
                "user_email",
                "business_public_id",
                "business_name",
                "employee_public_id",
                "employee_name",
                "role",
                "role_display",
                "is_active",
                "created_at",
                "updated_at",
            },
        )

    def test_openapi_documents_only_the_phase_three_contract(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        canonical = schema["paths"]["/api/business-memberships/"]["get"]
        self.assertEqual(canonical["operationId"], "api_business_memberships_list")
        self.assertEqual(
            {parameter["name"] for parameter in canonical["parameters"]},
            {
                "business_public_id",
                "employee_status_public_id",
                "is_active",
                "ordering",
                "page",
                "page_size",
                "role",
                "search",
            },
        )
        business_parameter = next(
            parameter
            for parameter in canonical["parameters"]
            if parameter["name"] == "business_public_id"
        )
        self.assertTrue(business_parameter["required"])
        self.assertEqual(
            canonical["responses"]["200"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/PaginatedBusinessMembershipListList",
        )

        members_path = schema["paths"]["/api/businesses/{public_id}/members/"]
        legacy_schema = members_path["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(
            legacy_schema["$ref"],
            "#/components/schemas/PaginatedBusinessMembershipList",
        )
        create_operation = members_path["post"]
        self.assertEqual(
            set(create_operation["responses"]),
            {"200", "201", "400", "401", "403", "404", "409"},
        )
        self.assertEqual(
            create_operation["requestBody"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/ExistingBusinessMemberCreateRequest",
        )
        request_component = schema["components"]["schemas"][
            "ExistingBusinessMemberCreateRequest"
        ]
        self.assertEqual(set(request_component["properties"]), {"email", "role"})
        role_reference = request_component["properties"]["role"]["$ref"]
        role_component = schema["components"]["schemas"][role_reference.rsplit("/", 1)[1]]
        self.assertNotIn("owner", role_component["enum"])


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class BusinessMembershipAccessConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        active = create_status("Activo")
        self.owner = create_user(email="phase3-race-owner@playnow.test")
        self.business = create_business(user=self.owner, status=active)
        self.target = create_user(email="phase3-race-target@playnow.test")
        self.outsider = create_user(email="phase3-race-outsider@playnow.test")

    def concurrent_adds(self, roles):
        barrier = Barrier(len(roles))

        def worker(role):
            close_old_connections()
            try:
                actor = User.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=10)
                membership, created = add_existing_member(
                    actor=actor,
                    business_public_id=self.business.public_id,
                    email=self.target.email,
                    role=role,
                )
                return "created" if created else "reactivated", membership.role
            except MembershipDomainError as exc:
                return exc.kind, None
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(roles)) as executor:
            futures = [executor.submit(worker, role) for role in roles]
            return [future.result(timeout=20) for future in futures]

    def run_after_locked_change(
        self,
        *,
        actor,
        target,
        mutation,
        role="viewer",
    ):
        reached_business_lock = Event()
        original_lock = membership_service._lock_business_by_public_id

        def marked_lock(public_id):
            reached_business_lock.set()
            return original_lock(public_id)

        def worker():
            close_old_connections()
            try:
                locked_actor = User.objects.get(pk=actor.pk)
                membership, created = add_existing_member(
                    actor=locked_actor,
                    business_public_id=self.business.public_id,
                    email=target.email,
                    role=role,
                )
                return "created" if created else "reactivated", membership.pk
            except MembershipDomainError as exc:
                return exc.kind, exc.detail
            finally:
                close_old_connections()
                connections.close_all()

        with patch.object(
            membership_service,
            "_lock_business_by_public_id",
            marked_lock,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with transaction.atomic():
                    locked_business = type(self.business).objects.select_for_update().get(
                        pk=self.business.pk
                    )
                    memberships = list(
                        BusinessMembership.objects.select_for_update()
                        .filter(
                            business=locked_business,
                            user_id__in=[actor.pk, target.pk],
                        )
                        .order_by("pk")
                    )
                    users = list(
                        User.objects.select_for_update()
                        .filter(pk__in=sorted({actor.pk, target.pk}))
                        .order_by("pk")
                    )
                    memberships_by_user = {
                        membership.user_id: membership
                        for membership in memberships
                    }
                    users_by_id = {user.pk: user for user in users}
                    future = executor.submit(worker)
                    self.assertTrue(reached_business_lock.wait(timeout=10))
                    mutation(
                        locked_business,
                        memberships_by_user,
                        users_by_id,
                    )
                return future.result(timeout=20)

    def test_same_user_concurrent_add_has_one_create_and_one_controlled_conflict(self):
        results = self.concurrent_adds(["seller", "viewer"])
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "created"])
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.target,
            ).count(),
            1,
        )

    def test_inactive_membership_concurrent_reactivation_is_serializable(self):
        membership = create_membership(
            user=self.target,
            business=self.business,
            role="cashier",
            is_active=False,
        )
        results = self.concurrent_adds(["seller", "viewer"])
        self.assertEqual(
            sorted(result[0] for result in results),
            ["conflict", "reactivated"],
        )
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        self.assertIn(membership.role, {"seller", "viewer"})

    def test_actor_state_is_revalidated_after_waiting_for_business_lock(self):
        owner_membership = BusinessMembership.objects.get(
            business=self.business,
            user=self.owner,
        )

        def make_membership_mutation(**changes):
            def mutate(_, memberships, __):
                locked = memberships[self.owner.pk]
                for field, value in changes.items():
                    setattr(locked, field, value)
                locked.save(update_fields=[*changes, "updated_at"])
            return mutate

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=make_membership_mutation(is_active=False),
        )
        self.assertEqual(result[0], "not_found")
        owner_membership.is_active = True
        owner_membership.save(update_fields=["is_active", "updated_at"])

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=make_membership_mutation(role="viewer"),
        )
        self.assertEqual(result[0], "forbidden")
        owner_membership.role = "owner"
        owner_membership.save(update_fields=["role", "updated_at"])

        def deactivate_actor(_, __, users):
            locked = users[self.owner.pk]
            locked.is_active = False
            locked.save(update_fields=["is_active", "updated_at"])

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=deactivate_actor,
        )
        self.assertEqual(result[0], "not_found")
        User.objects.filter(pk=self.owner.pk).update(is_active=True)

        platform = create_user(
            email="phase3-race-platform@playnow.test",
            is_superuser=True,
        )

        def revoke_platform(_, __, users):
            locked = users[platform.pk]
            locked.is_superuser = False
            locked.is_staff = False
            locked.save(update_fields=["is_superuser", "is_staff", "updated_at"])

        result = self.run_after_locked_change(
            actor=platform,
            target=self.target,
            mutation=revoke_platform,
        )
        self.assertEqual(result[0], "not_found")
        self.assertFalse(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.target,
            ).exists()
        )

    def test_target_state_and_membership_are_revalidated_after_wait(self):
        def deactivate_target(_, __, users):
            locked = users[self.target.pk]
            locked.is_active = False
            locked.save(update_fields=["is_active", "updated_at"])

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=deactivate_target,
        )
        self.assertEqual(result[0], "invalid")
        User.objects.filter(pk=self.target.pk).update(is_active=True)

        original_email = self.target.email

        def change_email(_, __, users):
            locked = users[self.target.pk]
            locked.email = "phase3-race-renamed@playnow.test"
            locked.save(update_fields=["email", "updated_at"])

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=change_email,
        )
        self.assertEqual(result[0], "invalid")
        User.objects.filter(pk=self.target.pk).update(email=original_email)

        def create_active_membership(business, _, __):
            BusinessMembership.objects.create(
                business=business,
                user_id=self.target.pk,
                role="cashier",
                is_active=True,
            )

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=create_active_membership,
        )
        self.assertEqual(result[0], "conflict")
        membership = BusinessMembership.objects.get(
            business=self.business,
            user=self.target,
        )
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])

        def promote_to_owner(_, memberships, __):
            locked = memberships[self.target.pk]
            locked.role = "owner"
            locked.save(update_fields=["role", "updated_at"])

        result = self.run_after_locked_change(
            actor=self.owner,
            target=self.target,
            mutation=promote_to_owner,
        )
        self.assertEqual(result[0], "conflict")
        membership.refresh_from_db()
        self.assertEqual(membership.role, "owner")
        self.assertFalse(membership.is_active)

    def test_unauthorized_http_request_never_waits_for_locked_target(self):
        target_locked = Event()
        release_target = Event()
        request_completed = Event()
        employee = create_employee(
            business=self.business,
            status=self.business.status,
            full_name="Phase 3 Locked Target",
        )
        membership = create_membership(
            user=self.target,
            business=self.business,
            employee=employee,
            role=BusinessMembership.ROLE_SELLER,
            is_active=False,
        )
        token = RefreshToken.for_user(self.outsider).access_token

        def persisted_snapshot(instance):
            field_names = [
                field.attname for field in instance._meta.concrete_fields
            ]
            return type(instance).objects.values(*field_names).get(pk=instance.pk)

        target_snapshot = persisted_snapshot(self.target)
        employee_snapshot = persisted_snapshot(employee)
        membership_snapshot = persisted_snapshot(membership)
        before_counts = {
            "users": User.objects.count(),
            "employees": Employee.objects.count(),
            "memberships": BusinessMembership.objects.count(),
        }

        def hold_target_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=self.target.pk)
                    target_locked.set()
                    if not release_target.wait(timeout=20):
                        raise TimeoutError("Target lock was not released")
            finally:
                close_old_connections()
                connections.close_all()

        def make_requests():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                url = reverse(
                    "business-members",
                    kwargs={"public_id": self.business.public_id},
                )
                existing = client.post(
                    url,
                    {"email": self.target.email, "role": "viewer"},
                    format="json",
                )
                missing = client.post(
                    url,
                    {
                        "email": "phase3-race-missing@playnow.test",
                        "role": "viewer",
                    },
                    format="json",
                )
                return existing, missing
            finally:
                request_completed.set()
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            lock_future = executor.submit(hold_target_lock)
            self.assertTrue(target_locked.wait(timeout=10))
            request_future = executor.submit(make_requests)
            completed_while_locked = request_completed.wait(timeout=10)
            release_target.set()
            lock_future.result(timeout=20)
            existing, missing = request_future.result(timeout=20)

        self.assertTrue(
            completed_while_locked,
            "An unauthorized request waited for the target User lock.",
        )
        self.assertEqual(existing.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(existing.data, missing.data)
        self.assertEqual(persisted_snapshot(self.target), target_snapshot)
        self.assertEqual(persisted_snapshot(employee), employee_snapshot)
        self.assertEqual(persisted_snapshot(membership), membership_snapshot)
        self.assertEqual(User.objects.count(), before_counts["users"])
        self.assertEqual(Employee.objects.count(), before_counts["employees"])
        self.assertEqual(
            BusinessMembership.objects.count(),
            before_counts["memberships"],
        )
        membership.refresh_from_db()
        self.assertEqual(membership.public_id, membership_snapshot["public_id"])
        self.assertEqual(membership.user_id, self.target.pk)
        self.assertEqual(membership.business_id, self.business.pk)
        self.assertEqual(membership.role, BusinessMembership.ROLE_SELLER)
        self.assertFalse(membership.is_active)
        self.assertEqual(membership.employee_id, employee.pk)
        self.assertEqual(
            BusinessMembership.objects.filter(
                business=self.business,
                user=self.target,
            ).count(),
            1,
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)

    def test_list_uses_current_platform_privilege_not_authenticated_snapshot(self):
        token = RefreshToken.for_user(
            create_user(
                email="phase3-stale-platform@playnow.test",
                is_superuser=True,
            )
        ).access_token
        actor_id = token["user_id"]
        ready = Event()
        proceed = Event()
        original = BusinessMembershipViewSet.list

        def synchronized(view, request, *args, **kwargs):
            self.assertTrue(request.user.is_superuser)
            ready.set()
            if not proceed.wait(timeout=10):
                raise TimeoutError("List request was not released")
            return original(view, request, *args, **kwargs)

        def request_list(business_id):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                return client.get(
                    reverse("business-membership-list")
                    + f"?business_public_id={business_id}"
                )
            finally:
                close_old_connections()
                connections.close_all()

        with patch.object(BusinessMembershipViewSet, "list", synchronized):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(request_list, self.business.public_id)
                self.assertTrue(ready.wait(timeout=10))
                User.objects.filter(pk=actor_id).update(
                    is_superuser=False,
                    is_staff=False,
                )
                proceed.set()
                existing = future.result(timeout=20)
        self.assertEqual(existing.status_code, 404)

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        missing = client.get(
            reverse("business-membership-list")
            + f"?business_public_id={uuid4()}"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(existing.data, missing.data)
