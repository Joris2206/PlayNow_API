from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
import uuid
from django.db.models import Q

# Opcional: Custom user manager
class UserManager(BaseUserManager):
    def create_user(self, email, full_name, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, full_name, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


class Business(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='businesses')
    business_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    currency = models.CharField(max_length=10)
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)  # <- manejo de estado
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        cur = f" · {self.currency}" if self.currency else ""
        return f"{self.business_name}{cur}"


class EntityStatus(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class ProductCategory(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=255)
    class Meta:
        unique_together = [("business", "name")]
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    
class Product(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image_url = models.TextField(blank=True)
    base_price = models.DecimalField(max_digits=12, decimal_places=2)
    base_cost = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField()
    is_visible = models.BooleanField(default=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(stock__gte=0), name="product_stock_gte_0"),
        ]
        indexes = [
            models.Index(fields=["business", "created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        cat = f" · {self.category.name}" if self.category_id else ""
        return f"{self.title}{cat}"

class ProductVariantType(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variant_types')
    name = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.product.title} - {self.name}"

class ProductVariant(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    variant_type = models.ForeignKey(ProductVariantType, on_delete=models.CASCADE, related_name='variants')
    label = models.CharField(max_length=255)
    additional_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock = models.IntegerField()
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(stock__gte=0), name="variant_stock_gte_0"),
        ]

    def __str__(self):
        prod = self.variant_type.product.title
        return f"{self.variant_type.name}: {self.label} · {prod}"
    
class Employee(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='employees')
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    role = models.CharField(max_length=100)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        biz = f" · {self.business.business_name}" if self.business_id else ""
        role = f" · {self.role}" if self.role else ""
        return f"{self.full_name}{role}{biz}"
    
class Customer(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='customers')
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        phone = f" · {self.phone}" if self.phone else ""
        return f"{self.full_name}{phone}"

class Supplier(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='suppliers')
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    status = models.ForeignKey(EntityStatus, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        phone = f" · {self.phone}" if self.phone else ""
        return f"{self.name}{phone}"
    
class PaymentMethod(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Transaction(models.Model):
    TRANSACTION_TYPES = [
        ('sale', 'Sale'),
        ('purchase', 'Purchase'),
        ('expense', 'Expense'),
    ]
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='transactions')
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    employee = models.ForeignKey('Employee', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    payment_method = models.ForeignKey('PaymentMethod', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    is_debt = models.BooleanField(default=False)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    concept = models.TextField(blank=True)
    total_value = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=100, blank=True)
    payment_status = models.CharField(max_length=50, blank=True)
    invoice_series = models.CharField(max_length=50, blank=True)
    invoice_file_url = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['business', 'invoice_series', 'invoice_number'],
                name='uniq_invoice_per_business'
            ),
            models.CheckConstraint(
                check=Q(total_value__gte=0),
                name='chk_total_value_non_negative'
            ),
        ]

    def __str__(self):
        t = self.get_type_display()
        inv = ""
        if self.invoice_series or self.invoice_number:
            inv = f" #{self.invoice_series}-{self.invoice_number}".replace("# -", "").replace("--", "-").strip()
        return f"{t}{inv} · {self.public_id}"


class TransactionDetail(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='details')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        if self.unit_price is not None and self.quantity is not None:
            self.total_price = self.unit_price * self.quantity
        super().save(*args, **kwargs)
    
    def __str__(self):
        prod = self.product.title
        qty = f"x{self.quantity}"
        return f"{prod} {qty} · Tx {self.transaction.public_id}"
    
class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('alert', 'Alert'),
    ]
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    title = models.CharField(max_length=255)
    message = models.TextField()
    type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='info')
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='notifications')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='notifications')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        kind = dict(self.NOTIFICATION_TYPES).get(self.type, self.type)
        return f"[{kind}] {self.title}"

class Reminder(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='reminders')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='reminders')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='reminders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        due = f" · due {self.due_date}" if self.due_date else ""
        return f"{self.title}{due}"
    
