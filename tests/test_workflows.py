from app import db
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from app.models import (
    CartItem, Coupon, CouponRedemption, Driver, DriverOfferResponse, Inventory, Notification, Order, OrderItem, PaymentTransaction,
    Review, SupportTicket, User, utcnow,
)
from app.routes import fit_product_image
from conftest import login
from werkzeug.security import generate_password_hash


def make_test_image(width=900, height=300, color=(33, 168, 116)):
    stream = BytesIO()
    Image.new('RGB', (width, height), color).save(stream, 'PNG')
    stream.seek(0)
    return stream


def test_product_image_fitter_trims_studio_space_and_scales_subject():
    source = Image.new('RGB', (1000, 1000), 'white')
    ImageDraw.Draw(source).rectangle((400, 400, 600, 600), fill=(33, 168, 116))

    fitted = fit_product_image(source)

    assert fitted.size == (1200, 900)
    assert fitted.getpixel((20, 20)) == (255, 255, 255)
    assert fitted.getpixel((300, 450)) != (255, 255, 255)


def test_signup_always_creates_customer(client, app):
    response = client.post('/signup', data={
        'username': 'newperson', 'name': 'New Person', 'password': 'longpassword',
        'address': 'Clifton, Karachi', 'role': 'admin',
    })
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username='newperson').one()
        assert user.role == 'customer'


