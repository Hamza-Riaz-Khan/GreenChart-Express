from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import math
import os
import re
import secrets

import requests
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from . import db, login_manager
from .models import (
    AppSetting, AuditLog, CartItem, Coupon, CouponRedemption, Delivery, Driver,
    DriverEarning, DriverIssue, DriverOfferResponse, Inventory, Notification, Order, OrderItem,
    OrderStatusEvent, PaymentTransaction, ProductCategory, Review,
    SupportTicket, User, utcnow,
)


main = Blueprint('main', __name__)
CANCELLABLE_STATUSES = ('Pending Payment', 'Paid', 'Finding Driver')
PAYMENT_METHODS = {
    'card': 'Credit / debit card',
    'jazzcash': 'JazzCash',
    'easypaisa': 'Easypaisa',
    'nayapay': 'NayaPay',
    'sadapay': 'SadaPay',
}
ORDER_STATUSES = ('Pending Payment', 'Paid', 'Finding Driver', 'Assigned', 'In Transit', 'Delivered', 'Cancelled')
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
STORE_LOCATION = (24.9324, 67.0871)
DEFAULT_SETTINGS = {
    'store_name': 'GreenCart Express',
    'store_email': 'support@greencart.example',
    'store_phone': '+92 300 0000000',
    'store_address': 'Karachi, Pakistan',
    'delivery_fee_cents': '0',
    'minimum_order_cents': '0',
    'demo_speed_multiplier': '15',
    'dispatch_offer_minutes': '2',
    'maintenance_mode': '0',
}


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@main.app_context_processor
def inject_navigation_data():
    cart_count = 0
    if current_user.is_authenticated and current_user.role == 'customer':
        cart_count = db.session.query(func.coalesce(func.sum(CartItem.quantity), 0)).filter_by(
            user_id=current_user.id,
        ).scalar()
    pending_driver_count = 0
    if current_user.is_authenticated and current_user.role == 'admin':
        pending_driver_count = User.query.filter_by(
            role='driver', approval_status='pending',
        ).count()
    driver_earnings_total = 0
    open_delivery_count = 0
    if current_user.is_authenticated and current_user.role == 'driver':
        driver = Driver.query.filter_by(user_id=current_user.id).first()
        if driver:
            driver_earnings_total = db.session.query(
                func.coalesce(func.sum(DriverEarning.amount_cents), 0),
            ).filter_by(driver_id=driver.id).scalar()
            open_delivery_count = len(open_offers_for_driver(driver))
    return {
        'cart_count': cart_count,
        'pending_driver_count': pending_driver_count,
        'unread_notification_count': Notification.query.filter_by(
            user_id=current_user.id, is_read=False,
        ).count() if current_user.is_authenticated else 0,
        'configured_store_name': setting_value('store_name'),
        'driver_earnings_total': driver_earnings_total,
        'open_delivery_count': open_delivery_count,
    }


@main.before_app_request
def enforce_maintenance_mode():
    if request.endpoint in ('static', 'main.login', 'main.logout'):
        return None
    if setting_value('maintenance_mode') == '1' and not (
        current_user.is_authenticated and current_user.role == 'admin'
    ):
        return render_template('maintenance.html'), 503
    return None


def setting_value(key, cast=str):
    row = db.session.get(AppSetting, key)
    raw = row.value if row else DEFAULT_SETTINGS.get(key, '')
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return cast(DEFAULT_SETTINGS.get(key, 0))


def set_setting(key, value):
    row = db.session.get(AppSetting, key)
    if row:
        row.value = str(value)
    else:
        db.session.add(AppSetting(key=key, value=str(value)))


def notify_user(user_id, title, message, kind='info', link=None):
    db.session.add(Notification(
        user_id=user_id, title=title, message=message, kind=kind, link=link,
    ))


def audit(action, entity_type=None, entity_id=None, details=None):
    db.session.add(AuditLog(
        actor_id=current_user.id if current_user.is_authenticated else None,
        action=action, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
    ))


def add_order_event(order, status, note=None):
    db.session.add(OrderStatusEvent(order=order, status=status, note=note))


def ensure_category_records():
    names = [row[0] for row in db.session.query(Inventory.category).distinct() if row[0]]
    existing = {category.name for category in ProductCategory.query.all()}
    for name in names:
        if name not in existing:
            db.session.add(ProductCategory(name=name))
    if set(names) - existing:
        db.session.flush()


def valid_coupon(code, subtotal_cents, user_id):
    if not code:
        return None, 0, None
    coupon = Coupon.query.filter(func.lower(Coupon.code) == code.strip().lower()).first()
    now = utcnow()
    error = None
    if not coupon or not coupon.is_active:
        error = 'That coupon is not active.'
    elif coupon.starts_at and coupon.starts_at > now:
        error = 'That coupon is not active yet.'
    elif coupon.expires_at and coupon.expires_at < now:
        error = 'That coupon has expired.'
    elif coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
        error = 'That coupon has reached its usage limit.'
    elif subtotal_cents < coupon.minimum_cents:
        error = f'This coupon requires a minimum order of ${coupon.minimum_cents / 100:,.2f}.'
    elif CouponRedemption.query.filter_by(coupon_id=coupon.id, user_id=user_id).first():
        error = 'You have already used this coupon.'
    if error:
        return coupon, 0, error
    discount = (
        subtotal_cents * min(coupon.value, 100) // 100
        if coupon.discount_type == 'percent' else min(coupon.value, subtotal_cents)
    )
    return coupon, discount, None


def phone_digits(value):
    return re.sub(r'\D', '', value or '')


def valid_phone(value):
    return 10 <= len(phone_digits(value)) <= 15


def valid_demo_payment(method, detail):
    if method not in PAYMENT_METHODS:
        return False
    digits = phone_digits(detail)
    return len(digits) == 16 if method == 'card' else 10 <= len(digits) <= 15


