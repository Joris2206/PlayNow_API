# AGENTS.md — PlayNow API

## Project purpose

PlayNow API is the Django REST backend for the PlayNow platform.

It is separate from the PlayNow Front repository.

This API manages business-scoped data including authentication, businesses, memberships, products, inventory, transactions, customers, suppliers, employees, debts, payments, cash management, commissions, notifications, reports, and related functionality.

Before making changes, inspect the existing models, serializers, viewsets, permissions, services/helpers, signals, tests, filters, and URLs relevant to the requested feature.

The current codebase is the source of truth for implementation details.

Do not redesign existing architecture unless explicitly required.

---

## Core priorities

The following are critical system properties:

1. Business isolation.
2. Correct authorization.
3. Correct inventory accounting.
4. Correct financial calculations.
5. Stable API contracts.
6. Auditability.
7. Consistent public identifiers.
8. Backward-compatible behavior unless a breaking change is explicitly approved.

Never weaken business isolation to make an endpoint easier to implement.

Never bypass permission/scoping helpers by directly exposing unrestricted querysets.

---

## Business isolation

Most domain resources belong directly or indirectly to a Business.

A user must not be able to list, retrieve, create, update, delete, reference, or associate resources belonging to another business unless explicitly authorized by the platform-level rules.

Business isolation applies not only to queryset filtering but also to related objects submitted in requests.

For example, a product must not be able to reference a category belonging to a different business.

The same principle applies wherever relevant to:

- categories
- products
- variants
- employees
- customers
- suppliers
- payment methods
- transactions
- transaction details
- debts
- debt payments
- cash registers
- cash movements
- commissions
- notifications
- reminders
- stock movements
- and other business-scoped entities

When adding a relation, explicitly consider cross-business assignment attacks.

Reuse the existing BusinessScopedViewSet/business-membership infrastructure rather than implementing business checks independently in every endpoint.

---

## Business memberships and roles

Business-level access is controlled using BusinessMembership.

Current business roles are:

- owner
- admin
- cashier
- seller
- inventory
- viewer

Use the existing role constants rather than raw string literals whenever possible.

Do not confuse global Django/system privileges with business-level membership permissions.

A superuser/system administrator may have platform-level privileges according to the existing implementation.

Business users are scoped through their active memberships.

Do not introduce a new role without explicit approval.

Do not change permissions for existing roles as a side effect of unrelated work.

---

## Public identifiers

Public API resources use `public_id`.

Do not expose internal numeric database primary keys as public API identifiers.

Use `public_id` for external references and URL lookups according to the existing architecture.

For resource-specific endpoints, use the resource `public_id`.

Maintain:

`lookup_field = "public_id"`

and the corresponding URL behavior where applicable.

Serializer relationships exposed to clients should use public IDs according to the existing serializer helpers/conventions.

Do not replace public IDs with internal database IDs for convenience.

---

## API endpoint conventions

Follow the established PlayNow API contract.

General/list endpoints for business-scoped resources require `business_public_id` when applicable.

POST operations for business-scoped resources require the appropriate `business_public_id`.

PUT/PATCH/DELETE identify the resource being modified using its `public_id`.

Not every endpoint follows business scoping:

- authentication endpoints
- registration/business initialization
- `/api/me`
- platform/global resources
- other intentionally global flows

must follow their specific existing contract.

Before changing an endpoint, inspect its current serializer/viewset/filter behavior.

Do not invent new parameter names for an existing concept.

---

## API response naming

Keep relationship display names consistent.

When an API response needs a human-readable representation of a related entity, prefer the established:

`<relation>_name`

convention.

Examples:

- `status_name`
- `category_name`
- `product_name`
- `variant_type_name`
- `supplier_name`
- `customer_name`
- `employee_name`
- `payment_method_name`

Do not introduce inconsistent alternatives such as `product_title` for a relationship-display field when the project convention is `product_name`.

This does not rename the Product model's own `title` domain field.

When modifying existing response fields, consider compatibility before renaming an already-consumed field.

Do not add `business_name` to normal business-scoped resource responses merely for display.

The frontend obtains current business information through `/api/me`, and repeating the business name throughout lists/tables is unnecessary.

Business identifiers still remain where required for scoping and relationships.

---

## Serializers

Use the existing serializer helpers and mixins when appropriate.

Do not duplicate logic already provided by helpers such as public-id relationship fields, related-name fields, default status handling, or common validation mechanisms.

Relations supplied by the client must be validated against the current business when applicable.

Readable `*_name` fields are normally read-only.

Calculated fields that belong to server business logic should remain server-controlled.

Do not make server-calculated financial/inventory fields writable merely to simplify a request.

Use partial update semantics correctly.

Avoid silently accepting invalid combinations of domain fields.

---

## Status and soft deletion

Respect the existing EntityStatus and soft-delete conventions.

Do not replace logical deletion with physical deletion for resources whose current architecture uses status-based deletion.

Do not automatically reactivate, delete, cancel, or alter status-related records without following existing domain behavior.

Statuses such as deleted/cancelled/inactive/annulled must continue to be excluded where the existing business-scoping/queryset infrastructure requires it.

Do not hardcode a new status string in many locations; reuse existing mechanisms where possible.

---