class Debt(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    transaction = models.ForeignKey('Transaction', on_delete=models.CASCADE, related_name='debts')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    term_months = models.IntegerField(default=0)
    due_date = models.DateField()
    is_settled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        ratio = f"{self.paid_amount}/{self.total_amount}"
        return f"Debt {self.public_id} · Tx {self.transaction.public_id} · {ratio}"


class DebtPayment(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    debt = models.ForeignKey('Debt', on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    method = models.CharField(max_length=50)
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='debt_payments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    payment_method = models.ForeignKey('PaymentMethod', on_delete=models.PROTECT, related_name='debt_payments')

    def __str__(self):
        return f"{self.amount} on {self.payment_date} · Debt {self.debt.public_id}"
    
class Budget(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='budgets')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='budgets')
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2)
    used_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"Budget {self.public_id} · {self.business.business_name} · {rng}"


class Goal(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='goals')
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='goals')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    current_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        prog = f"{self.current_amount}/{self.target_amount}"
        return f"{self.name} · {prog}"

class GoalProgress(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    goal = models.ForeignKey('Goal', on_delete=models.CASCADE, related_name='progress')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='goal_progress')
    status = models.ForeignKey('EntityStatus', on_delete=models.PROTECT)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.amount} towards {self.goal.name}"
    
class CashRegister(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='cash_registers')
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='cash_registers')
    open_time = models.DateTimeField()
    close_time = models.DateTimeField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=[('open', 'Open'), ('closed', 'Closed')])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business"], condition=models.Q(status="open"),
                                    name="one_open_register_per_business"),
        ]

    def __str__(self):
        who = f"{self.employee.full_name}" if self.employee_id else "N/A"
        return f"{self.business.business_name} · {who} · {self.status}"

class ActivityLog(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    user = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='activity_logs')
    action = models.TextField()
    entity_type = models.CharField(max_length=100)
    entity_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        who = self.user.email if self.user_id else "system"
        ent = f"{self.entity_type}#{self.entity_id}" if self.entity_type else "N/A"
        return f"{self.action} {ent} by {who}"

class StockMovement(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='stock_movements')
    variant = models.ForeignKey('ProductVariant', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    note = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20, choices=[('entry', 'Entry'), ('sale', 'Sale'), ('adjustment', 'Adjustment')])
    quantity = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    transaction_detail = models.ForeignKey(
        'TransactionDetail', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements'
    )
    
    def __str__(self):
        qty = f"{self.quantity:+d}"
        var = ""
        if self.variant_id:
            var = f" · {self.variant.variant_type.name}: {self.variant.label}"
        return f"{self.type} {qty} · {self.product.title}{var}"
    
class SalesSummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='sales_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_purchases = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"{self.business.business_name} · {rng}"


class SuppliersSummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='suppliers_summaries')
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='suppliers_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    total_transactions = models.IntegerField(default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"{self.business.business_name} · {self.supplier.name} · {rng}"

class CustomersSummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='customers_summaries')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='customers_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    total_transactions = models.IntegerField(default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"{self.business.business_name} · {self.customer.full_name} · {rng}"


class PaymentsSummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='payments_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    total_payments = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_debt_payments = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_cash_payments = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_card_payments = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"{self.business.business_name} · {rng} · payments {self.total_payments}"

class DebtsSummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='debts_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    total_debt_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_pending_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        return f"{self.business.business_name} · {rng} · pending {self.total_pending_amount}"

class InventorySummary(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='inventory_summaries')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='inventory_summaries')
    variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_summaries')
    period_start = models.DateField()
    period_end = models.DateField()
    opening_stock = models.IntegerField(default=0)
    stock_in = models.IntegerField(default=0)
    stock_out = models.IntegerField(default=0)
    adjustments = models.IntegerField(default=0)
    closing_stock = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        rng = f"{self.period_start}→{self.period_end}"
        var = ""
        if self.variant_id:
            var = f" [{self.variant.variant_type.name}: {self.variant.label}]"
        return f"{self.business.business_name} · {self.product.title}{var} · {rng}"