def test_customer_cart_persists_across_sign_out(client, app):
    login(client, 'customer', 'customerpass')
    with app.app_context():
        product_id = Inventory.query.filter_by(item='Apples').one().id
        customer_id = User.query.filter_by(username='customer').one().id
    response = client.post(
        f'/cart/add/{product_id}', data={'quantity': 3},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.get_json()['cart_count'] == 3
    client.post('/logout')
    with app.app_context():
        saved = CartItem.query.filter_by(
            user_id=customer_id, inventory_id=product_id,
        ).one()
        assert saved.quantity == 3

    login(client, 'customer', 'customerpass')
    restored_cart = client.get('/cart')
    assert restored_cart.status_code == 200
    assert b'Apples' in restored_cart.data
    assert b'value="3"' in restored_cart.data


def test_multi_item_demo_checkout_and_soft_cancel(client, app):
    login(client, 'customer', 'customerpass')
    with app.app_context():
        apple_id = Inventory.query.filter_by(item='Apples').one().id
        milk_id = Inventory.query.filter_by(item='Milk').one().id
    ajax_response = client.post(
        f'/cart/add/{apple_id}', data={'quantity': 2},
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert ajax_response.status_code == 200
    assert ajax_response.get_json()['cart_count'] == 2
    client.post(f'/cart/add/{milk_id}', data={'quantity': 1})
    assert client.get('/checkout').status_code == 200
    response = client.post('/checkout', data={
        'delivery_name': 'Customer', 'delivery_phone': '03001234567',
        'delivery_address': 'Street 1, Clifton', 'delivery_city': 'Karachi',
        'payment_method': 'jazzcash', 'payment_detail': '03001234567',
        'delivery_lat': '24.8138', 'delivery_lng': '67.0305',
        'save_address': '1', 'demo_consent': '1',
    })
    assert response.status_code == 302

    with app.app_context():
        order = Order.query.one()
        assert order.status == 'Finding Driver'
        assert order.dispatch_deadline_at is not None
        assert order.payment_status == 'virtual_paid'
        assert order.amount_cents == 900
        assert len(order.items) == 2
        assert order.invoice_number
        assert PaymentTransaction.query.filter_by(order_id=order.id, transaction_type='charge').count() == 1
        assert order.customer.wallet_balance_cents == 99100
        order_id = order.id
        assert db.session.get(Inventory, apple_id).quantity == 8

    assert client.get(f'/order/{order_id}/invoice').status_code == 200

    response = client.post(f'/order/cancel/{order_id}')
    assert response.status_code == 302
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.status == 'Cancelled'
        assert order.payment_status == 'refunded'
        assert db.session.get(Inventory, apple_id).quantity == 10
        assert order.customer.wallet_balance_cents == 100000
        assert PaymentTransaction.query.filter_by(order_id=order.id, transaction_type='refund').count() == 1


def test_customer_pages_and_virtual_topup(client, app):
    login(client, 'customer', 'customerpass')
    shop_page = client.get('/shop')
    assert shop_page.status_code == 200
    assert b'compact-stepper' in shop_page.data
    assert client.get('/orders').status_code == 200
    assert client.get('/profile').status_code == 200
    settings_page = client.get('/settings')
    assert settings_page.status_code == 200
    assert b'data-topup-form' in settings_page.data
    assert client.post('/profile', data={
        'name': 'Customer Updated', 'email': 'customer@example.com',
        'phone': '0300 1234567', 'address': 'Clifton, Karachi',
        'profile_image': (make_test_image(900, 300), 'avatar.png'),
    }).status_code == 302
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        assert customer.profile_image_filename.endswith('.webp')
        with Image.open(Path(app.config['PROFILE_UPLOAD_FOLDER']) / customer.profile_image_filename) as image:
            assert image.size == (512, 512)
    response = client.post('/settings', data={
        'action': 'topup', 'amount_cents': '10000',
    })
    assert response.status_code == 302
    assert response.location.endswith('/settings?topup=1')
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        assert customer.wallet_balance_cents == 100000
        assert PaymentTransaction.query.filter_by(
            user_id=customer.id, transaction_type='topup',
        ).count() == 0

    response = client.post('/settings', data={
        'action': 'topup', 'amount_cents': '10000',
        'payment_method': 'card',
        'payment_detail': '4242 4242 4242 4242',
        'demo_consent': '1',
    })
    assert response.status_code == 302
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        assert customer.wallet_balance_cents == 110000
        transaction = PaymentTransaction.query.filter_by(
            user_id=customer.id, transaction_type='topup',
        ).one()
        assert transaction.method == 'card'
        assert transaction.method_label == 'Credit / debit card'


def test_admin_can_create_category_and_deactivate_product(client, app):
    login(client, 'admin', 'adminpass')
    response = client.post('/admin/inventory', data={
        'action': 'create', 'item': 'Desk Lamp', 'description': 'LED lamp',
        'category_mode': 'new', 'new_category': 'Lighting',
        'price': '19.99', 'quantity': '8', 'threshold': '2',
    })
    assert response.status_code == 302
    with app.app_context():
        product = Inventory.query.filter_by(item='Desk Lamp').one()
        assert product.category == 'Lighting'
        product_id = product.id
    client.post('/admin/inventory', data={
        'action': 'toggle', 'inventory_id': product_id,
    })
    with app.app_context():
        assert db.session.get(Inventory, product_id).is_active is False


def test_dispatch_delivery_and_location_flow(client, app):
    response = client.post('/signup', data={
        'role': 'driver', 'name': 'Driver One', 'username': 'driver1',
        'password': 'driverpass', 'email': 'driver@example.com',
        'phone': '03001234567',
    })
    assert response.status_code == 302
    with app.app_context():
        applicant = User.query.filter_by(username='driver1').one()
        assert applicant.approval_status == 'pending'
        applicant_id = applicant.id

    login(client, 'driver1', 'driverpass')
    pending_page = client.get('/driver/application')
    assert pending_page.status_code == 200
    assert b'reviewing your driver application' in pending_page.data
    client.post('/logout')

    login(client, 'admin', 'adminpass')
    assert client.get('/admin/dashboard').status_code == 200
    assert client.get('/profile').status_code == 200
    assert client.get('/admin/inventory').status_code == 200
    assert client.get('/admin/driver-applications').status_code == 200
    client.post('/admin/driver-applications', data={
        'action': 'approve_driver', 'user_id': applicant_id,
    })

    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        product = Inventory.query.filter_by(item='Apples').one()
        order = Order(
            customer_id=customer.id, status='Paid', payment_status='demo',
            amount_cents=250, amount=2.5, order_number='GCX-TEST1234',
            delivery_name='Customer', delivery_address='Street 1',
            delivery_city='Karachi', delivery_phone='03001234567',
            delivery_lat=24.8138, delivery_lng=67.0305,
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id
        driver = Driver.query.one()
        driver_id = driver.id

    response = client.post('/admin/drivers', data={
        'action': 'assign', 'order_id': order_id, 'driver_id': driver_id,
    })
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).status == 'Assigned'
        assert db.session.get(Driver, driver_id).status == 'Busy'
    dispatch_page = client.get('/admin/drivers')
    assert b'Assigned' in dispatch_page.data
    assert b'Driver One' in dispatch_page.data
    locations = client.get('/api/admin/live-locations').get_json()
    assert locations['orders'][0]['order_number'] == 'GCX-TEST1234'

    client.post('/logout')
    login(client, 'driver1', 'driverpass')
    driver_page = client.get('/driver/dashboard')
    assert driver_page.status_code == 200
    assert client.get('/profile').status_code == 200
    assert client.get('/driver/history').status_code == 200
    assert b'Open route map' in driver_page.data
    assert client.get(f'/order/{order_id}/track').status_code == 200
    response = client.post('/api/driver/location', json={'latitude': 24.86, 'longitude': 67.01})
    assert response.status_code == 200
    client.post(f'/driver/update_status/{order_id}', data={'status': 'In Transit'})
    moving = client.get(f'/api/orders/{order_id}/location').get_json()
    assert len(moving['remaining_route']) >= 2
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.transit_started_at = utcnow() - timedelta(minutes=31)
        order.simulation_duration_minutes = 30
        db.session.commit()
    location = client.get(f'/api/orders/{order_id}/location').get_json()
    assert location['progress'] == 100
    assert location['status'] == 'Delivered'
    assert len(location['route']) >= 2
    assert location['remaining_route'] == []

    with app.app_context():
        assert db.session.get(Order, order_id).status == 'Delivered'
        driver = db.session.get(Driver, driver_id)
        assert driver.status == 'Available'
        assert driver.current_lat == 24.8138


def test_customer_cannot_cancel_another_customers_order(client, app):
    with app.app_context():
        other = User(
            username='other', name='Other', role='customer', address='Karachi',
            password='unused',
        )
        db.session.add(other)
        db.session.flush()
        order = Order(customer_id=other.id, status='Paid', amount_cents=100)
        db.session.add(order)
        db.session.commit()
        order_id = order.id
    login(client, 'customer', 'customerpass')
    assert client.post(f'/order/cancel/{order_id}').status_code == 403
    with app.app_context():
        assert db.session.get(Order, order_id).status == 'Paid'


def test_new_customer_driver_and_admin_feature_pages(client, app):
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        product = Inventory.query.filter_by(item='Apples').one()
        driver_user = User(
            username='route-driver', name='Route Driver', role='driver',
            approval_status='approved', password=generate_password_hash('driverpass'),
        )
        db.session.add(driver_user)
        db.session.flush()
        driver = Driver(user_id=driver_user.id, current_lat=24.86, current_lng=67.01)
        db.session.add(driver)
        db.session.flush()
        order = Order(
            customer_id=customer.id, driver_id=driver.id, status='Delivered',
            payment_status='virtual_paid', amount_cents=250, subtotal_cents=250,
            order_number='GCX-FEATURE01', invoice_number='GC-FEATURE01',
            delivery_name='Customer', delivery_address='Clifton', delivery_city='Karachi',
            delivery_phone='03001234567', delivery_lat=24.82, delivery_lng=67.03,
            delivered_at=utcnow(),
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderItem(
            order_id=order.id, inventory_id=product.id, item_name=product.item,
            unit_price_cents=product.price_cents, quantity=1,
        ))
        db.session.commit()
        product_id, order_id, customer_id = product.id, order.id, customer.id

    login(client, 'customer', 'customerpass')
    for path in (f'/product/{product_id}', '/notifications', '/rewards', f'/order/{order_id}/details', '/support'):
        assert client.get(path).status_code == 200
    customer_details = client.get(f'/order/{order_id}/details')
    assert b'Paid with virtual funds' in customer_details.data
    assert b'virtual_paid' not in customer_details.data
    assert b'Back to my orders' in customer_details.data
    assert client.post(f'/product/{product_id}', data={
        'order_id': order_id, 'rating': '5', 'comment': 'Excellent quality.',
    }).status_code == 302
    assert client.post('/support', data={
        'order_id': order_id, 'category': 'Order', 'subject': 'Demo question',
        'message': 'Please confirm this delivered order.',
    }).status_code == 302
    with app.app_context():
        assert Review.query.count() == 1
        assert SupportTicket.query.count() == 1
        assert Notification.query.filter_by(user_id=User.query.filter_by(username='customer').one().id).count() >= 2

    client.post('/logout')
    login(client, 'route-driver', 'driverpass')
    for path in (f'/driver/delivery/{order_id}', '/driver/route-queue', '/driver/performance', '/driver/report-issue', '/notifications'):
        assert client.get(path).status_code == 200
    delivery_details = client.get(f'/driver/delivery/{order_id}')
    assert b'leaflet.css' in delivery_details.data
    assert b'leaflet.js' in delivery_details.data
    assert b'Back to my deliveries' in delivery_details.data
    assert b'[[24.86, 67.01], [24.82, 67.03]]' in delivery_details.data
    assert client.post('/driver/performance', data={
        'action': 'availability', 'availability': 'Offline',
    }).status_code == 302
    assert client.post('/driver/performance', data={
        'action': 'schedule', 'shift_start': '10:00', 'shift_end': '19:00',
    }).status_code == 302
    with app.app_context():
        driver = Driver.query.join(User).filter(User.username == 'route-driver').one()
        assert driver.status == 'Offline'
        assert (driver.shift_start, driver.shift_end) == ('10:00', '19:00')

    client.post('/logout')
    login(client, 'admin', 'adminpass')
    admin_pages = (
        '/admin/orders', '/admin/customers', '/admin/driver-management',
        '/admin/categories', '/admin/promotions', '/admin/analytics',
        '/admin/notifications', '/admin/support', '/admin/audit-log',
        '/admin/application-settings',
        f'/admin/customers/{customer_id}',
    )
    for path in admin_pages:
        response = client.get(path)
        assert response.status_code == 200, path
    order_management = client.get('/admin/orders')
    assert b'Pending Payment' in order_management.data
    assert b'In Transit' in order_management.data
    assert b'Track delivery' in order_management.data
    assert b'Back to order management' in client.get(f'/order/{order_id}/details').data
    assert client.post('/admin/promotions', data={
        'action': 'create', 'code': 'SAVE10', 'discount_type': 'percent',
        'value': '10', 'minimum': '0', 'description': 'Ten percent off',
    }).status_code == 302
    with app.app_context():
        assert Coupon.query.filter_by(code='SAVE10').one().value == 10


def test_coupon_rewards_and_refund_restore_virtual_value(client, app):
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        customer.reward_points = 100
        db.session.add(Coupon(
            code='REWARD10', discount_type='percent', value=10,
            description='Reward test', minimum_cents=0,
        ))
        product_id = Inventory.query.filter_by(item='Apples').one().id
        db.session.commit()
    login(client, 'customer', 'customerpass')
    client.post(f'/cart/add/{product_id}', data={'quantity': 2})
    preview = client.post('/checkout', data={
        'coupon_code': 'reward10', 'redeem_rewards': '1',
        'checkout_action': 'apply_coupon',
    })
    assert preview.status_code == 200
    assert b'REWARD10 applied' in preview.data
    assert b'$3.60' in preview.data
    response = client.post('/checkout', data={
        'delivery_name': 'Customer', 'delivery_phone': '03001234567',
        'delivery_address': 'Street 2, Clifton', 'delivery_city': 'Karachi',
        'payment_method': 'card', 'payment_detail': '4242424242424242',
        'delivery_lat': '24.8138', 'delivery_lng': '67.0305',
        'coupon_code': 'reward10', 'redeem_rewards': '1', 'demo_consent': '1',
    })
    assert response.status_code == 302
    with app.app_context():
        order = Order.query.one()
        assert order.subtotal_cents == 500
        assert order.discount_cents == 140
        assert order.amount_cents == 360
        assert order.coupon_code == 'REWARD10'
        assert order.reward_points_redeemed == 90
        assert order.reward_points_earned == 4
        assert order.customer.reward_points == 14
        assert CouponRedemption.query.filter_by(order_id=order.id).count() == 1
        order_id = order.id
    invoice = client.get(f'/order/{order_id}/invoice')
    assert b'Coupon REWARD10' in invoice.data
    assert b'Reward points (90 points)' in invoice.data
    assert b'You saved' in invoice.data
    client.post(f'/order/cancel/{order_id}')
    with app.app_context():
        assert User.query.filter_by(username='customer').one().reward_points == 100
        assert Coupon.query.filter_by(code='REWARD10').one().usage_count == 0
        assert CouponRedemption.query.count() == 0


def test_driver_offer_first_claim_and_automatic_fallback(client, app):
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        driver_users = []
        for index in (1, 2):
            user = User(
                username=f'offer-driver-{index}', name=f'Offer Driver {index}',
                role='driver', approval_status='approved', is_active=True,
                password=generate_password_hash('driverpass'),
            )
            db.session.add(user)
            db.session.flush()
            driver = Driver(
                user_id=user.id, status='Available', shift_start='00:00', shift_end='23:59',
                current_lat=24.85 + index / 100, current_lng=67.01,
            )
            db.session.add(driver)
            db.session.flush()
            driver_users.append((user.id, driver.id))
        offered = Order(
            customer_id=customer.id, status='Finding Driver', payment_status='virtual_paid',
            amount_cents=1200, order_number='GCX-OFFER001', delivery_name='Customer',
            delivery_address='Clifton', delivery_city='Karachi', delivery_phone='03001234567',
            delivery_lat=24.82, delivery_lng=67.03, dispatch_started_at=utcnow(),
            dispatch_deadline_at=utcnow() + timedelta(minutes=5),
        )
        db.session.add(offered)
        db.session.commit()
        offered_id = offered.id

    login(client, 'offer-driver-1', 'driverpass')
    offers_page = client.get('/driver/available-deliveries')
    assert offers_page.status_code == 200
    assert b'GCX-OFFER001' in offers_page.data
    response = client.post('/driver/available-deliveries', data={
        'order_id': offered_id, 'action': 'accept',
    })
    assert response.status_code == 302
    with app.app_context():
        order = db.session.get(Order, offered_id)
        assert order.status == 'Assigned'
        assert order.driver_id == driver_users[0][1]
        assert order.assignment_method == 'driver_accepted'
        assert DriverOfferResponse.query.filter_by(
            order_id=offered_id, driver_id=driver_users[0][1], response='accepted',
        ).count() == 1

    client.post('/logout')
    login(client, 'offer-driver-2', 'driverpass')
    driver_two_page = client.get('/driver/available-deliveries')
    assert f'data-order-id="{offered_id}"'.encode() not in driver_two_page.data
    client.post('/driver/available-deliveries', data={
        'order_id': offered_id, 'action': 'accept',
    })
    with app.app_context():
        assert db.session.get(Order, offered_id).driver_id == driver_users[0][1]
        customer = User.query.filter_by(username='customer').one()
        expired = Order(
            customer_id=customer.id, status='Finding Driver', payment_status='virtual_paid',
            amount_cents=900, order_number='GCX-AUTO0001', delivery_name='Customer',
            delivery_address='Gulshan', delivery_city='Karachi', delivery_phone='03001234567',
            delivery_lat=24.91, delivery_lng=67.09, dispatch_started_at=utcnow() - timedelta(minutes=5),
            dispatch_deadline_at=utcnow() - timedelta(seconds=1),
        )
        db.session.add(expired)
        db.session.commit()
        expired_id = expired.id
    client.get('/api/dispatch/tick')
    with app.app_context():
        expired = db.session.get(Order, expired_id)
        assert expired.status == 'Assigned'
        assert expired.driver_id == driver_users[1][1]
        assert expired.assignment_method == 'automatic'


def test_busy_driver_can_accept_multiple_orders_and_returns_available(client, app):
    with app.app_context():
        customer = User.query.filter_by(username='customer').one()
        driver_user = User(
            username='multi-order-driver', name='Multi Order Driver', role='driver',
            approval_status='approved', is_active=True,
            password=generate_password_hash('driverpass'),
        )
        db.session.add(driver_user)
        db.session.flush()
        driver = Driver(
            user_id=driver_user.id, status='Available', shift_start='00:00',
            shift_end='23:59', current_lat=24.86, current_lng=67.01,
        )
        db.session.add(driver)
        db.session.flush()
        orders = []
        for index in (1, 2):
            order = Order(
                customer_id=customer.id, status='Finding Driver',
                payment_status='virtual_paid', amount_cents=1000,
                order_number=f'GCX-MULTI00{index}', delivery_name='Customer',
                delivery_address=f'Street {index}', delivery_city='Karachi',
                delivery_phone='03001234567', delivery_lat=24.82 + index / 100,
                delivery_lng=67.03, dispatch_started_at=utcnow(),
                dispatch_deadline_at=utcnow() + timedelta(minutes=5),
            )
            db.session.add(order)
            orders.append(order)
        db.session.commit()
        order_ids = [order.id for order in orders]
        driver_id = driver.id

    login(client, 'multi-order-driver', 'driverpass')
    assert client.post('/driver/available-deliveries', data={
        'order_id': order_ids[0], 'action': 'accept',
    }).status_code == 302
    with app.app_context():
        assert db.session.get(Driver, driver_id).status == 'Busy'

    busy_driver_offers = client.get('/driver/available-deliveries')
    assert f'data-order-id="{order_ids[1]}"'.encode() in busy_driver_offers.data
    assert client.post('/driver/available-deliveries', data={
        'order_id': order_ids[1], 'action': 'accept',
    }).status_code == 302
    with app.app_context():
        assert Order.query.filter_by(driver_id=driver_id, status='Assigned').count() == 2

    for status in ('In Transit', 'Delivered'):
        client.post(f'/driver/update_status/{order_ids[0]}', data={'status': status})
    with app.app_context():
        assert db.session.get(Driver, driver_id).status == 'Busy'

    for status in ('In Transit', 'Delivered'):
        client.post(f'/driver/update_status/{order_ids[1]}', data={'status': status})
    with app.app_context():
        assert db.session.get(Driver, driver_id).status == 'Available'


def test_admin_product_image_is_resized_previewed_and_removable(client, app):
    login(client, 'admin', 'adminpass')
    with app.app_context():
        product = Inventory.query.filter_by(item='Apples').one()
        product_id = product.id

    response = client.post(f'/admin/inventory/{product_id}/edit', data={
        'item': 'Apples', 'description': 'Fresh apples',
        'category_mode': 'existing', 'category': 'Produce',
        'price': '2.50', 'quantity': '10', 'threshold': '2',
        'is_active': '1', 'image': (make_test_image(400, 1200), 'portrait.png'),
    })
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Inventory, product_id)
        stored_name = product.image_filename
        stored_path = Path(app.config['UPLOAD_FOLDER']) / stored_name
        assert stored_name.endswith('.webp')
        assert stored_path.exists()
        with Image.open(stored_path) as image:
            assert image.size == (1200, 900)
            # A portrait product is fitted in full instead of being centre-cropped.
            assert image.convert('RGB').getpixel((10, 10)) == (255, 255, 255)
            assert image.convert('RGB').getpixel((600, 450)) != (255, 255, 255)

    edit_page = client.get(f'/admin/inventory/{product_id}/edit')
    assert b'data-product-image-preview' in edit_page.data
    assert b'Remove current image' in edit_page.data
    response = client.post(f'/admin/inventory/{product_id}/edit', data={
        'item': 'Apples', 'description': 'Fresh apples',
        'category_mode': 'existing', 'category': 'Produce',
        'price': '2.50', 'quantity': '10', 'threshold': '2',
        'is_active': '1', 'remove_product_image': '1',
    })
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Inventory, product_id)
        assert product.image_filename is None
        assert product.image_url is None
        assert not (Path(app.config['UPLOAD_FOLDER']) / stored_name).exists()
