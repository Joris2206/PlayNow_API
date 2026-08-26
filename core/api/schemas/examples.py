from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiTypes



# ---------------------------------------------------------------------------
# Ejemplos de Swagger
#
# Los identificadores relacionales utilizan public_id en formato UUID.
# Los UUID definidos aquí son ejemplos y deben sustituirse por registros
# existentes al ejecutar solicitudes desde Swagger.
# ---------------------------------------------------------------------------

BUSINESS_PUBLIC_ID = "a0507c11-2617-41cd-90eb-da63917a5cdd"
CATEGORY_PUBLIC_ID = "4276b1bb-82fc-4806-9a5c-de70002e8e41"
PRODUCT_PUBLIC_ID = "7aa19d16-1915-4cfa-8658-3b967e198c70"
EMPLOYEE_PUBLIC_ID = "c9fc2d2d-148d-4ab7-9b24-a94389cf0c74"
CUSTOMER_PUBLIC_ID = "eb659af9-b488-4562-85cb-c4b4130aa607"
SUPPLIER_PUBLIC_ID = "e4069f25-0208-4094-9609-d7e40db38b27"
PAYMENT_METHOD_PUBLIC_ID = "9f98c592-fc72-4649-8c39-a1540c895737"

EMPLOYEE_PERIOD_QUERY_PARAMETERS = [
    OpenApiParameter(
        name="business_public_id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="employee_public_id",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="date_from",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
    OpenApiParameter(
        name="date_to",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=True,
    ),
]
TRANSACTION_PUBLIC_ID = "bcd85f11-e36d-4cac-94ca-b005f48843cf"
DEBT_PUBLIC_ID = "4da69052-bb85-483b-aef8-ad3d14579a49"
GOAL_PUBLIC_ID = "85af7d8e-8dc8-4616-8609-ce92df799ad6"
STATUS_PUBLIC_ID = "61823ecf-a0ec-45e3-b909-4ee8780a8246"

BUSINESS_CREATE_EXAMPLE = OpenApiExample(
    "Crear tienda",
    value={
        "business_name": "Variedades La Esperanza",
        "description": "Venta de ropa, calzado y artículos para el hogar.",
        "currency": "NIO",
    },
    request_only=True,
)

EMPLOYEE_ACCESS_EXAMPLE = OpenApiExample(
    "Crear acceso de cajero",
    value={
        "email": "carlos.mendez@example.com",
        "password": "ClaveSegura2026!",
        "full_name": "Carlos Méndez",
        "position": "Cajero principal",
        "phone": "8888-1234",
        "role": "cashier",
    },
    request_only=True,
)

MEMBERSHIP_UPDATE_EXAMPLE = OpenApiExample(
    "Cambiar rol y mantener acceso activo",
    value={
        "role": "seller",
        "is_active": True,
    },
    request_only=True,
)

CATEGORY_CREATE_EXAMPLE = OpenApiExample(
    "Crear categoría",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Calzado",
    },
    request_only=True,
)

PRODUCT_CREATE_EXAMPLE = OpenApiExample(
    "Crear producto",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "category_public_id": CATEGORY_PUBLIC_ID,
        "title": "Zapato deportivo unisex",
        "description": "Zapato cómodo para uso diario.",
        "image_url": "https://example.com/images/zapato-deportivo.jpg",
        "base_price": "850.00",
        "base_cost": "560.00",
        "stock": 24,
        "is_visible": True,
    },
    request_only=True,
)

EMPLOYEE_CREATE_EXAMPLE = OpenApiExample(
    "Registrar empleado sin acceso al sistema",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "full_name": "Ana Martínez",
        "phone": "7777-4321",
        "position": "Dependiente",
    },
    request_only=True,
)

CUSTOMER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar cliente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "full_name": "José Ramírez",
        "phone": "8666-1122",
        "email": "jose.ramirez@example.com",
    },
    request_only=True,
)

SUPPLIER_CREATE_EXAMPLE = OpenApiExample(
    "Registrar proveedor",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Distribuidora Central",
        "phone": "2255-7788",
        "email": "ventas@distribuidoracentral.example.com",
    },
    request_only=True,
)

PAYMENT_METHOD_CREATE_EXAMPLE = OpenApiExample(
    "Crear método de pago",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Transferencia bancaria",
        "method_type": "transfer",
    },
    request_only=True,
)

TRANSACTION_SALE_EXAMPLE = OpenApiExample(
    "Venta pagada",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "supplier_public_id": None,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "sale",
        "discount_percent": "5.00",
        "concept": "Venta en mostrador",
        "payment_status": "paid",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
        "details": [
            {
                "product_public_id": PRODUCT_PUBLIC_ID,
                "quantity": 2,
                "unit_price": "850.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_SALE_PENDING_EXAMPLE = OpenApiExample(
    "Venta pendiente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "type": "sale",
        "payment_status": "pending",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 2,
        }],
    },
    request_only=True,
)

