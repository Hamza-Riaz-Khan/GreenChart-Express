import pytest
from pathlib import Path

from app import create_app, db
from app.models import Inventory, User
from config import Config
from werkzeug.security import generate_password_hash


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = 'test-only-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    upload_folder = Path(app.instance_path) / 'test-profile-uploads'
    product_upload_folder = Path(app.instance_path) / 'test-product-uploads'
    upload_folder.mkdir(parents=True, exist_ok=True)
    product_upload_folder.mkdir(parents=True, exist_ok=True)
    app.config['PROFILE_UPLOAD_FOLDER'] = str(upload_folder)
    app.config['UPLOAD_FOLDER'] = str(product_upload_folder)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(
                username='admin', name='Admin', role='admin',
                password=generate_password_hash('adminpass'),
            ),
            User(
                username='customer', name='Customer', role='customer',
                address='Karachi', password=generate_password_hash('customerpass'),
            ),
            Inventory(item='Apples', price_cents=250, quantity=10, category='Produce'),
            Inventory(item='Milk', price_cents=400, quantity=5, category='Dairy'),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()
    for uploaded_file in upload_folder.iterdir():
        if uploaded_file.is_file():
            uploaded_file.unlink()
    for uploaded_file in product_upload_folder.iterdir():
        if uploaded_file.is_file():
            uploaded_file.unlink()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    return client.post('/login', data={'username': username, 'password': password})
