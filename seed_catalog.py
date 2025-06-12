import re

from app import create_app, db
from app.models import Inventory


CATALOG = [
    ('Apples', 'Produce', 'Crisp, naturally sweet red apples.', 249, 100),
    ('Bananas', 'Produce', 'Ripe bananas for snacks and smoothies.', 179, 80),
    ('Oranges', 'Produce', 'Juicy citrus packed with vitamin C.', 299, 65),
    ('Tomatoes', 'Produce', 'Fresh tomatoes for salads and cooking.', 189, 55),
    ('Milk', 'Dairy & Chilled', 'Fresh full-cream milk, one litre.', 329, 45),
    ('Eggs', 'Dairy & Chilled', 'Farm fresh eggs, pack of twelve.', 449, 40),
    ('Ice Cream', 'Dairy & Chilled', 'Creamy vanilla family tub.', 699, 24),
    ('Cheddar Cheese', 'Dairy & Chilled', 'Rich sliced cheddar cheese.', 579, 30),
    ('Bread', 'Bakery', 'Soft baked sandwich loaf.', 219, 35),
    ('Chocolate Cookies', 'Bakery', 'Crunchy cookies with chocolate chips.', 349, 42),
    ('Basmati Rice', 'Pantry', 'Premium long-grain basmati rice, 2 kg.', 899, 38),
    ('Cooking Oil', 'Pantry', 'Everyday cooking oil, one litre.', 649, 44),
    ('Ground Coffee', 'Beverages', 'Medium roast ground coffee.', 849, 27),
    ('Orange Juice', 'Beverages', 'Refreshing orange juice, one litre.', 399, 33),
    ('Wireless Headphones', 'Electronics', 'Comfortable Bluetooth headphones.', 5999, 18),
    ('Wireless Mouse', 'Electronics', 'Compact silent-click wireless mouse.', 2299, 22),
    ('Mechanical Keyboard', 'Electronics', 'Tactile backlit mechanical keyboard.', 7499, 14),
    ('Power Bank', 'Electronics', 'Fast-charging 10,000 mAh power bank.', 3999, 20),
    ('Smart Watch', 'Electronics', 'Fitness tracking and phone notifications.', 8999, 12),
    ('Table Lamp', 'Home & Living', 'Warm LED desk and bedside lamp.', 2799, 19),
    ('Cotton Pillow', 'Home & Living', 'Soft breathable everyday pillow.', 1599, 26),
    ('Water Bottle', 'Home & Living', 'Reusable insulated steel bottle.', 1299, 31),
    ('Laundry Detergent', 'Household', 'Concentrated detergent for bright laundry.', 749, 36),
    ('Dishwashing Liquid', 'Household', 'Grease-cutting lemon dish liquid.', 299, 48),
    ('Shampoo', 'Personal Care', 'Gentle daily-care shampoo.', 549, 34),
    ('Hand Wash', 'Personal Care', 'Moisturising liquid hand wash.', 349, 41),
    ('Toothpaste', 'Personal Care', 'Fresh mint fluoride toothpaste.', 279, 52),
    ('Notebook Set', 'Stationery', 'Three premium ruled notebooks.', 599, 29),
    ('Gel Pen Pack', 'Stationery', 'Smooth-writing gel pens, pack of five.', 399, 46),
    ('Everyday Backpack', 'Fashion', 'Lightweight backpack with laptop sleeve.', 3499, 16),
]


def catalog_image_url(name):
    filename = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return f'/static/catalog/{filename}.webp'


def seed_catalog():
    created = 0
    for name, category, description, price, quantity in CATALOG:
        product = Inventory.query.filter_by(item=name).first()
        if not product:
            product = Inventory(item=name)
            db.session.add(product)
            created += 1
        product.category = category
        product.description = description
        product.price_cents = price
        product.quantity = max(product.quantity or 0, quantity)
        product.low_stock_threshold = 10
        product.is_active = True
        if not product.image_filename and (
            not product.image_url or product.image_url.startswith('https://placehold.co/')
        ):
            product.image_url = catalog_image_url(name)
    db.session.commit()
    return created


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        count = seed_catalog()
        print(f'Catalog ready: {count} products added, {len(CATALOG)} products synchronized.')