def haversine_meters(start_lat, start_lng, end_lat, end_lng):
    radius = 6371000
    lat1, lat2 = math.radians(start_lat), math.radians(end_lat)
    delta_lat = math.radians(end_lat - start_lat)
    delta_lng = math.radians(end_lng - start_lng)
    value = (
        math.sin(delta_lat / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def build_road_route(start_lat, start_lng, end_lat, end_lng):
    """Return road geometry, distance, and a realistic city-driving estimate."""
    direct_distance = haversine_meters(start_lat, start_lng, end_lat, end_lng)
    fallback_distance = max(500, round(direct_distance * 1.25))
    fallback_duration = max(5 * 60, round((fallback_distance / 1000) / 24 * 3600 + 4 * 60))
    fallback = (
        [[start_lat, start_lng], [end_lat, end_lng]],
        fallback_distance,
        fallback_duration,
    )
    if current_app.config.get('TESTING'):
        return fallback
    try:
        response = requests.get(
            f'https://router.project-osrm.org/route/v1/driving/'
            f'{start_lng},{start_lat};{end_lng},{end_lat}',
            params={'overview': 'full', 'geometries': 'geojson', 'steps': 'false'},
            timeout=6,
        )
        response.raise_for_status()
        route = response.json()['routes'][0]
        coordinates = [
            [float(latitude), float(longitude)]
            for longitude, latitude in route['geometry']['coordinates']
        ]
        if len(coordinates) < 2:
            return fallback
        # OSRM's raw duration can be optimistic in urban traffic, so add a
        # small handoff allowance while preserving location-based variation.
        duration_seconds = max(5 * 60, round(float(route['duration']) * 1.15 + 3 * 60))
        return coordinates, round(float(route['distance'])), duration_seconds
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        current_app.logger.warning('Road routing unavailable; using distance-based fallback')
        return fallback


def route_labels(distance_meters, duration_seconds):
    distance_km = distance_meters / 1000
    distance = f'{distance_km:.1f} km' if distance_km < 10 else f'{distance_km:.0f} km'
    minutes = max(1, math.ceil(duration_seconds / 60))
    duration = f'{minutes // 60} hr {minutes % 60} mins' if minutes >= 60 else f'{minutes} mins'
    return distance, duration


def demo_duration_minutes(duration_seconds):
    multiplier = max(1, min(60, setting_value('demo_speed_multiplier', int)))
    return max(1, min(30, math.ceil(duration_seconds / 60 / multiplier)))


def selected_category(form):
    if form.get('category_mode') == 'new':
        return form.get('new_category', '').strip()
    return form.get('category', '').strip()


def order_route_points(order, start, destination):
    try:
        points = json.loads(order.route_geometry) if order.route_geometry else []
        if len(points) >= 2:
            return [[float(point[0]), float(point[1])] for point in points]
    except (TypeError, ValueError, IndexError):
        pass
    return [[start[0], start[1]], [destination[0], destination[1]]]


def point_along_route(points, progress):
    if progress <= 0:
        return points[0]
    if progress >= 1:
        return points[-1]
    segments, total = [], 0
    for first, second in zip(points, points[1:]):
        length = haversine_meters(first[0], first[1], second[0], second[1])
        segments.append((first, second, length))
        total += length
    target, travelled = total * progress, 0
    for first, second, length in segments:
        if travelled + length >= target:
            ratio = 0 if length == 0 else (target - travelled) / length
            return [
                first[0] + (second[0] - first[0]) * ratio,
                first[1] + (second[1] - first[1]) * ratio,
            ]
        travelled += length
    return points[-1]


def remaining_route_points(points, progress):
    """Return only the untravelled polyline, beginning at the moving marker."""
    if not points or progress >= 1:
        return []
    if progress <= 0:
        return points
    lengths = [
        haversine_meters(first[0], first[1], second[0], second[1])
        for first, second in zip(points, points[1:])
    ]
    target = sum(lengths) * progress
    travelled = 0
    for index, length in enumerate(lengths):
        if travelled + length >= target:
            current = point_along_route(points, progress)
            return [current, *points[index + 1:]]
        travelled += length
    return []


def ensure_order_route(order):
    """Populate road data lazily for orders created before routing was added."""
    if order.route_geometry or order.delivery_lat is None or order.delivery_lng is None:
        return
    start_lat = order.route_start_lat if order.route_start_lat is not None else STORE_LOCATION[0]
    start_lng = order.route_start_lng if order.route_start_lng is not None else STORE_LOCATION[1]
    points, distance_meters, duration_seconds = build_road_route(
        start_lat, start_lng, order.delivery_lat, order.delivery_lng,
    )
    order.route_geometry = json.dumps(points)
    order.route_distance_meters = distance_meters
    order.route_duration_seconds = duration_seconds
    order.distance_text, order.duration_text = route_labels(distance_meters, duration_seconds)
    order.simulation_duration_minutes = demo_duration_minutes(duration_seconds)
    db.session.commit()


def generate_order_number():
    while True:
        number = f'GCX-{secrets.token_hex(4).upper()}'
        if not Order.query.filter_by(order_number=number).first():
            return number


def fit_product_image(image, size=(1200, 900)):
    """Fit a product subject prominently without cutting off any of it."""
    image = image.convert(
        'RGBA' if image.mode in ('RGBA', 'LA') or 'transparency' in image.info
        else 'RGB'
    )
    rgb = image.convert('RGB')
    width, height = rgb.size
    corners = [
        rgb.getpixel((0, 0)), rgb.getpixel((width - 1, 0)),
        rgb.getpixel((0, height - 1)), rgb.getpixel((width - 1, height - 1)),
    ]
    background = tuple(sum(pixel[channel] for pixel in corners) // 4 for channel in range(3))
    corners_are_uniform = all(
        max(abs(pixel[channel] - background[channel]) for channel in range(3)) <= 24
        for pixel in corners
    )
    background_is_neutral = max(background) - min(background) <= 28

    # Product photos commonly arrive with a large white/grey studio border.
    # Trim only a uniform neutral border so genuine full-bleed photography is
    # left untouched. A small safety margin keeps shadows and soft edges intact.
    if corners_are_uniform and background_is_neutral:
        backdrop = Image.new('RGB', rgb.size, background)
        difference = ImageChops.difference(rgb, backdrop).convert('L')
        mask = difference.point(lambda value: 255 if value > 14 else 0)
        bounds = mask.getbbox()
        if bounds:
            left, top, right, bottom = bounds
            margin = max(8, round(max(right - left, bottom - top) * 0.045))
            bounds = (
                max(0, left - margin), max(0, top - margin),
                min(width, right + margin), min(height, bottom + margin),
            )
            image = image.crop(bounds)

    inner_size = (round(size[0] * 0.92), round(size[1] * 0.92))
    # Image.thumbnail() only shrinks, so small source photos would remain tiny.
    # ImageOps.contain() scales both down and up while preserving aspect ratio.
    image = ImageOps.contain(image, inner_size, method=Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', size, (255, 255, 255, 255))
    position = ((size[0] - image.width) // 2, (size[1] - image.height) // 2)
    if image.mode == 'RGBA':
        canvas.alpha_composite(image, position)
    else:
        canvas.paste(image, position)
    return canvas.convert('RGB')


def save_resized_image(
    file_storage, upload_folder, size, error_message, token_bytes=12,
    fit_mode='cover',
):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if extension not in ALLOWED_IMAGE_EXTENSIONS or not (file_storage.mimetype or '').startswith('image/'):
        raise ValueError(error_message)
    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
        image.load()
        image = image.convert('RGBA' if image.mode in ('RGBA', 'LA') or 'transparency' in image.info else 'RGB')
        if fit_mode == 'contain':
            image = fit_product_image(image, size)
        else:
            image = ImageOps.fit(
                image, size, method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValueError(error_message)
    stored_name = f'{secrets.token_hex(token_bytes)}.webp'
    os.makedirs(upload_folder, exist_ok=True)
    image.save(os.path.join(upload_folder, stored_name), 'WEBP', quality=88, method=6)
    return stored_name


def remove_uploaded_image(upload_folder, stored_name):
    if not stored_name or os.path.basename(stored_name) != stored_name:
        return
    path = os.path.join(upload_folder, stored_name)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        current_app.logger.warning('Unable to remove uploaded image %s', path)


def save_product_image(file_storage):
    return save_resized_image(
        file_storage, current_app.config['UPLOAD_FOLDER'], (1200, 900),
        'Upload a valid PNG, JPG, WEBP, or GIF image.', token_bytes=8,
        fit_mode='contain',
    )


def save_profile_image(file_storage):
    return save_resized_image(
        file_storage, current_app.config['PROFILE_UPLOAD_FOLDER'], (512, 512),
        'Upload a valid PNG, JPG, WEBP, or GIF profile photo.',
    )


def clean_image_url(value):
    value = (value or '').strip()
    if value and not re.match(r'^https?://', value, re.IGNORECASE):
        raise ValueError('Image URLs must start with http:// or https://.')
    return value or None


def simulated_order_location(order):
    driver = order.driver
    destination = (order.delivery_lat, order.delivery_lng)
    if destination[0] is None or destination[1] is None:
        return (
            driver.current_lat if driver else None,
            driver.current_lng if driver else None,
            0,
        )
    if order.status == 'Delivered':
        return destination[0], destination[1], 100
    start = (
        order.route_start_lat if order.route_start_lat is not None else
        (driver.current_lat if driver and driver.current_lat is not None else STORE_LOCATION[0]),
        order.route_start_lng if order.route_start_lng is not None else
        (driver.current_lng if driver and driver.current_lng is not None else STORE_LOCATION[1]),
    )
    if order.status != 'In Transit' or not order.transit_started_at:
        return start[0], start[1], 0
    elapsed = max(0, (utcnow() - order.transit_started_at).total_seconds())
    duration = max(1, order.simulation_duration_minutes or 30) * 60
    progress = min(1, elapsed / duration)
    latitude, longitude = point_along_route(
        order_route_points(order, start, destination), progress,
    )
    if progress >= 1 and order.status == 'In Transit':
        finalize_delivery(order, driver)
        db.session.commit()
    return latitude, longitude, round(progress * 100)


def persist_legacy_session_cart():
    """Move carts created by older session-based versions into the account."""
    if not current_user.is_authenticated or current_user.role != 'customer':
        return
    raw_cart = session.pop('cart', None)
    if not raw_cart:
        return
    product_ids = [int(item_id) for item_id in raw_cart if str(item_id).isdigit()]
    products = {
        product.id: product for product in Inventory.query.filter(Inventory.id.in_(product_ids)).all()
    } if product_ids else {}
    for raw_item_id, raw_quantity in raw_cart.items():
        if not str(raw_item_id).isdigit():
            continue
        product = products.get(int(raw_item_id))
        if not product or not product.is_active or product.quantity <= 0:
            continue
        try:
            quantity = max(1, int(raw_quantity))
        except (TypeError, ValueError):
            continue
        cart_item = CartItem.query.filter_by(
            user_id=current_user.id, inventory_id=product.id,
        ).first()
        if cart_item:
            cart_item.quantity = min(product.quantity, cart_item.quantity + quantity)
        else:
            db.session.add(CartItem(
                user_id=current_user.id, inventory_id=product.id,
                quantity=min(product.quantity, quantity),
            ))
    db.session.commit()


@main.before_app_request
def migrate_legacy_cart():
    persist_legacy_session_cart()


def get_cart_lines():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return []
    lines, changed = [], False
    cart_items = CartItem.query.filter_by(user_id=current_user.id).order_by(CartItem.created_at).all()
    for cart_item in cart_items:
        product = cart_item.product
        if product and product.is_active and product.quantity > 0:
            quantity = min(max(1, cart_item.quantity), product.quantity)
            if cart_item.quantity != quantity:
                cart_item.quantity = quantity
                changed = True
            lines.append({
                'product': product,
                'quantity': quantity,
                'line_total_cents': product.price_cents * quantity,
            })
        else:
            db.session.delete(cart_item)
            changed = True
    if changed:
        db.session.commit()
    return lines


def restore_order_stock(order):
    if order.status == 'Cancelled':
        return
    for line in order.items:
        if line.inventory:
            line.inventory.quantity += line.quantity


def update_driver_availability(driver):
    if not driver:
        return
    active = Order.query.filter(
        Order.driver_id == driver.id,
        Order.status.in_(('Assigned', 'In Transit')),
    ).first()
    driver.status = 'Busy' if active else 'Available'


def driver_is_on_shift(driver, at_time=None):
    """Return whether a driver's configured local-time shift is active."""
    current = (at_time or datetime.now()).strftime('%H:%M')
    start, end = driver.shift_start or '00:00', driver.shift_end or '23:59'
    return start <= current <= end if start <= end else current >= start or current <= end


def eligible_dispatch_drivers(order=None):
    query = Driver.query.join(User).filter(
        User.role == 'driver', User.approval_status == 'approved',
        User.is_active.is_(True), Driver.status.in_(('Available', 'Busy')),
    )
    drivers = [driver for driver in query.all() if driver_is_on_shift(driver)]
    if order:
        declined = {
            response.driver_id for response in order.offer_responses
            if response.response == 'declined'
        }
        drivers = [driver for driver in drivers if driver.id not in declined]
    return drivers


def open_offers_for_driver(driver):
    if driver.status == 'Offline' or not driver_is_on_shift(driver):
        return []
    declined_order_ids = db.session.query(DriverOfferResponse.order_id).filter_by(
        driver_id=driver.id, response='declined',
    )
    return Order.query.filter(
        Order.status == 'Finding Driver', Order.driver_id.is_(None),
        ~Order.id.in_(declined_order_ids),
    ).order_by(Order.dispatch_deadline_at, Order.created_at).all()


def assignment_distance(driver, order):
    if driver.current_lat is None or driver.current_lng is None:
        return float('inf')
    destination_lat = order.delivery_lat if order.delivery_lat is not None else STORE_LOCATION[0]
    destination_lng = order.delivery_lng if order.delivery_lng is not None else STORE_LOCATION[1]
    return haversine_meters(driver.current_lat, driver.current_lng, destination_lat, destination_lng)


def assign_dispatch_order(order, driver, method, previous_driver=None):
    """Apply all assignment side effects without committing the transaction."""
    previous_driver = previous_driver or order.driver
    order.driver = driver
    order.status = 'Assigned'
    order.assigned_at = utcnow()
    order.dispatch_deadline_at = None
    order.assignment_method = method
    delivery = Delivery.query.filter_by(order_id=order.id).first()
    if delivery:
        delivery.driver_id = driver.id
    else:
        db.session.add(Delivery(
            order_id=order.id, driver_id=driver.id,
            delivery_date=datetime.now().strftime('%Y-%m-%d'),
        ))
    driver.status = 'Busy'
    db.session.flush()
    if previous_driver and previous_driver.id != driver.id:
        notify_user(
            previous_driver.user_id, 'Delivery reassigned',
            f'{order.order_number} was moved to another driver.', 'warning',
            url_for('main.driver_history'),
        )
        update_driver_availability(previous_driver)
    method_labels = {
        'manual': 'assigned by dispatch',
        'reassigned': 'reassigned by dispatch',
        'driver_accepted': 'accepted by the driver',
        'automatic': 'assigned automatically after the offer window expired',
    }
    note = f'Assigned to {driver.user.name}; {method_labels.get(method, method)}.'
    add_order_event(order, 'Assigned', note)
    notify_user(
        driver.user_id, 'New delivery assignment',
        f'{order.order_number} is ready in your route queue.', 'driver',
        url_for('main.driver_delivery_detail', order_id=order.id),
    )
    notify_user(
        order.customer_id, 'Driver assigned',
        f'{driver.user.name} was assigned to {order.order_number}.', 'delivery',
        url_for('main.order_details', order_id=order.id),
    )


def announce_dispatch_offer(order):
    drivers = eligible_dispatch_drivers(order)
    for driver in drivers:
        notify_user(
            driver.user_id, 'New delivery available',
            f'{order.order_number} is available to accept.', 'driver',
            url_for('main.available_deliveries'),
        )
    return len(drivers)


def process_expired_dispatch_offers():
    now = utcnow()
    due_orders = Order.query.filter(
        Order.status == 'Finding Driver', Order.driver_id.is_(None),
        Order.dispatch_deadline_at.isnot(None), Order.dispatch_deadline_at <= now,
    ).order_by(Order.dispatch_deadline_at).all()
    if not due_orders:
        return 0
    assigned = 0
    retry_minutes = max(1, min(60, setting_value('dispatch_offer_minutes', int)))
    for order in due_orders:
        candidates = eligible_dispatch_drivers(order)
        if not candidates:
            order.dispatch_deadline_at = now + timedelta(minutes=retry_minutes)
            for admin in User.query.filter_by(role='admin', is_active=True):
                notify_user(
                    admin.id, 'Dispatch attention required',
                    f'No eligible driver is available for {order.order_number}; automatic dispatch will retry.',
                    'warning', url_for('main.manage_drivers'),
                )
            continue
        workload = dict(db.session.query(
            Order.driver_id, func.count(Order.id),
        ).filter(Order.status.in_(('Assigned', 'In Transit'))).group_by(Order.driver_id).all())
        driver = min(
            candidates,
            key=lambda candidate: (
                workload.get(candidate.id, 0),
                assignment_distance(candidate, order), candidate.id,
            ),
        )
        assign_dispatch_order(order, driver, 'automatic')
        db.session.add(AuditLog(
            actor_id=None, action='order.auto_assigned', entity_type='order',
            entity_id=str(order.id), details=f'Driver {driver.user.name}',
        ))
        assigned += 1
    db.session.commit()
    return assigned


@main.before_app_request
def run_dispatch_automation():
    if request.endpoint != 'static':
        process_expired_dispatch_offers()


@main.route('/api/dispatch/tick')
@login_required
def dispatch_tick():
    return jsonify(ok=True, checked_at=utcnow().isoformat() + 'Z')


def finalize_delivery(order, driver):
    order.status = 'Delivered'
    order.delivered_at = utcnow()
    if driver:
        if order.delivery_lat is not None:
            driver.current_lat, driver.current_lng = order.delivery_lat, order.delivery_lng
            driver.last_location_at = utcnow()
        if not DriverEarning.query.filter_by(order_id=order.id).first():
            distance_km = (order.route_distance_meters or 0) / 1000
            db.session.add(DriverEarning(
                driver_id=driver.id, order_id=order.id,
                amount_cents=max(300, min(1500, round(300 + distance_km * 30))),
            ))
        db.session.flush()
        update_driver_availability(driver)
    add_order_event(order, 'Delivered', 'Delivery completed successfully.')
    notify_user(order.customer_id, 'Order delivered', f'{order.order_number} has been delivered. You can now review your products.', 'success', url_for('main.order_details', order_id=order.id))


def cancel_order_record(order, refund=False, force=False):
    if order.status not in CANCELLABLE_STATUSES and not (
        force and order.status not in ('Cancelled', 'Delivered')
    ):
        return False, 'This order can no longer be cancelled.'
    if refund and order.payment_status in ('paid', 'demo', 'virtual_paid'):
        customer = order.customer
        customer.wallet_balance_cents += order.amount_cents
        order.payment_status = 'refunded'
        db.session.add(PaymentTransaction(
            user_id=customer.id, order_id=order.id, transaction_type='refund',
            method=order.payment_method or 'demo_wallet',
            amount_cents=order.amount_cents,
            balance_after_cents=customer.wallet_balance_cents,
            reference=f'REF-{order.id}-{secrets.token_hex(4).upper()}',
        ))
        if order.reward_points_earned:
            customer.reward_points = max(0, customer.reward_points - order.reward_points_earned)
        if order.reward_points_redeemed:
            customer.reward_points += order.reward_points_redeemed
        redemption = CouponRedemption.query.filter_by(order_id=order.id).first()
        if redemption:
            redemption.coupon.usage_count = max(0, redemption.coupon.usage_count - 1)
            db.session.delete(redemption)
    restore_order_stock(order)
    if order.driver:
        notify_user(
            order.driver.user_id, 'Delivery cancelled',
            f'{order.order_number} was cancelled and removed from active dispatch.',
            'warning', url_for('main.driver_dashboard'),
        )
    order.status = 'Cancelled'
    order.cancelled_at = utcnow()
    if order.driver:
        db.session.flush()
        update_driver_availability(order.driver)
    add_order_event(order, 'Cancelled', 'Order cancelled and eligible virtual funds refunded.')
    notify_user(order.customer_id, 'Order cancelled', f'{order.order_number} was cancelled and ${order.amount_cents / 100:,.2f} returned to your virtual balance.', 'refund', url_for('main.order_details', order_id=order.id))
    audit('order.cancelled', 'order', order.id, f'Refund: {refund}')
    return True, 'Order cancelled successfully.'


@main.route('/')
def home():
    featured = Inventory.query.filter_by(is_active=True).filter(Inventory.quantity > 0).limit(4).all()
    return render_template('home.html', featured=featured)


@main.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        if current_user.role == 'driver' and current_user.approval_status != 'approved':
            return redirect(url_for('main.driver_application'))
        return redirect(url_for(f'main.{current_user.role}_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'customer')
        email = request.form.get('email', '').strip().lower() or None
        phone = request.form.get('phone', '').strip() or None
        if role not in ('customer', 'driver'):
            role = 'customer'
        if len(username) < 3 or len(password) < 8 or not name:
            flash('Enter your name, username, and a password of at least 8 characters.', 'danger')
            return render_template('signup.html')
        if User.query.filter_by(username=username).first():
            flash('That username is already in use.', 'danger')
            return render_template('signup.html')
        if email and User.query.filter_by(email=email).first():
            flash('That email address is already registered.', 'danger')
            return render_template('signup.html')
        if role == 'driver' and (not email or not valid_phone(phone)):
            flash('Driver applications require an email and a valid 10–15 digit phone number.', 'danger')
            return render_template('signup.html')
        user = User(
            username=username, name=name, password=generate_password_hash(password),
            role=role, email=email, phone=phone,
            approval_status='pending' if role == 'driver' else 'approved',
        )
        db.session.add(user)
        db.session.flush()
        if role == 'driver':
            for admin in User.query.filter_by(role='admin', is_active=True):
                notify_user(admin.id, 'New driver application', f'{name} submitted a driver application.', 'driver', url_for('main.driver_applications'))
        db.session.commit()
        if role == 'driver':
            flash('Application received. Sign in anytime to check the approval status.', 'success')
        else:
            flash('Your account is ready. Sign in to start shopping.', 'success')
        return redirect(url_for('main.login'))
    return render_template('signup.html')


@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'driver' and current_user.approval_status != 'approved':
            return redirect(url_for('main.driver_application'))
        return redirect(url_for(f'main.{current_user.role}_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and check_password_hash(user.password, request.form.get('password', '')):
            login_user(user, remember=bool(request.form.get('remember')))
            if user.role == 'driver' and user.approval_status != 'approved':
                return redirect(url_for('main.driver_application'))
            destinations = {
                'admin': 'main.admin_dashboard', 'driver': 'main.driver_dashboard',
                'customer': 'main.shop',
            }
            return redirect(url_for(destinations.get(user.role, 'main.home')))
        flash('The username or password is incorrect.', 'danger')
    return render_template('login.html')


@main.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.pop('cart', None)
    return redirect(url_for('main.home'))


@main.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        uploaded = None
        try:
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip().lower() or None
            phone = request.form.get('phone', '').strip() or None
            if not name:
                raise ValueError('Your display name cannot be empty.')
            if email and User.query.filter(User.email == email, User.id != current_user.id).first():
                raise ValueError('That email address is already connected to another account.')
            if phone and not valid_phone(phone):
                raise ValueError('Enter a valid phone number containing 10 to 15 digits.')
            previous_image = current_user.profile_image_filename
            uploaded = save_profile_image(request.files.get('profile_image'))
            current_user.name = name
            current_user.email = email
            current_user.phone = phone
            current_user.address = request.form.get('address', '').strip() or None
            if uploaded:
                current_user.profile_image_filename = uploaded
            elif request.form.get('remove_profile_image') == '1':
                current_user.profile_image_filename = None
            db.session.commit()
            if previous_image and (uploaded or request.form.get('remove_profile_image') == '1'):
                remove_uploaded_image(current_app.config['PROFILE_UPLOAD_FOLDER'], previous_image)
            flash('Profile details updated.', 'success')
        except ValueError as error:
            db.session.rollback()
            if uploaded:
                remove_uploaded_image(current_app.config['PROFILE_UPLOAD_FOLDER'], uploaded)
            flash(str(error), 'danger')
        return redirect(url_for('main.profile'))

    if current_user.role == 'customer':
        orders = Order.query.filter_by(customer_id=current_user.id)
        stats = {
            'primary_label': 'Orders placed', 'primary_value': orders.count(),
            'secondary_label': 'Delivered',
            'secondary_value': orders.filter_by(status='Delivered').count(),
            'tertiary_label': 'Virtual balance',
            'tertiary_value': f'{current_user.reward_points} points',
        }
    elif current_user.role == 'driver':
        driver = Driver.query.filter_by(user_id=current_user.id).first()
        stats = {
            'primary_label': 'Driver status',
            'primary_value': driver.status if driver else current_user.approval_status.title(),
            'secondary_label': 'Completed deliveries',
            'secondary_value': Order.query.filter_by(
                driver_id=driver.id if driver else None, status='Delivered',
            ).count() if driver else 0,
            'tertiary_label': 'Approval',
            'tertiary_value': current_user.approval_status.title(),
        }
    else:
        stats = {
            'primary_label': 'Total orders', 'primary_value': Order.query.count(),
            'secondary_label': 'Active products',
            'secondary_value': Inventory.query.filter_by(is_active=True).count(),
            'tertiary_label': 'Pending drivers',
            'tertiary_value': User.query.filter_by(
                role='driver', approval_status='pending',
            ).count(),
        }
    return render_template('profile.html', stats=stats)


@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'profile':
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip().lower() or None
            phone = request.form.get('phone', '').strip() or None
            if not name:
                flash('Your display name cannot be empty.', 'danger')
            elif email and User.query.filter(User.email == email, User.id != current_user.id).first():
                flash('That email address is already connected to another account.', 'danger')
            elif phone and not valid_phone(phone):
                flash('Enter a valid phone number containing 10 to 15 digits.', 'danger')
            else:
                current_user.name = name
                current_user.email = email
                current_user.phone = phone
                current_user.address = request.form.get('address', '').strip() or None
                db.session.commit()
                flash('Profile details updated.', 'success')
        elif action == 'password':
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            if not check_password_hash(current_user.password, current_password):
                flash('Your current password is incorrect.', 'danger')
            elif len(new_password) < 8:
                flash('The new password must contain at least 8 characters.', 'danger')
            else:
                current_user.password = generate_password_hash(new_password)
                db.session.commit()
                flash('Password changed successfully.', 'success')
        elif action == 'topup' and current_user.role == 'customer':
            try:
                amount_cents = int(request.form.get('amount_cents', 0))
            except ValueError:
                amount_cents = 0
            payment_method = request.form.get('payment_method', '').strip().lower()
            payment_detail = request.form.get('payment_detail', '').strip()
            if amount_cents not in (10000, 25000, 50000):
                flash('Choose one of the available demo top-up amounts.', 'danger')
                return redirect(url_for('main.settings', topup='1'))
            elif not valid_demo_payment(payment_method, payment_detail):
                flash('Select a payment method and enter valid demo payment details.', 'danger')
                return redirect(url_for('main.settings', topup='1'))
            elif request.form.get('demo_consent') != '1':
                flash('Confirm that this top-up uses a demonstration payment method.', 'danger')
                return redirect(url_for('main.settings', topup='1'))
            else:
                current_user.wallet_balance_cents += amount_cents
                db.session.add(PaymentTransaction(
                    user_id=current_user.id, transaction_type='topup',
                    method=payment_method, amount_cents=amount_cents,
                    balance_after_cents=current_user.wallet_balance_cents,
                    reference=f'TOP-{payment_method[:4].upper()}-{secrets.token_hex(5).upper()}',
                ))
                notify_user(
                    current_user.id, 'Virtual funds added',
                    f'${amount_cents / 100:,.2f} was added using {PAYMENT_METHODS[payment_method]}.',
                    'success', url_for('main.settings'),
                )
                db.session.commit()
                flash(
                    f'${amount_cents / 100:,.2f} added to your virtual balance using '
                    f'{PAYMENT_METHODS[payment_method]}.',
                    'success',
                )
        else:
            flash('That settings action is not available.', 'danger')
        return redirect(url_for('main.settings'))

    transactions = PaymentTransaction.query.filter_by(user_id=current_user.id).order_by(
        PaymentTransaction.created_at.desc()
    ).limit(12).all()
    return render_template(
        'settings.html', transactions=transactions,
        payment_methods=PAYMENT_METHODS,
    )


@main.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        abort(403)
    orders = Order.query.order_by(Order.created_at.desc()).all()
    drivers = Driver.query.all()
    inventory = Inventory.query.filter_by(is_active=True).all()
    low_stock = [item for item in inventory if item.quantity <= item.low_stock_threshold]
    revenue_cents = db.session.query(func.coalesce(func.sum(Order.amount_cents), 0)).filter(
        Order.payment_status.in_(('paid', 'demo', 'virtual_paid')), Order.status != 'Cancelled',
    ).scalar()
    return render_template(
        'admin_dashboard.html', orders=orders, drivers=drivers,
        low_stock=low_stock, revenue_cents=revenue_cents,
        active_orders=sum(o.status in ('Assigned', 'In Transit') for o in orders),
        pending_drivers=User.query.filter_by(role='driver', approval_status='pending').count(),
    )


@main.route('/admin/inventory', methods=['GET', 'POST'])
@login_required
def manage_inventory():
    if current_user.role != 'admin':
        abort(403)
    ensure_category_records()
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'create':
                price = Decimal(request.form.get('price', '0'))
                category = selected_category(request.form)
                product = Inventory(
                    item=request.form.get('item', '').strip(),
                    description=request.form.get('description', '').strip(),
                    category=category,
                    price_cents=int(price * 100),
                    quantity=max(0, int(request.form.get('quantity', 0))),
                    low_stock_threshold=max(0, int(request.form.get('threshold', 10))),
                    image_url=clean_image_url(request.form.get('image_url')),
                )
                if not product.item or not category or product.price_cents < 1:
                    raise ValueError
                product.image_filename = save_product_image(request.files.get('image'))
                if not ProductCategory.query.filter(func.lower(ProductCategory.name) == category.lower()).first():
                    db.session.add(ProductCategory(name=category))
                db.session.add(product)
                audit('product.created', 'inventory', None, product.item)
                flash(f'{product.item} was added to the catalog.', 'success')
            elif action == 'adjust':
                product = db.get_or_404(Inventory, int(request.form['inventory_id']))
                adjustment = int(request.form.get('adjustment', 0))
                if product.quantity + adjustment < 0:
                    raise ValueError
                product.quantity += adjustment
                audit('product.stock_adjusted', 'inventory', product.id, str(adjustment))
                flash(f'{product.item} stock is now {product.quantity}.', 'success')
            elif action == 'price':
                product = db.get_or_404(Inventory, int(request.form['inventory_id']))
                price = Decimal(request.form.get('price', '0'))
                if price <= 0:
                    raise ValueError
                product.price_cents = int(price * 100)
                flash(f'{product.item} price updated.', 'success')
            elif action == 'toggle':
                product = db.get_or_404(Inventory, int(request.form['inventory_id']))
                product.is_active = not product.is_active
                audit('product.status_changed', 'inventory', product.id, str(product.is_active))
                flash(f'{product.item} is now {"activated" if product.is_active else "deactivated"}.', 'info')
            else:
                raise ValueError
            db.session.commit()
        except (ValueError, InvalidOperation, KeyError, IntegrityError):
            db.session.rollback()
            flash('Check the values. Product names must be unique and amounts cannot be negative.', 'danger')
        return redirect(url_for('main.manage_inventory'))
    inventory = Inventory.query.order_by(Inventory.is_active.desc(), Inventory.item).all()
    db.session.commit()
    categories = [category.name for category in ProductCategory.query.filter_by(is_active=True).order_by(ProductCategory.name)]
    return render_template('inventory.html', inventory=inventory, categories=categories)


@main.route('/admin/inventory/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(item_id):
    if current_user.role != 'admin':
        abort(403)
    product = db.get_or_404(Inventory, item_id)
    if request.method == 'POST':
        uploaded = None
        try:
            name = request.form.get('item', '').strip()
            price = Decimal(request.form.get('price', '0'))
            quantity = int(request.form.get('quantity', 0))
            threshold = int(request.form.get('threshold', 10))
            if not name or price <= 0 or quantity < 0 or threshold < 0:
                raise ValueError
            duplicate = Inventory.query.filter(Inventory.item == name, Inventory.id != product.id).first()
            if duplicate:
                raise ValueError
            product.item = name
            product.description = request.form.get('description', '').strip()
            product.category = selected_category(request.form)
            if not product.category:
                raise ValueError
            product.price_cents = int(price * 100)
            product.quantity = quantity
            product.low_stock_threshold = threshold
            previous_image = product.image_filename
            previous_url = product.image_url
            requested_url = clean_image_url(request.form.get('image_url'))
            remove_image = request.form.get('remove_product_image') == '1'
            uploaded = save_product_image(request.files.get('image'))
            if uploaded:
                product.image_filename = uploaded
                product.image_url = None
            elif remove_image:
                product.image_filename = None
                product.image_url = None
            elif requested_url != previous_url:
                product.image_filename = None
                product.image_url = requested_url
            else:
                product.image_url = requested_url
            product.is_active = bool(request.form.get('is_active'))
            if not ProductCategory.query.filter(func.lower(ProductCategory.name) == product.category.lower()).first():
                db.session.add(ProductCategory(name=product.category))
            audit('product.updated', 'inventory', product.id, product.item)
            db.session.commit()
            if previous_image and product.image_filename != previous_image:
                remove_uploaded_image(current_app.config['UPLOAD_FOLDER'], previous_image)
            flash(f'{product.item} was updated.', 'success')
            return redirect(url_for('main.manage_inventory'))
        except (ValueError, InvalidOperation, IntegrityError):
            db.session.rollback()
            if uploaded:
                remove_uploaded_image(current_app.config['UPLOAD_FOLDER'], uploaded)
            flash('Check all product values and upload a supported image.', 'danger')
    ensure_category_records()
    db.session.commit()
    categories = [category.name for category in ProductCategory.query.filter_by(is_active=True).order_by(ProductCategory.name)]
    return render_template('edit_product.html', product=product, categories=categories)


@main.route('/admin/drivers', methods=['GET', 'POST'])
@login_required
def manage_drivers():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        if action in ('approve_driver', 'reject_driver'):
            flash('Review driver applications on the dedicated approvals page.', 'info')
            return redirect(url_for('main.driver_applications'))
        if action in ('assign', 'reassign'):
            try:
                order_id, driver_id = int(request.form['order_id']), int(request.form['driver_id'])
            except (KeyError, ValueError):
                order_id, driver_id = 0, 0
            order, driver = db.session.get(Order, order_id), db.session.get(Driver, driver_id)
            valid_order = (
                order and (
                    (action == 'assign' and order.status in ('Paid', 'Finding Driver') and order.driver_id is None) or
                    (action == 'reassign' and order.status == 'Assigned' and order.driver_id is not None)
                )
            )
            if not valid_order or not driver:
                flash('Choose an order from the correct section and a valid driver.', 'danger')
            elif driver.status == 'Offline':
                flash('An offline driver cannot receive an order.', 'danger')
            else:
                previous_driver = order.driver
                method = 'reassigned' if action == 'reassign' else 'manual'
                assign_dispatch_order(order, driver, method, previous_driver)
                audit(f'order.{action}ed', 'order', order.id, f'Driver {driver.user.name}')
                db.session.commit()
                flash(f'{order.order_number or "Order #" + str(order.id)} assigned to {driver.user.name}.', 'success')
        else:
            flash('That dispatch action is not available.', 'danger')
        return redirect(url_for('main.manage_drivers'))
    drivers = Driver.query.join(User).filter(User.approval_status == 'approved').order_by(Driver.status, Driver.id).all()
    unassigned_orders = Order.query.filter(
        Order.status.in_(('Paid', 'Finding Driver')), Order.driver_id.is_(None),
    ).order_by(Order.created_at).all()
    assigned_orders = Order.query.filter_by(status='Assigned').filter(Order.driver_id.isnot(None)).order_by(Order.assigned_at.desc()).all()
    return render_template(
        'manage_drivers.html', drivers=drivers,
        unassigned_orders=unassigned_orders, assigned_orders=assigned_orders,
    )


@main.route('/admin/driver-applications', methods=['GET', 'POST'])
@login_required
def driver_applications():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        applicant = db.session.get(User, int(request.form.get('user_id', 0)))
        if (
            action not in ('approve_driver', 'reject_driver') or not applicant or
            applicant.role != 'driver' or applicant.approval_status != 'pending'
        ):
            flash('That driver application is no longer pending.', 'danger')
        elif action == 'approve_driver':
            applicant.approval_status = 'approved'
            if not Driver.query.filter_by(user_id=applicant.id).first():
                db.session.add(Driver(user_id=applicant.id, status='Available'))
            notify_user(applicant.id, 'Driver application approved', 'Your driver workspace is now available.', 'success', url_for('main.driver_dashboard'))
            audit('driver.approved', 'user', applicant.id, applicant.name)
            db.session.commit()
            flash(f'{applicant.name} was approved as a driver.', 'success')
        else:
            applicant.approval_status = 'rejected'
            notify_user(applicant.id, 'Driver application update', 'Your driver application was not approved. You can update your profile and contact support.', 'warning', url_for('main.profile'))
            audit('driver.rejected', 'user', applicant.id, applicant.name)
            db.session.commit()
            flash(f"{applicant.name}'s application was declined.", 'info')
        return redirect(url_for('main.driver_applications'))
    applications = User.query.filter_by(
        role='driver', approval_status='pending',
    ).order_by(User.created_at).all()
    reviewed = User.query.filter(
        User.role == 'driver', User.approval_status.in_(('approved', 'rejected')),
    ).order_by(User.created_at.desc()).limit(20).all()
    return render_template(
        'driver_applications_admin.html', applications=applications, reviewed=reviewed,
    )


@main.route('/customer/dashboard')
@login_required
def customer_dashboard():
    if current_user.role != 'customer':
        abort(403)
    return redirect(url_for('main.shop'))


@main.route('/shop')
@login_required
def shop():
    if current_user.role != 'customer':
        abort(403)
    search = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    ensure_category_records()
    db.session.commit()
    active_categories = [category.name for category in ProductCategory.query.filter_by(is_active=True).all()]
    query = Inventory.query.filter_by(is_active=True).filter(
        Inventory.quantity > 0, Inventory.category.in_(active_categories),
    )
    if search:
        query = query.filter(or_(
            Inventory.item.ilike(f'%{search}%'),
            Inventory.description.ilike(f'%{search}%'),
            Inventory.category.ilike(f'%{search}%'),
        ))
    if category:
        query = query.filter(Inventory.category == category)
    inventory = query.order_by(Inventory.category, Inventory.item).all()
    categories = sorted(active_categories)
    grouped = {}
    for product in inventory:
        grouped.setdefault(product.category, []).append(product)
    return render_template(
        'shop.html', inventory=inventory, grouped=grouped, categories=categories,
        selected_category=category, search=search,
    )


@main.route('/orders')
@login_required
def orders():
    if current_user.role != 'customer':
        abort(403)
    orders = Order.query.filter_by(customer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders)


@main.route('/cart')
@login_required
def cart():
    if current_user.role != 'customer':
        abort(403)
    lines = get_cart_lines()
    return render_template('cart.html', lines=lines, total_cents=sum(l['line_total_cents'] for l in lines))


@main.route('/cart/add/<int:item_id>', methods=['POST'])
@login_required
def add_to_cart(item_id):
    if current_user.role != 'customer':
        abort(403)
    product = db.get_or_404(Inventory, item_id)
    quantity = max(1, request.form.get('quantity', 1, type=int) or 1)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not product.is_active or product.quantity < quantity:
        if is_ajax:
            return jsonify(ok=False, message='That quantity is not currently available.'), 400
        flash('That quantity is not currently available.', 'danger')
    else:
        cart_item = CartItem.query.filter_by(
            user_id=current_user.id, inventory_id=product.id,
        ).first()
        if cart_item:
            cart_item.quantity = min(cart_item.quantity + quantity, product.quantity)
        else:
            cart_item = CartItem(
                user_id=current_user.id, inventory_id=product.id,
                quantity=min(quantity, product.quantity),
            )
            db.session.add(cart_item)
        db.session.commit()
        cart_count = db.session.query(func.coalesce(func.sum(CartItem.quantity), 0)).filter_by(
            user_id=current_user.id,
        ).scalar()
        message = f'{product.item} added to your cart.'
        if is_ajax:
            return jsonify(ok=True, message=message, cart_count=cart_count)
        flash(message, 'success')
    return redirect(request.referrer or url_for('main.shop'))


@main.route('/cart/update', methods=['POST'])
@login_required
def update_cart():
    if current_user.role != 'customer':
        abort(403)
    item_id = request.form.get('item_id', type=int)
    quantity = max(0, request.form.get('quantity', 0, type=int) or 0)
    product = db.session.get(Inventory, item_id) if item_id else None
    cart_item = CartItem.query.filter_by(
        user_id=current_user.id, inventory_id=item_id,
    ).first() if item_id else None
    if quantity == 0 or not product or not product.is_active or product.quantity <= 0:
        if cart_item:
            db.session.delete(cart_item)
    elif cart_item:
        cart_item.quantity = min(quantity, product.quantity)
    else:
        db.session.add(CartItem(
            user_id=current_user.id, inventory_id=product.id,
            quantity=min(quantity, product.quantity),
        ))
    db.session.commit()
    return redirect(url_for('main.cart'))


@main.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if current_user.role != 'customer':
        abort(403)
    lines = get_cart_lines()
    if not lines:
        flash('Your cart is empty.', 'danger')
        return redirect(url_for('main.cart'))
    subtotal_cents = sum(l['line_total_cents'] for l in lines)
    delivery_fee_cents = max(0, setting_value('delivery_fee_cents', int))
    minimum_order_cents = max(0, setting_value('minimum_order_cents', int))
    coupon, coupon_discount_cents, reward_discount_cents = None, 0, 0
    total_cents = subtotal_cents + delivery_fee_cents

    def render_checkout_page():
        return render_template(
            'checkout.html', lines=lines, subtotal_cents=subtotal_cents,
            delivery_fee_cents=delivery_fee_cents,
            coupon_discount_cents=coupon_discount_cents,
            reward_discount_cents=reward_discount_cents,
            total_cents=total_cents, payment_methods=PAYMENT_METHODS,
        )

    if subtotal_cents < minimum_order_cents:
        flash(f'The minimum order is ${minimum_order_cents / 100:,.2f}.', 'danger')
        return redirect(url_for('main.cart'))
    if request.method == 'GET':
        return render_checkout_page()

    delivery_name = request.form.get('delivery_name', '').strip()
    delivery_address = request.form.get('delivery_address', '').strip()
    delivery_city = request.form.get('delivery_city', '').strip()
    delivery_phone = request.form.get('delivery_phone', '').strip()
    delivery_notes = request.form.get('delivery_notes', '').strip()
    payment_method = request.form.get('payment_method', '')
    payment_detail = request.form.get('payment_detail', '').strip()
    coupon, coupon_discount_cents, coupon_error = valid_coupon(
        request.form.get('coupon_code', ''), subtotal_cents, current_user.id,
    )
    if coupon_error:
        flash(coupon_error, 'danger')
        return render_checkout_page()
    if request.form.get('redeem_rewards') == '1':
        reward_discount_cents = min(
            current_user.reward_points,
            max(0, (subtotal_cents - coupon_discount_cents) * 20 // 100),
        )
    total_cents = max(0, subtotal_cents + delivery_fee_cents - coupon_discount_cents - reward_discount_cents)
    checkout_action = request.form.get('checkout_action', 'place_order')
    if checkout_action in ('apply_coupon', 'apply_rewards'):
        if checkout_action == 'apply_coupon':
            if coupon:
                flash(f'{coupon.code} applied. You save ${coupon_discount_cents / 100:,.2f}.', 'success')
            else:
                flash('Enter a coupon code to apply.', 'warning')
        elif reward_discount_cents:
            flash(f'{reward_discount_cents} reward points applied.', 'success')
        else:
            flash('Select reward points or earn more points before applying them.', 'warning')
        return render_checkout_page()
    try:
        delivery_lat = float(request.form.get('delivery_lat', ''))
        delivery_lng = float(request.form.get('delivery_lng', ''))
        valid_coordinates = -90 <= delivery_lat <= 90 and -180 <= delivery_lng <= 180
    except (TypeError, ValueError):
        valid_coordinates = False

    if not all((delivery_name, delivery_address, delivery_city, delivery_phone)):
        flash('Complete all required delivery details.', 'danger')
        return render_checkout_page()
    if not valid_phone(delivery_phone):
        flash('Enter a valid phone number containing 10 to 15 digits.', 'danger')
        return render_checkout_page()
    if not valid_demo_payment(payment_method, payment_detail):
        flash('Select a demo payment method and enter its requested details.', 'danger')
        return render_checkout_page()
    if not valid_coordinates:
        flash('Choose the delivery point on the checkout map.', 'danger')
        return render_checkout_page()
    if request.form.get('demo_consent') != '1':
        flash('Confirm that you understand this is a virtual transaction.', 'danger')
        return render_checkout_page()
    if current_user.wallet_balance_cents < total_cents:
        flash('Your demo balance is too low. Add virtual funds in Settings.', 'danger')
        return redirect(url_for('main.settings'))

    for line in lines:
        if line['product'].quantity < line['quantity']:
            flash(f'{line["product"].item} no longer has enough stock.', 'danger')
            return redirect(url_for('main.cart'))

    destination = f'{delivery_address}, {delivery_city}'
    route_points, route_distance, route_duration = build_road_route(
        STORE_LOCATION[0], STORE_LOCATION[1], delivery_lat, delivery_lng,
    )
    distance, duration = route_labels(route_distance, route_duration)
    current_user.wallet_balance_cents -= total_cents
    if request.form.get('save_address'):
        current_user.name = delivery_name
        current_user.address = destination
        current_user.phone = delivery_phone

    dispatch_started_at = utcnow()
    offer_minutes = max(1, min(60, setting_value('dispatch_offer_minutes', int)))
    order = Order(
        customer_id=current_user.id, status='Finding Driver', amount_cents=total_cents,
        subtotal_cents=subtotal_cents, delivery_fee_cents=delivery_fee_cents,
        discount_cents=coupon_discount_cents + reward_discount_cents,
        coupon_code=coupon.code if coupon else None,
        reward_points_redeemed=reward_discount_cents,
        reward_points_earned=max(0, (subtotal_cents - coupon_discount_cents) // 100),
        amount=total_cents / 100, payment_status='virtual_paid', paid_at=utcnow(),
        payment_method=payment_method,
        payment_reference=f'PAY-{secrets.token_hex(6).upper()}',
        delivery_name=delivery_name, delivery_address=delivery_address,
        delivery_city=delivery_city, delivery_phone=delivery_phone,
        delivery_notes=delivery_notes[:300], distance_text=distance,
        duration_text=duration, delivery_lat=delivery_lat, delivery_lng=delivery_lng,
        route_geometry=json.dumps(route_points), route_distance_meters=route_distance,
        route_duration_seconds=route_duration,
        simulation_duration_minutes=demo_duration_minutes(route_duration),
        dispatch_started_at=dispatch_started_at,
        dispatch_deadline_at=dispatch_started_at + timedelta(minutes=offer_minutes),
        order_number=generate_order_number(),
    )
    db.session.add(order)
    db.session.flush()
    order.invoice_number = f'GC-{order.created_at.year}-{order.id:06d}'
    for line in lines:
        product = line['product']
        product.quantity -= line['quantity']
        order.items.append(OrderItem(
            inventory=product, item_name=product.item,
            unit_price_cents=product.price_cents, quantity=line['quantity'],
        ))
    db.session.add(PaymentTransaction(
        user_id=current_user.id, order_id=order.id, transaction_type='charge',
        method=payment_method, amount_cents=total_cents,
        balance_after_cents=current_user.wallet_balance_cents,
        reference=order.payment_reference,
    ))
    if coupon:
        coupon.usage_count += 1
        db.session.add(CouponRedemption(
            coupon_id=coupon.id, user_id=current_user.id, order_id=order.id,
            discount_cents=coupon_discount_cents,
        ))
    current_user.reward_points = max(
        0, current_user.reward_points - reward_discount_cents + order.reward_points_earned,
    )
    add_order_event(order, 'Paid', 'Order placed and paid using virtual funds.')
    add_order_event(order, 'Finding Driver', f'Available drivers have {offer_minutes} minute(s) to accept this delivery.')
    offered_to = announce_dispatch_offer(order)
    notify_user(
        current_user.id, 'Order confirmed',
        f'{order.order_number} is paid and searching for a driver. {offered_to} eligible driver(s) were notified.',
        'order', url_for('main.order_details', order_id=order.id),
    )
    CartItem.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
    db.session.commit()
    flash(f'{order.order_number} was paid and sent to eligible online drivers.', 'success')
    return redirect(url_for('main.invoice', order_id=order.id))


@main.route('/order/<int:order_id>/invoice')
@login_required
def invoice(order_id):
    order = db.get_or_404(Order, order_id)
    if current_user.role != 'admin' and order.customer_id != current_user.id:
        abort(403)
    return render_template(
        'invoice.html', order=order,
        payment_label=PAYMENT_METHODS.get(order.payment_method, 'Demo wallet'),
    )


@main.route('/order/cancel/<int:order_id>', methods=['POST'])
@login_required
def cancel_order(order_id):
    order = db.get_or_404(Order, order_id)
    if current_user.role != 'customer' or order.customer_id != current_user.id:
        abort(403)
    success, message = cancel_order_record(order, refund=True)
    if success:
        db.session.commit()
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('main.orders'))


@main.route('/order/<int:order_id>/track')
@login_required
def track_order(order_id):
    order = db.get_or_404(Order, order_id)
    allowed = current_user.role == 'admin' or (
        current_user.role == 'customer' and order.customer_id == current_user.id
    ) or (
        current_user.role == 'driver' and order.driver and order.driver.user_id == current_user.id
    )
    if not allowed:
        abort(403)
    ensure_order_route(order)
    return render_template('track_order.html', order=order)


@main.route('/api/orders/<int:order_id>/location')
@login_required
def order_location(order_id):
    order = db.get_or_404(Order, order_id)
    if current_user.role == 'customer' and order.customer_id != current_user.id:
        abort(403)
    if current_user.role == 'driver' and (not order.driver or order.driver.user_id != current_user.id):
        abort(403)
    if current_user.role not in ('admin', 'customer', 'driver'):
        abort(403)
    ensure_order_route(order)
    driver = order.driver
    latitude, longitude, progress = simulated_order_location(order)
    start = (
        order.route_start_lat if order.route_start_lat is not None else STORE_LOCATION[0],
        order.route_start_lng if order.route_start_lng is not None else STORE_LOCATION[1],
    )
    destination = (order.delivery_lat, order.delivery_lng)
    route = order_route_points(order, start, destination)
    remaining_route = remaining_route_points(route, progress / 100)
    if remaining_route and latitude is not None and longitude is not None:
        remaining_route[0] = [latitude, longitude]
    return jsonify({
        'status': order.status,
        'latitude': latitude,
        'longitude': longitude,
        'destination_lat': order.delivery_lat,
        'destination_lng': order.delivery_lng,
        'progress': progress,
        'route': route if destination[0] is not None and destination[1] is not None else [],
        'remaining_route': remaining_route
        if destination[0] is not None and destination[1] is not None else [],
        'demo_duration_minutes': order.simulation_duration_minutes,
        'updated_at': utcnow().isoformat() + 'Z',
    })


@main.route('/api/admin/live-locations')
@login_required
def admin_live_locations():
    if current_user.role != 'admin':
        abort(403)
    active = Order.query.filter(Order.status.in_(('Assigned', 'In Transit'))).all()
    results = []
    for order in active:
        latitude, longitude, progress = simulated_order_location(order)
        results.append({
            'order_number': order.order_number or f'Order #{order.id}',
            'driver': order.driver.user.name if order.driver else 'Unassigned',
            'status': order.status, 'latitude': latitude, 'longitude': longitude,
            'destination_lat': order.delivery_lat,
            'destination_lng': order.delivery_lng, 'progress': progress,
            'updated_at': utcnow().isoformat() + 'Z',
        })
    return jsonify(orders=results)


@main.route('/driver/application')
@login_required
def driver_application():
    if current_user.role != 'driver':
        abort(403)
    if current_user.approval_status == 'approved':
        return redirect(url_for('main.driver_dashboard'))
    return render_template('driver_application.html')


@main.route('/api/account/approval')
@login_required
def account_approval():
    return jsonify(status=current_user.approval_status)


@main.route('/driver/dashboard')
@login_required
def driver_dashboard():
    if current_user.role != 'driver':
        abort(403)
    if current_user.approval_status != 'approved':
        return redirect(url_for('main.driver_application'))
    driver = Driver.query.filter_by(user_id=current_user.id).first()
    if not driver:
        abort(403)
    orders = Order.query.filter(
        Order.driver_id == driver.id, Order.status.in_(('Assigned', 'In Transit')),
    ).order_by(Order.assigned_at).all()
    return render_template('deliveries.html', tasks=orders, driver=driver)


@main.route('/driver/available-deliveries', methods=['GET', 'POST'])
@login_required
def available_deliveries():
    if current_user.role != 'driver' or current_user.approval_status != 'approved':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        order_id = request.form.get('order_id', type=int)
        action = request.form.get('action')
        order = db.session.get(Order, order_id) if order_id else None
        if not order or order.status != 'Finding Driver' or order.driver_id is not None:
            flash('That delivery was already accepted or is no longer available.', 'warning')
        elif action == 'decline':
            response = DriverOfferResponse.query.filter_by(
                order_id=order.id, driver_id=driver.id,
            ).first() or DriverOfferResponse(order_id=order.id, driver_id=driver.id)
            response.response = 'declined'
            db.session.add(response)
            audit('dispatch.offer_declined', 'order', order.id, driver.user.name)
            db.session.commit()
            flash('Delivery offer declined.', 'info')
        elif action == 'accept':
            if driver.status == 'Offline' or not driver_is_on_shift(driver):
                flash('You must be online and inside your working hours to accept a delivery.', 'danger')
            else:
                claimed = Order.query.filter(
                    Order.id == order.id, Order.status == 'Finding Driver',
                    Order.driver_id.is_(None),
                ).update({
                    Order.driver_id: driver.id, Order.status: 'Assigned',
                    Order.assigned_at: utcnow(),
                    Order.assignment_method: 'driver_accepted',
                    Order.dispatch_deadline_at: None,
                }, synchronize_session=False)
                if not claimed:
                    db.session.rollback()
                    flash('Another driver accepted this delivery first.', 'warning')
                else:
                    db.session.expire_all()
                    order = db.session.get(Order, order_id)
                    response = DriverOfferResponse.query.filter_by(
                        order_id=order.id, driver_id=driver.id,
                    ).first() or DriverOfferResponse(order_id=order.id, driver_id=driver.id)
                    response.response = 'accepted'
                    db.session.add(response)
                    assign_dispatch_order(order, driver, 'driver_accepted')
                    audit('dispatch.offer_accepted', 'order', order.id, driver.user.name)
                    db.session.commit()
                    flash(f'{order.order_number} is now assigned to you.', 'success')
                    return redirect(url_for('main.driver_delivery_detail', order_id=order.id))
        else:
            flash('Choose a valid offer action.', 'danger')
        return redirect(url_for('main.available_deliveries'))
    offers = open_offers_for_driver(driver)
    offer_rows = [{
        'order': order,
        'distance_km': assignment_distance(driver, order) / 1000
        if driver.current_lat is not None and driver.current_lng is not None else None,
        'seconds_left': max(0, int((order.dispatch_deadline_at - utcnow()).total_seconds()))
        if order.dispatch_deadline_at else 0,
    } for order in offers]
    return render_template(
        'driver_available_deliveries.html', driver=driver, offers=offer_rows,
        on_shift=driver_is_on_shift(driver),
    )


@main.route('/api/driver/available-deliveries')
@login_required
def available_deliveries_poll():
    if current_user.role != 'driver' or current_user.approval_status != 'approved':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    offers = open_offers_for_driver(driver)
    return jsonify(
        count=len(offers), order_ids=[order.id for order in offers],
        generated_at=utcnow().isoformat() + 'Z',
    )


@main.route('/driver/history')
@login_required
def driver_history():
    if current_user.role != 'driver':
        abort(403)
    if current_user.approval_status != 'approved':
        return redirect(url_for('main.driver_application'))
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    history = Order.query.filter(
        Order.driver_id == driver.id, Order.status.in_(('Delivered', 'Cancelled')),
    ).order_by(Order.created_at.desc()).limit(100).all()
    return render_template('driver_history.html', history=history)


@main.route('/driver/update_status/<int:order_id>', methods=['POST'])
@login_required
def update_order_status(order_id):
    if current_user.role != 'driver':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    order = db.get_or_404(Order, order_id)
    if order.driver_id != driver.id:
        abort(403)
    requested = request.form.get('status')
    expected = {'Assigned': 'In Transit', 'In Transit': 'Delivered'}.get(order.status)
    if requested != expected:
        flash('That delivery status change is not allowed.', 'danger')
    else:
        order.status = requested
        if requested == 'Delivered':
            finalize_delivery(order, driver)
        else:
            driver.status = 'Busy'
            order.transit_started_at = utcnow()
            order.route_start_lat = driver.current_lat if driver.current_lat is not None else STORE_LOCATION[0]
            order.route_start_lng = driver.current_lng if driver.current_lng is not None else STORE_LOCATION[1]
            if order.delivery_lat is not None and order.delivery_lng is not None:
                points, distance_meters, duration_seconds = build_road_route(
                    order.route_start_lat, order.route_start_lng,
                    order.delivery_lat, order.delivery_lng,
                )
                order.route_geometry = json.dumps(points)
                order.route_distance_meters = distance_meters
                order.route_duration_seconds = duration_seconds
                order.distance_text, order.duration_text = route_labels(
                    distance_meters, duration_seconds,
                )
                order.simulation_duration_minutes = demo_duration_minutes(duration_seconds)
            add_order_event(order, 'In Transit', f'{driver.user.name} started the delivery route.')
            notify_user(order.customer_id, 'Order is on the way', f'{order.order_number} is now in transit.', 'delivery', url_for('main.track_order', order_id=order.id))
        db.session.commit()
        flash(f'{order.order_number or "Order #" + str(order.id)} is now {requested}.', 'success')
    return redirect(url_for('main.driver_dashboard'))


@main.route('/api/driver/location', methods=['POST'])
@login_required
def update_driver_location():
    if current_user.role != 'driver':
        abort(403)
    data = request.get_json(silent=True) or {}
    try:
        latitude, longitude = float(data['latitude']), float(data['longitude'])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(error='Invalid coordinates'), 400
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    driver.current_lat, driver.current_lng = latitude, longitude
    driver.last_location_at = utcnow()
    db.session.commit()
    return jsonify(updated_at=driver.last_location_at.isoformat() + 'Z')


@main.route('/product/<int:item_id>', methods=['GET', 'POST'])
@login_required
def product_detail(item_id):
    if current_user.role != 'customer':
        abort(403)
    product = db.get_or_404(Inventory, item_id)
    if not product.is_active:
        abort(404)
    eligible_orders = Order.query.join(OrderItem).filter(
        Order.customer_id == current_user.id, Order.status == 'Delivered',
        OrderItem.inventory_id == product.id,
    ).order_by(Order.delivered_at.desc()).all()
    if request.method == 'POST':
        order = db.session.get(Order, int(request.form.get('order_id', 0)))
        try:
            rating = int(request.form.get('rating', 0))
        except ValueError:
            rating = 0
        eligible = order and any(candidate.id == order.id for candidate in eligible_orders)
        if not eligible or rating not in range(1, 6):
            flash('Reviews require a delivered purchase and a rating from 1 to 5.', 'danger')
        else:
            review = Review.query.filter_by(
                user_id=current_user.id, inventory_id=product.id, order_id=order.id,
            ).first() or Review(user_id=current_user.id, inventory_id=product.id, order_id=order.id)
            review.rating = rating
            review.comment = request.form.get('comment', '').strip()[:600] or None
            db.session.add(review)
            notify_user(current_user.id, 'Review published', f'Your review of {product.item} is now live.', 'success', url_for('main.product_detail', item_id=product.id))
            db.session.commit()
            flash('Thank you—your review is now live.', 'success')
        return redirect(url_for('main.product_detail', item_id=product.id))
    reviews = Review.query.filter_by(inventory_id=product.id).order_by(Review.created_at.desc()).all()
    average_rating = round(sum(review.rating for review in reviews) / len(reviews), 1) if reviews else 0
    related = Inventory.query.filter(
        Inventory.category == product.category, Inventory.id != product.id,
        Inventory.is_active.is_(True), Inventory.quantity > 0,
    ).limit(4).all()
    reviewed_order_ids = {review.order_id for review in Review.query.filter_by(
        user_id=current_user.id, inventory_id=product.id,
    ).all()}
    reviewable_orders = [order for order in eligible_orders if order.id not in reviewed_order_ids]
    return render_template(
        'product_detail.html', product=product, reviews=reviews,
        average_rating=average_rating, related=related,
        reviewable_orders=reviewable_orders, can_review=bool(reviewable_orders),
        existing_review=bool(reviewed_order_ids),
    )


@main.route('/notifications', methods=['GET', 'POST'])
@login_required
def notifications():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'read_all':
            Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
        elif action == 'read':
            notification = db.session.get(Notification, int(request.form.get('notification_id', 0)))
            if notification and notification.user_id == current_user.id:
                notification.is_read = True
        db.session.commit()
        return redirect(url_for('main.notifications'))
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(100).all()
    return render_template('notifications.html', notifications=items)


@main.route('/rewards')
@login_required
def rewards():
    if current_user.role != 'customer':
        abort(403)
    now = utcnow()
    coupons = Coupon.query.filter(
        Coupon.is_active.is_(True),
        or_(Coupon.starts_at.is_(None), Coupon.starts_at <= now),
        or_(Coupon.expires_at.is_(None), Coupon.expires_at >= now),
        or_(Coupon.usage_limit.is_(None), Coupon.usage_count < Coupon.usage_limit),
    ).order_by(Coupon.expires_at, Coupon.created_at.desc()).all()
    used_coupon_ids = {
        row[0] for row in db.session.query(CouponRedemption.coupon_id).filter_by(
            user_id=current_user.id,
        ).all()
    }
    return render_template(
        'rewards.html', coupons=coupons, used_coupon_ids=used_coupon_ids,
    )


@main.route('/order/<int:order_id>/details')
@login_required
def order_details(order_id):
    order = db.get_or_404(Order, order_id)
    allowed = current_user.role == 'admin' or order.customer_id == current_user.id or (
        current_user.role == 'driver' and order.driver and order.driver.user_id == current_user.id
    )
    if not allowed:
        abort(403)
    events = list(order.status_events)
    if not events:
        events = [
            type('Event', (), {'status': 'Order placed', 'note': 'Virtual payment completed.', 'created_at': order.created_at})(),
        ]
    return render_template('order_details.html', order=order, events=events)


@main.route('/support', methods=['GET', 'POST'])
@login_required
def support_center():
    if current_user.role == 'admin':
        return redirect(url_for('main.admin_support'))
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        order = db.session.get(Order, int(order_id)) if order_id and order_id.isdigit() else None
        owns_order = not order or order.customer_id == current_user.id or (
            current_user.role == 'driver' and order.driver and order.driver.user_id == current_user.id
        )
        subject = request.form.get('subject', '').strip()
        message = request.form.get('message', '').strip()
        if not owns_order or not subject or not message:
            flash('Complete the support request and choose one of your orders.', 'danger')
        else:
            number = f'TKT-{secrets.token_hex(4).upper()}'
            ticket = SupportTicket(
                user_id=current_user.id, order_id=order.id if order else None,
                ticket_number=number, category=request.form.get('category', 'General')[:50],
                subject=subject[:160], message=message[:1200],
            )
            db.session.add(ticket)
            notify_user(current_user.id, 'Support request received', f'{number} was created. We will update you here.', 'success', url_for('main.support_center'))
            db.session.commit()
            flash(f'{number} was submitted.', 'success')
        return redirect(url_for('main.support_center'))
    if current_user.role == 'customer':
        user_orders = Order.query.filter_by(customer_id=current_user.id).order_by(Order.created_at.desc()).all()
    else:
        driver = Driver.query.filter_by(user_id=current_user.id).first()
        user_orders = Order.query.filter_by(driver_id=driver.id).order_by(Order.created_at.desc()).all() if driver else []
    tickets = SupportTicket.query.filter_by(user_id=current_user.id).order_by(SupportTicket.updated_at.desc()).all()
    return render_template('support.html', tickets=tickets, orders=user_orders)


@main.route('/driver/delivery/<int:order_id>')
@login_required
def driver_delivery_detail(order_id):
    if current_user.role != 'driver':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    order = db.get_or_404(Order, order_id)
    if order.driver_id != driver.id:
        abort(403)
    start_lat = driver.current_lat if driver.current_lat is not None else STORE_LOCATION[0]
    start_lng = driver.current_lng if driver.current_lng is not None else STORE_LOCATION[1]
    destination_lat = order.delivery_lat if order.delivery_lat is not None else start_lat
    destination_lng = order.delivery_lng if order.delivery_lng is not None else start_lng
    display_route, distance_meters, duration_seconds = build_road_route(
        start_lat, start_lng, destination_lat, destination_lng,
    )
    distance_km = distance_meters / 1000
    eta_minutes = max(1, math.ceil(duration_seconds / 60))
    return render_template(
        'driver_delivery_detail.html', order=order,
        distance_km=distance_km, eta_minutes=eta_minutes,
        display_route=json.dumps(display_route),
    )


@main.route('/driver/route-queue')
@login_required
def driver_route_queue():
    if current_user.role != 'driver':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    tasks = Order.query.filter(
        Order.driver_id == driver.id, Order.status.in_(('Assigned', 'In Transit')),
    ).all()
    start_lat = driver.current_lat if driver.current_lat is not None else STORE_LOCATION[0]
    start_lng = driver.current_lng if driver.current_lng is not None else STORE_LOCATION[1]
    queue = []
    remaining = list(tasks)
    while remaining:
        next_order = min(remaining, key=lambda order: haversine_meters(
            start_lat, start_lng, order.delivery_lat or start_lat, order.delivery_lng or start_lng,
        ))
        distance_km = haversine_meters(
            start_lat, start_lng,
            next_order.delivery_lat or start_lat, next_order.delivery_lng or start_lng,
        ) / 1000
        queue.append({'order': next_order, 'distance_km': distance_km})
        start_lat, start_lng = next_order.delivery_lat or start_lat, next_order.delivery_lng or start_lng
        remaining.remove(next_order)
    return render_template('driver_route_queue.html', route_queue=queue)


@main.route('/driver/performance', methods=['GET', 'POST'])
@login_required
def driver_performance():
    if current_user.role != 'driver':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'availability':
            status = request.form.get('availability')
            active_delivery = Order.query.filter_by(driver_id=driver.id).filter(
                Order.status.in_(('Assigned', 'In Transit')),
            ).first()
            if status not in ('Available', 'Busy', 'Offline'):
                flash('Choose a valid availability.', 'danger')
            elif active_delivery and status != 'Busy':
                flash('Drivers with active deliveries remain Busy until the work is completed or reassigned.', 'danger')
            else:
                driver.status = status
                audit('driver.availability_updated', 'driver', driver.id, status)
                db.session.commit()
                flash('Availability updated.', 'success')
        elif action == 'schedule':
            start, end = request.form.get('shift_start', ''), request.form.get('shift_end', '')
            if not re.fullmatch(r'\d{2}:\d{2}', start) or not re.fullmatch(r'\d{2}:\d{2}', end):
                flash('Choose valid working hours.', 'danger')
            else:
                driver.shift_start, driver.shift_end = start, end
                audit('driver.schedule_updated', 'driver', driver.id, f'{start}-{end}')
                db.session.commit()
                flash('Working hours updated.', 'success')
        else:
            flash('Choose an availability or schedule action.', 'danger')
        return redirect(url_for('main.driver_performance'))
    delivered = Order.query.filter_by(driver_id=driver.id, status='Delivered').all()
    earnings = DriverEarning.query.filter_by(driver_id=driver.id).order_by(DriverEarning.created_at.desc()).all()
    durations = [
        (order.delivered_at - order.transit_started_at).total_seconds() / 60
        for order in delivered if order.delivered_at and order.transit_started_at
    ]
    assigned_total = Order.query.filter_by(driver_id=driver.id).filter(
        Order.status.in_(('Assigned', 'In Transit', 'Delivered', 'Cancelled')),
    ).count()
    today = utcnow().date()
    today_earnings = sum(
        item.amount_cents for item in earnings if item.created_at.date() == today
    )
    return render_template(
        'driver_performance.html', driver=driver, delivered=delivered, earnings=earnings,
        total_earnings=sum(item.amount_cents for item in earnings),
        average_minutes=round(sum(durations) / len(durations)) if durations else 0,
        completed_count=len(delivered),
        completion_rate=round(len(delivered) / assigned_total * 100) if assigned_total else 0,
        today_earnings=today_earnings,
    )


@main.route('/driver/report-issue', methods=['GET', 'POST'])
@login_required
def driver_report_issue():
    if current_user.role != 'driver':
        abort(403)
    driver = Driver.query.filter_by(user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        order = db.session.get(Order, int(request.form.get('order_id', 0)))
        details = request.form.get('details', '').strip()
        if not order or order.driver_id != driver.id or not details:
            flash('Choose one of your deliveries and describe the problem.', 'danger')
        else:
            issue = DriverIssue(
                driver_id=driver.id, order_id=order.id,
                issue_type=request.form.get('issue_type', 'Other')[:60], details=details[:1000],
            )
            db.session.add(issue)
            for admin in User.query.filter_by(role='admin', is_active=True):
                notify_user(admin.id, 'Driver reported a problem', f'{driver.user.name} reported an issue for {order.order_number}.', 'warning', url_for('main.admin_drivers'))
            db.session.commit()
            flash('The operations team has been notified.', 'success')
        return redirect(url_for('main.driver_report_issue'))
    orders = Order.query.filter_by(driver_id=driver.id).order_by(Order.created_at.desc()).limit(50).all()
    issues = DriverIssue.query.filter_by(driver_id=driver.id).order_by(DriverIssue.created_at.desc()).all()
    selected_order_id = request.args.get('order_id', type=int)
    return render_template(
        'driver_report_issue.html', orders=orders, issues=issues,
        selected_order_id=selected_order_id,
    )


@main.route('/admin/orders', methods=['GET', 'POST'])
@login_required
def admin_orders():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        order = db.session.get(Order, int(request.form.get('order_id', 0)))
        action = request.form.get('action')
        if not order:
            flash('Order not found.', 'danger')
        elif action == 'cancel_refund':
            success, message = cancel_order_record(order, refund=True, force=True)
            if success:
                db.session.commit()
            flash(message, 'success' if success else 'danger')
        else:
            flash('Choose a valid order action.', 'danger')
        return redirect(url_for('main.admin_orders'))
    search = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    query = Order.query.join(User, Order.customer_id == User.id)
    if search:
        query = query.filter(or_(
            Order.order_number.ilike(f'%{search}%'),
            Order.invoice_number.ilike(f'%{search}%'),
            User.name.ilike(f'%{search}%'), User.username.ilike(f'%{search}%'),
        ))
    if status:
        query = query.filter(Order.status == status)
    items = query.order_by(Order.created_at.desc()).limit(250).all()
    database_statuses = {row[0] for row in db.session.query(Order.status).distinct() if row[0]}
    statuses = list(ORDER_STATUSES) + sorted(database_statuses - set(ORDER_STATUSES))
    return render_template('admin_orders.html', orders=items, statuses=statuses, search=search, selected_status=status)


@main.route('/admin/customers', methods=['GET', 'POST'])
@login_required
def admin_customers():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        customer = db.session.get(User, int(request.form.get('user_id', 0)))
        action = request.form.get('action')
        if not customer or customer.role != 'customer':
            flash('Customer account not found.', 'danger')
        elif action == 'toggle':
            customer.is_active = not customer.is_active
            audit('customer.status_changed', 'user', customer.id, 'Active' if customer.is_active else 'Suspended')
            db.session.commit()
            flash('Customer status updated.', 'success')
        elif action == 'balance':
            try:
                adjustment = int(Decimal(request.form.get('amount', '0')) * 100)
            except (InvalidOperation, ValueError):
                adjustment = 0
            if not adjustment or customer.wallet_balance_cents + adjustment < 0:
                flash('Enter a valid balance adjustment.', 'danger')
            else:
                customer.wallet_balance_cents += adjustment
                db.session.add(PaymentTransaction(
                    user_id=customer.id, transaction_type='admin_adjustment', method='admin',
                    amount_cents=abs(adjustment), balance_after_cents=customer.wallet_balance_cents,
                    reference=f'ADM-{customer.id}-{secrets.token_hex(5).upper()}',
                ))
                audit('customer.balance_adjusted', 'user', customer.id, f'{adjustment} cents')
                db.session.commit()
                flash('Virtual balance adjusted.', 'success')
        return redirect(url_for('main.admin_customers'))
    search = request.args.get('q', '').strip()
    query = User.query.filter_by(role='customer')
    if search:
        query = query.filter(or_(User.name.ilike(f'%{search}%'), User.username.ilike(f'%{search}%'), User.email.ilike(f'%{search}%')))
    customers = query.order_by(User.created_at.desc()).all()
    order_counts = dict(db.session.query(Order.customer_id, func.count(Order.id)).group_by(Order.customer_id).all())
    return render_template('admin_customers.html', customers=customers, order_counts=order_counts, search=search)


@main.route('/admin/customers/<int:user_id>')
@login_required
def admin_customer_detail(user_id):
    if current_user.role != 'admin':
        abort(403)
    customer = db.get_or_404(User, user_id)
    if customer.role != 'customer':
        abort(404)
    orders = Order.query.filter_by(customer_id=customer.id).order_by(Order.created_at.desc()).all()
    transactions = PaymentTransaction.query.filter_by(user_id=customer.id).order_by(
        PaymentTransaction.created_at.desc(),
    ).limit(100).all()
    tickets = SupportTicket.query.filter_by(user_id=customer.id).order_by(
        SupportTicket.updated_at.desc(),
    ).all()
    return render_template(
        'admin_customer_detail.html', customer=customer, orders=orders,
        transactions=transactions, tickets=tickets,
    )


@main.route('/admin/driver-management', methods=['GET', 'POST'])
@login_required
def admin_drivers():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'resolve_issue':
            issue = db.session.get(DriverIssue, int(request.form.get('issue_id', 0)))
            if not issue:
                flash('Issue not found.', 'danger')
            else:
                issue.status = 'Resolved'
                issue.admin_response = request.form.get('admin_response', '').strip()[:1000] or 'Resolved by operations.'
                issue.resolved_at = utcnow()
                notify_user(issue.driver.user_id, 'Driver report resolved', issue.admin_response, 'success', url_for('main.driver_report_issue'))
                audit('driver.issue_resolved', 'driver_issue', issue.id, issue.admin_response)
                db.session.commit()
                flash('Driver report resolved.', 'success')
        return redirect(url_for('main.admin_drivers'))
    drivers = Driver.query.join(User).filter(User.approval_status == 'approved').order_by(Driver.status, User.name).all()
    metrics = {}
    for driver in drivers:
        delivered = Order.query.filter_by(driver_id=driver.id, status='Delivered').count()
        active = Order.query.filter(Order.driver_id == driver.id, Order.status.in_(('Assigned', 'In Transit'))).count()
        earnings = db.session.query(func.coalesce(func.sum(DriverEarning.amount_cents), 0)).filter_by(driver_id=driver.id).scalar()
        metrics[driver.id] = {'delivered': delivered, 'active': active, 'earnings': earnings}
    issues = DriverIssue.query.order_by(DriverIssue.status, DriverIssue.created_at.desc()).all()
    return render_template('admin_drivers.html', drivers=drivers, metrics=metrics, issues=issues)


@main.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def admin_categories():
    if current_user.role != 'admin':
        abort(403)
    ensure_category_records()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            name = request.form.get('name', '').strip()
            if not name or ProductCategory.query.filter(func.lower(ProductCategory.name) == name.lower()).first():
                flash('Enter a unique category name.', 'danger')
            else:
                category = ProductCategory(name=name)
                db.session.add(category)
                audit('category.created', 'category', None, name)
                db.session.commit()
                flash('Category created.', 'success')
        else:
            category = db.session.get(ProductCategory, int(request.form.get('category_id', 0)))
            if not category:
                flash('Category not found.', 'danger')
            elif action == 'rename':
                new_name = request.form.get('name', '').strip()
                duplicate = ProductCategory.query.filter(func.lower(ProductCategory.name) == new_name.lower(), ProductCategory.id != category.id).first()
                if not new_name or duplicate:
                    flash('Enter a unique category name.', 'danger')
                else:
                    old_name = category.name
                    Inventory.query.filter_by(category=old_name).update({'category': new_name})
                    category.name = new_name
                    audit('category.renamed', 'category', category.id, f'{old_name} -> {new_name}')
                    db.session.commit()
                    flash('Category and products updated.', 'success')
            elif action == 'toggle':
                category.is_active = not category.is_active
                if not category.is_active:
                    Inventory.query.filter_by(category=category.name).update({'is_active': False})
                audit('category.status_changed', 'category', category.id, str(category.is_active))
                db.session.commit()
                flash('Category status updated.', 'success')
        return redirect(url_for('main.admin_categories'))
    db.session.commit()
    categories = ProductCategory.query.order_by(ProductCategory.is_active.desc(), ProductCategory.name).all()
    counts = dict(db.session.query(Inventory.category, func.count(Inventory.id)).group_by(Inventory.category).all())
    return render_template('admin_categories.html', categories=categories, counts=counts)


@main.route('/admin/promotions', methods=['GET', 'POST'])
@login_required
def admin_promotions():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            try:
                discount_type = request.form.get('discount_type')
                raw_value = Decimal(request.form.get('value', '0'))
                value = int(raw_value) if discount_type == 'percent' else int(raw_value * 100)
                minimum = int(Decimal(request.form.get('minimum', '0')) * 100)
                usage_limit = int(request.form['usage_limit']) if request.form.get('usage_limit') else None
                expires = datetime.fromisoformat(request.form['expires_at']) if request.form.get('expires_at') else None
                code = request.form.get('code', '').strip().upper()
                if not code or discount_type not in ('percent', 'fixed') or value <= 0:
                    raise ValueError
                coupon = Coupon(
                    code=code, description=request.form.get('description', '').strip()[:255],
                    discount_type=discount_type, value=value, minimum_cents=max(0, minimum),
                    usage_limit=usage_limit, expires_at=expires,
                )
                db.session.add(coupon)
                offer_text = (
                    f'{value}% off' if discount_type == 'percent'
                    else f'${value / 100:,.2f} off'
                )
                for customer in User.query.filter_by(role='customer', is_active=True):
                    notify_user(
                        customer.id, f'New offer: {code}',
                        f'Use {code} for {offer_text}' + (
                            f' on orders of ${minimum / 100:,.2f} or more.' if minimum else '.'
                        ),
                        'promotion', url_for('main.rewards'),
                    )
                audit('coupon.created', 'coupon', None, code)
                db.session.commit()
                flash(f'Coupon {code} created.', 'success')
            except (ValueError, InvalidOperation, IntegrityError):
                db.session.rollback()
                flash('Check the coupon values and use a unique code.', 'danger')
        elif action == 'toggle':
            coupon = db.session.get(Coupon, int(request.form.get('coupon_id', 0)))
            if coupon:
                coupon.is_active = not coupon.is_active
                audit('coupon.status_changed', 'coupon', coupon.id, str(coupon.is_active))
                db.session.commit()
                flash('Coupon status updated.', 'success')
        return redirect(url_for('main.admin_promotions'))
    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template('admin_promotions.html', coupons=coupons)


@main.route('/admin/analytics')
@login_required
def admin_analytics():
    if current_user.role != 'admin':
        abort(403)
    orders = Order.query.all()
    paid = [order for order in orders if order.payment_status in ('paid', 'demo', 'virtual_paid') and order.status != 'Cancelled']
    status_counts = dict(db.session.query(Order.status, func.count(Order.id)).group_by(Order.status).all())
    product_sales = db.session.query(
        OrderItem.item_name, func.sum(OrderItem.quantity).label('units'),
        func.sum(OrderItem.quantity * OrderItem.unit_price_cents).label('revenue'),
    ).join(Order).filter(Order.status != 'Cancelled').group_by(OrderItem.item_name).order_by(func.sum(OrderItem.quantity).desc()).limit(8).all()
    category_sales = db.session.query(
        Inventory.category, func.sum(OrderItem.quantity).label('units'),
    ).join(OrderItem, OrderItem.inventory_id == Inventory.id).join(Order).filter(Order.status != 'Cancelled').group_by(Inventory.category).order_by(func.sum(OrderItem.quantity).desc()).all()
    return render_template(
        'admin_analytics.html', orders=orders, paid=paid, status_counts=status_counts,
        product_sales=product_sales, category_sales=category_sales,
        revenue_cents=sum(order.amount_cents for order in paid),
    )


@main.route('/admin/notifications', methods=['GET', 'POST'])
@login_required
def admin_notifications():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        audience = request.form.get('audience')
        title = request.form.get('title', '').strip()
        message = request.form.get('message', '').strip()
        if audience not in ('all', 'customer', 'driver') or not title or not message:
            flash('Complete the announcement and choose an audience.', 'danger')
        else:
            query = User.query.filter(User.is_active.is_(True), User.id != current_user.id)
            if audience != 'all':
                query = query.filter_by(role=audience)
            recipients = query.all()
            for user in recipients:
                notify_user(user.id, title[:160], message[:600], 'promotion')
            audit('notification.broadcast', 'notification', None, f'{audience}: {title}')
            db.session.commit()
            flash(f'Announcement sent to {len(recipients)} accounts.', 'success')
        return redirect(url_for('main.admin_notifications'))
    recent = Notification.query.order_by(Notification.created_at.desc()).limit(50).all()
    return render_template('admin_notifications.html', recent=recent)


@main.route('/admin/support', methods=['GET', 'POST'])
@login_required
def admin_support():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        ticket = db.session.get(SupportTicket, int(request.form.get('ticket_id', 0)))
        if not ticket:
            flash('Support ticket not found.', 'danger')
        else:
            ticket.status = request.form.get('status', 'In Progress')
            ticket.priority = request.form.get('priority', 'Normal')
            ticket.admin_response = request.form.get('admin_response', '').strip()[:1200] or None
            notify_user(ticket.user_id, f'{ticket.ticket_number} updated', ticket.admin_response or f'Status changed to {ticket.status}.', 'support', url_for('main.support_center'))
            audit('support.updated', 'support_ticket', ticket.id, ticket.status)
            db.session.commit()
            flash('Support ticket updated.', 'success')
        return redirect(url_for('main.admin_support'))
    status = request.args.get('status', '')
    query = SupportTicket.query
    if status:
        query = query.filter_by(status=status)
    tickets = query.order_by(SupportTicket.updated_at.desc()).all()
    return render_template('admin_support.html', tickets=tickets, selected_status=status)


@main.route('/admin/audit-log')
@login_required
def admin_audit_log():
    if current_user.role != 'admin':
        abort(403)
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(300).all()
    return render_template('admin_audit_log.html', logs=logs)


@main.route('/admin/application-settings', methods=['GET', 'POST'])
@login_required
def admin_application_settings():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        try:
            delivery_fee = int(Decimal(request.form.get('delivery_fee', '0')) * 100)
            minimum_order = int(Decimal(request.form.get('minimum_order', '0')) * 100)
            demo_speed = int(request.form.get('demo_speed_multiplier', 15))
            dispatch_offer_minutes = int(request.form.get('dispatch_offer_minutes', 2))
            if delivery_fee < 0 or minimum_order < 0 or not 1 <= demo_speed <= 60 or not 1 <= dispatch_offer_minutes <= 60:
                raise ValueError
            set_setting('store_name', request.form.get('store_name', '').strip() or 'GreenCart Express')
            set_setting('store_email', request.form.get('store_email', '').strip())
            set_setting('store_phone', request.form.get('store_phone', '').strip())
            set_setting('store_address', request.form.get('store_address', '').strip())
            set_setting('delivery_fee_cents', delivery_fee)
            set_setting('minimum_order_cents', minimum_order)
            set_setting('demo_speed_multiplier', demo_speed)
            set_setting('dispatch_offer_minutes', dispatch_offer_minutes)
            set_setting('maintenance_mode', '1' if request.form.get('maintenance_mode') else '0')
            audit('settings.updated', 'application', 'global', 'Commerce and demo settings updated')
            db.session.commit()
            flash('Application settings saved.', 'success')
        except (ValueError, InvalidOperation):
            db.session.rollback()
            flash('Check the settings values.', 'danger')
        return redirect(url_for('main.admin_application_settings'))
    settings_data = {key: setting_value(key) for key in DEFAULT_SETTINGS}
    return render_template('admin_application_settings.html', settings=settings_data)
