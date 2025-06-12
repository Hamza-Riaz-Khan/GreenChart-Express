from . import db
from flask_login import UserMixin
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=True)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50))  # admin, customer, driver
    approval_status = db.Column(db.String(20), default='approved', nullable=False)
    address = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    profile_image_filename = db.Column(db.String(255), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    wallet_balance_cents = db.Column(db.Integer, default=100000, nullable=False)
    reward_points = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

class Driver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    user = db.relationship('User', backref=db.backref('driver_profile', uselist=False))
    current_lat = db.Column(db.Float, nullable=True)
    current_lng = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(50), default='Available')  # Available, Busy, Offline
    last_location_at = db.Column(db.DateTime, nullable=True)
    shift_start = db.Column(db.String(5), default='09:00', nullable=False)
    shift_end = db.Column(db.String(5), default='18:00', nullable=False)

class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item = db.Column(db.String(100), unique=True)
    description = db.Column(db.String(300), nullable=True)
    category = db.Column(db.String(80), default='Groceries', nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    price_cents = db.Column(db.Integer, default=1000, nullable=False)
    quantity = db.Column(db.Integer, default=0)
    low_stock_threshold = db.Column(db.Integer, default=10)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('cart_items', lazy=True, cascade='all, delete-orphan'))
    product = db.relationship('Inventory')
    __table_args__ = (db.UniqueConstraint('user_id', 'inventory_id'),)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer = db.relationship('User', foreign_keys=[customer_id])

    item_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=True) # Linking to inventory item
    item_name = db.Column(db.String(100)) # Snapshot of item name
    quantity = db.Column(db.Integer, default=1)

    status = db.Column(db.String(50), default='Pending Payment')
    order_number = db.Column(db.String(30), unique=True, nullable=True)

    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=True)
    driver = db.relationship('Driver')

    # Legacy external-payment references retained for database compatibility.
    # New checkouts use PaymentTransaction and virtual balances exclusively.
    stripe_pi = db.Column(db.String(100), nullable=True)
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=True)
    amount = db.Column(db.Float, default=0.0)
    amount_cents = db.Column(db.Integer, default=0, nullable=False)
    subtotal_cents = db.Column(db.Integer, default=0, nullable=False)
    delivery_fee_cents = db.Column(db.Integer, default=0, nullable=False)
    discount_cents = db.Column(db.Integer, default=0, nullable=False)
    coupon_code = db.Column(db.String(50), nullable=True)
    reward_points_earned = db.Column(db.Integer, default=0, nullable=False)
    reward_points_redeemed = db.Column(db.Integer, default=0, nullable=False)
    currency = db.Column(db.String(3), default='usd', nullable=False)
    payment_status = db.Column(db.String(30), default='unpaid', nullable=False)
    payment_method = db.Column(db.String(50), nullable=True)
    payment_reference = db.Column(db.String(100), nullable=True)

    # Delivery snapshot. Orders retain the address used at checkout even when
    # the customer later edits their profile.
    delivery_name = db.Column(db.String(150), nullable=True)
    delivery_address = db.Column(db.String(300), nullable=True)
    delivery_city = db.Column(db.String(100), nullable=True)
    delivery_phone = db.Column(db.String(30), nullable=True)
    delivery_notes = db.Column(db.String(300), nullable=True)
    delivery_lat = db.Column(db.Float, nullable=True)
    delivery_lng = db.Column(db.Float, nullable=True)
    invoice_number = db.Column(db.String(40), unique=True, nullable=True)

    # Geolocation Info
    distance_text = db.Column(db.String(50), nullable=True)
    duration_text = db.Column(db.String(50), nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    assigned_at = db.Column(db.DateTime, nullable=True)
    dispatch_started_at = db.Column(db.DateTime, nullable=True)
    dispatch_deadline_at = db.Column(db.DateTime, nullable=True, index=True)
    assignment_method = db.Column(db.String(30), nullable=True)
    transit_started_at = db.Column(db.DateTime, nullable=True)
    route_start_lat = db.Column(db.Float, nullable=True)
    route_start_lng = db.Column(db.Float, nullable=True)
    simulation_duration_minutes = db.Column(db.Integer, default=30, nullable=False)
    route_geometry = db.Column(db.Text, nullable=True)
    route_distance_meters = db.Column(db.Integer, nullable=True)
    route_duration_seconds = db.Column(db.Integer, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)

    items = db.relationship(
        'OrderItem', back_populates='order', cascade='all, delete-orphan',
        order_by='OrderItem.id'
    )

    @property
    def item_summary(self):
        if self.items:
            return ', '.join(f'{line.item_name} x{line.quantity}' for line in self.items)
        return f'{self.item_name} x{self.quantity}' if self.item_name else 'No items'

    @property
    def estimated_driver_fee_cents(self):
        distance_km = (self.route_distance_meters or 0) / 1000
        return max(300, min(1500, round(300 + distance_km * 30)))

    @property
    def payment_status_label(self):
        return {
            'virtual_paid': 'Paid with virtual funds',
            'paid': 'Paid',
            'demo': 'Paid with virtual funds',
            'refunded': 'Refunded to virtual balance',
            'unpaid': 'Awaiting payment',
        }.get((self.payment_status or '').lower(), (self.payment_status or 'Not available').replace('_', ' ').title())

    @property
    def payment_method_label(self):
        return {
            'card': 'Credit / debit card',
            'jazzcash': 'JazzCash',
            'easypaisa': 'Easypaisa',
            'nayapay': 'NayaPay',
            'sadapay': 'SadaPay',
            'demo_wallet': 'Demo wallet',
        }.get((self.payment_method or '').lower(), (self.payment_method or 'Demo wallet').replace('_', ' ').title())

    @property
    def assignment_method_label(self):
        return {
            'manual': 'Assigned by admin',
            'reassigned': 'Reassigned by admin',
            'driver_accepted': 'Accepted by driver',
            'automatic': 'Automatically assigned',
        }.get(self.assignment_method, 'Not assigned')


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False, index=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=True)
    item_name = db.Column(db.String(100), nullable=False)
    unit_price_cents = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    order = db.relationship('Order', back_populates='items')
    inventory = db.relationship('Inventory')

    @property
    def line_total_cents(self):
        return self.unit_price_cents * self.quantity

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'))
    delivery_date = db.Column(db.String(100)) # Can keep string or upgrade to DateTime


class PaymentTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True, index=True)
    transaction_type = db.Column(db.String(20), nullable=False)  # charge, refund, topup
    method = db.Column(db.String(50), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    balance_after_cents = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('payment_transactions', lazy=True))
    order = db.relationship('Order', backref=db.backref('payment_transactions', lazy=True))

    @property
    def method_label(self):
        return {
            'card': 'Credit / debit card',
            'jazzcash': 'JazzCash',
            'easypaisa': 'Easypaisa',
            'nayapay': 'NayaPay',
            'sadapay': 'SadaPay',
            'demo_topup': 'Demo top-up',
            'admin': 'Administrator',
        }.get((self.method or '').lower(), (self.method or 'Demo payment').replace('_', ' ').title())


class ProductCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(600), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship('User')
    product = db.relationship('Inventory', backref=db.backref('reviews', lazy=True))
    order = db.relationship('Order', backref=db.backref('driver_earning', uselist=False))
    __table_args__ = (db.UniqueConstraint('user_id', 'inventory_id', 'order_id'),)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    message = db.Column(db.String(600), nullable=False)
    kind = db.Column(db.String(40), default='info', nullable=False)
    link = db.Column(db.String(300), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship('User', backref=db.backref('notifications', lazy=True))


class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    discount_type = db.Column(db.String(20), nullable=False)  # percent, fixed
    value = db.Column(db.Integer, nullable=False)  # percent or cents
    minimum_cents = db.Column(db.Integer, default=0, nullable=False)
    usage_limit = db.Column(db.Integer, nullable=True)
    usage_count = db.Column(db.Integer, default=0, nullable=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class CouponRedemption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupon.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    discount_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    coupon = db.relationship('Coupon')
    user = db.relationship('User')
    order = db.relationship('Order')


class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True, index=True)
    ticket_number = db.Column(db.String(30), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    message = db.Column(db.String(1200), nullable=False)
    status = db.Column(db.String(30), default='Open', nullable=False)
    priority = db.Column(db.String(20), default='Normal', nullable=False)
    admin_response = db.Column(db.String(1200), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship('User')
    order = db.relationship('Order')


class DriverIssue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True, index=True)
    issue_type = db.Column(db.String(60), nullable=False)
    details = db.Column(db.String(1000), nullable=False)
    status = db.Column(db.String(30), default='Open', nullable=False)
    admin_response = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)

    driver = db.relationship('Driver', backref=db.backref('issues', lazy=True))
    order = db.relationship('Order')


class DriverOfferResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False, index=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False, index=True)
    response = db.Column(db.String(20), nullable=False)  # accepted, declined
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    order = db.relationship('Order', backref=db.backref('offer_responses', lazy=True))
    driver = db.relationship('Driver', backref=db.backref('offer_responses', lazy=True))
    __table_args__ = (db.UniqueConstraint('order_id', 'driver_id'),)


class DriverEarning(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), unique=True, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    driver = db.relationship('Driver', backref=db.backref('earnings', lazy=True))
    order = db.relationship('Order')

    @property
    def distance_km(self):
        return (self.order.route_distance_meters or 0) / 1000 if self.order else 0


class OrderStatusEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False)
    note = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    order = db.relationship('Order', backref=db.backref('status_events', lazy=True, order_by='OrderStatusEvent.created_at'))


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.String(50), nullable=True)
    details = db.Column(db.String(800), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    actor = db.relationship('User')

    @property
    def action_label(self):
        labels = {
            'order.auto_assigned': 'Order automatically assigned',
            'dispatch.offer_accepted': 'Delivery offer accepted',
            'dispatch.offer_declined': 'Delivery offer declined',
            'order.cancelled': 'Order cancelled and refunded',
            'product.created': 'Product created',
            'product.updated': 'Product details updated',
            'product.status_changed': 'Product availability changed',
            'product.stock_adjusted': 'Product stock adjusted',
            'category.created': 'Category created',
            'category.renamed': 'Category renamed',
            'category.status_changed': 'Category availability changed',
            'coupon.created': 'Promotion created',
            'coupon.status_changed': 'Promotion availability changed',
            'customer.balance_adjusted': 'Customer balance adjusted',
            'customer.status_changed': 'Customer account status changed',
            'driver.approved': 'Driver application approved',
            'driver.rejected': 'Driver application declined',
            'driver.availability_updated': 'Driver availability updated',
            'driver.schedule_updated': 'Driver schedule updated',
            'driver.issue_resolved': 'Driver report resolved',
            'notification.broadcast': 'Announcement sent',
            'settings.updated': 'Application settings updated',
            'support.updated': 'Support ticket updated',
        }
        return labels.get(self.action, (self.action or 'Activity').replace('.', ' ').replace('_', ' ').title())


class AppSetting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(500), nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