TRANSACTION_SALE_PARTIAL_EXAMPLE = OpenApiExample(
    "Venta con pago inicial",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "employee_public_id": EMPLOYEE_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "sale",
        "payment_status": "partial",
        "initial_paid_amount": "75.00",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 2,
        }],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_EXAMPLE = OpenApiExample(
    "Compra de inventario",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": None,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "purchase",
        "discount_percent": "0.00",
        "concept": "Reposición semanal de inventario",
        "payment_status": "paid",
        "invoice_number": "FAC-9087",
        "invoice_series": "PROV",
        "invoice_file_url": "",
        "details": [
            {
                "product_public_id": PRODUCT_PUBLIC_ID,
                "quantity": 12,
                "unit_price": "560.00",
            }
        ],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_PENDING_EXAMPLE = OpenApiExample(
    "Compra pendiente",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "type": "purchase",
        "payment_status": "pending",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 12,
        }],
    },
    request_only=True,
)

TRANSACTION_PURCHASE_PARTIAL_EXAMPLE = OpenApiExample(
    "Compra con pago inicial",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "supplier_public_id": SUPPLIER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "purchase",
        "payment_status": "partial",
        "initial_paid_amount": "100.00",
        "details": [{
            "product_public_id": PRODUCT_PUBLIC_ID,
            "quantity": 12,
        }],
    },
    request_only=True,
)

TRANSACTION_EXPENSE_EXAMPLE = OpenApiExample(
    "Gasto general",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "customer_public_id": None,
        "supplier_public_id": None,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "type": "expense",
        "discount_percent": "0.00",
        "concept": "Pago mensual de energía eléctrica",
        "expense_amount": "2350.00",
        "payment_status": "paid",
        "invoice_number": "EN-0726",
        "invoice_series": "SERV",
        "invoice_file_url": "",
    },
    request_only=True,
)

TRANSACTION_UPDATE_EXAMPLE = OpenApiExample(
    "Actualizar datos permitidos",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "type": "sale",
        "customer_public_id": CUSTOMER_PUBLIC_ID,
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "discount_percent": "5.00",
        "concept": "Venta corregida por solicitud del cliente",
        "invoice_number": "000145",
        "invoice_series": "A",
        "invoice_file_url": "",
    },
    request_only=True,
)

DEBT_CREATE_EXAMPLE = OpenApiExample(
    "Registrar deuda",
    value={
        "transaction_public_id": TRANSACTION_PUBLIC_ID,
        "total_amount": "1700.00",
        "paid_amount": "0.00",
        "interest_rate": "0.00",
        "term_months": 1,
        "due_date": "2026-08-30",
    },
    request_only=True,
)

DEBT_PAYMENT_CREATE_EXAMPLE = OpenApiExample(
    "Abono a deuda",
    value={
        "debt_public_id": DEBT_PUBLIC_ID,
        "amount": "500.00",
        "payment_date": "2026-07-30",
        "payment_method_public_id": PAYMENT_METHOD_PUBLIC_ID,
        "transaction_public_id": None,
    },
    request_only=True,
)

NOTIFICATION_CREATE_EXAMPLE = OpenApiExample(
    "Crear notificación",
    value={
        "title": "Stock bajo",
        "message": "El zapato deportivo talla 38 tiene solo 3 unidades.",
        "type": "warning",
        "business_public_id": BUSINESS_PUBLIC_ID,
        "transaction_public_id": None,
        "is_read": False,
        "scheduled_at": None,
    },
    request_only=True,
)

REMINDER_CREATE_EXAMPLE = OpenApiExample(
    "Crear recordatorio",
    value={
        "title": "Contactar al proveedor",
        "description": "Confirmar la entrega del nuevo inventario.",
        "due_date": "2026-08-02",
        "is_completed": False,
        "business_public_id": BUSINESS_PUBLIC_ID,
        "transaction_public_id": None,
    },
    request_only=True,
)

BUDGET_CREATE_EXAMPLE = OpenApiExample(
    "Crear presupuesto mensual",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "status_public_id": STATUS_PUBLIC_ID,
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "allocated_amount": "50000.00",
        "used_amount": "0.00",
    },
    request_only=True,
)

GOAL_CREATE_EXAMPLE = OpenApiExample(
    "Crear meta de ventas",
    value={
        "business_public_id": BUSINESS_PUBLIC_ID,
        "name": "Meta de ventas de agosto",
        "description": "Alcanzar cincuenta mil córdobas en ventas.",
        "target_amount": "50000.00",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    },
    request_only=True,
)

GOAL_PROGRESS_CREATE_EXAMPLE = OpenApiExample(
    "Registrar avance de meta",
    value={
        "goal_public_id": GOAL_PUBLIC_ID,
        "amount": "3500.00",
        "transaction_public_id": TRANSACTION_PUBLIC_ID,
        "status_public_id": STATUS_PUBLIC_ID,
        "note": "Venta mayorista registrada.",
    },
    request_only=True,
)

PUBLIC_CATALOG_BUSINESS_PARAMETER = OpenApiParameter(
    name="business_public_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.QUERY,
    required=True,
    description=(
        "Public ID del negocio cuyo catálogo público se consulta."
    ),
)