## Transactions

Transaction behavior is financially and operationally sensitive.

A transaction may affect:

- transaction details
- inventory
- debts
- payments
- cash
- commissions
- audit records
- reports

Do not change transaction behavior without inspecting all associated effects.

Transaction operations that modify inventory or multiple related records should preserve atomicity.

Do not allow partially completed financial/inventory operations.

Use database transactions when the current workflow requires atomic multi-record changes.

Server-calculated totals remain authoritative.

Validate transaction details and related objects before committing effects.

---

## Inventory

Inventory changes must remain internally consistent.

Product and variant stock must not become negative unless a specific domain rule explicitly allows it.

Variant sales/purchases must affect the correct stock target.

Do not modify both base product stock and variant stock unless that is intentionally required by the current domain model.

Stock movements should represent actual inventory effects.

Automatically created stock movements must remain traceable to their originating operation where applicable.

Do not allow clients to freely alter generated inventory history when the resource is intended to be read-only/immutable.

When reversing/cancelling a transaction, ensure inventory effects are neutralized exactly once.

Avoid duplicate stock effects.

Use row locking/atomic operations where concurrency could corrupt stock.

---

## Debts and payments

Debt amounts, paid amounts, balances, and settlement status are server-controlled domain values.

A debt payment must belong to the same business as its debt and related transaction/payment method.

Do not allow paid amounts to exceed the total debt according to existing rules.

Do not create duplicate debt effects from the same transaction.

When payments modify debt balances, preserve atomicity.

Read-only debt behavior must remain read-only wherever the existing API intentionally enforces it.

---

## Cash and commissions

Cash and commission calculations must be reproducible and auditable.

Do not silently modify historical movements or settlements.

Immutable resources must remain immutable.

Do not allow the same commission settlement/payment operation to be applied more than once.

Respect existing business isolation, role restrictions, and audit behavior.

---

## Audit

Important create/update/delete/domain actions should use the existing audit infrastructure when applicable.

Do not implement parallel ad-hoc auditing if a project audit helper/service already exists.

Do not remove existing audit calls as part of unrelated refactoring.

Audit records should identify the authenticated actor and relevant resource/action.

Do not include secrets or authentication tokens in audit metadata.

---

## Authentication and security

Authentication uses the project's existing JWT implementation.

Do not weaken JWT validation, refresh behavior, throttling, CSRF/CORS/security settings, or permissions to work around development problems.

Do not place secrets in source code.

Environment-specific secrets and configuration belong in environment variables.

Never commit `.env` secrets.

Do not expose sensitive authentication information in API errors.

Use the authenticated request user for `created_by`/`updated_by` style audit fields rather than trusting client-provided user identifiers.

---

## Querysets, filtering and performance

Reuse `select_related` and `prefetch_related` where relationships are serialized and doing so avoids obvious N+1 queries.

Do not introduce broad eager-loading without checking whether it is necessary.

Apply business scoping before returning querysets.

Keep filtering, searching, ordering and pagination consistent with existing endpoint conventions.

Do not bypass pagination for large list endpoints without an explicit requirement.

---

## Models and migrations

Do not modify models without determining whether a migration is required.

When model changes require migrations, create the migration through Django's normal migration workflow.

Do not manually edit old applied migrations unless explicitly requested and the consequences are understood.

Do not delete migrations to resolve migration conflicts.

Do not modify production data as part of a schema task unless explicitly requested.

Do not run destructive database commands.

---

## Tests and validation

The backend has an automated test suite and existing tests are an important regression safety net.

When changing backend behavior:

1. Inspect relevant existing tests.
2. Update/add tests when behavior changes or a regression risk is introduced.
3. Run the smallest relevant test module first.
4. Run the complete test suite when the scope/risk justifies it.

Useful commands include:

`python manage.py check`

`python manage.py test core.tests.<relevant_module> -v 2`

and, when appropriate:

`python manage.py test -v 2`

Do not modify application behavior merely to make an incorrect test pass.

If an existing unrelated test is already failing, report it separately.

Do not delete or weaken tests to make the suite green.

---

## Dependencies

Do not add Python packages unless there is clear justification.

Prefer the existing stack.

Do not update packages or major framework versions during unrelated implementation work.

If a new package is genuinely required, explain why before adding it.

Keep dependency declarations synchronized with the project's dependency management files.

---

## Git safety

Codex may inspect repository changes using read-only commands such as:

`git status`
`git status --short`
`git diff`
`git diff --stat`
`git diff --staged`
`git log`

Do not automatically commit changes.

Do not push.

Do not pull.

Do not merge.

Do not rebase.

Do not reset.

Do not discard existing user changes.

Do not use destructive Git operations unless explicitly requested.

Before finishing a meaningful task, inspect the resulting diff and ensure unrelated files were not changed.

---

## Change discipline

Make the smallest coherent change that solves the requested problem.

Do not rewrite whole modules when a localized change is sufficient.

Do not change API contracts, field names, endpoint paths, permissions, role rules, or database structure unless required by the task.

If a requested change conflicts with an existing architectural rule, explain the conflict before implementing a broad workaround.

At the end of a task summarize:

- files changed
- behavior changed
- migrations created, if any
- tests/checks executed
- results
- remaining concerns