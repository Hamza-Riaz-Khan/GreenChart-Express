"""Create or upgrade a GreenCart database and add optional starter data.

This command is safe to run more than once. It applies pending migrations,
synchronizes the sample catalog unless disabled, and only creates accounts
that do not already exist.
"""

import argparse
import os

from flask_migrate import upgrade
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Driver, User
from seed_catalog import seed_catalog


DEMO_ACCOUNTS = (
    ('admin', 'admin123', 'admin', 'System Administrator'),
    ('customer', 'customer123', 'customer', 'Demo Customer'),
    ('driver', 'driver123', 'driver', 'Demo Driver'),
)


def create_account(username, password, role, name, email=None):
    existing = User.query.filter_by(username=username).first()
    if existing:
        return existing, False
    user = User(
        username=username,
        password=generate_password_hash(password),
        role=role,
        name=name,
        email=email or None,
        approval_status='approved',
    )
    db.session.add(user)
    db.session.flush()
    if role == 'driver':
        db.session.add(Driver(user_id=user.id, status='Available'))
    return user, True


def initialize_database(include_demo_accounts=False, include_catalog=True):
    upgrade()
    created_accounts = []

    if include_catalog:
        seed_catalog()

    if include_demo_accounts:
        for username, password, role, name in DEMO_ACCOUNTS:
            _, created = create_account(username, password, role, name)
            if created:
                created_accounts.append(username)
    else:
        username = os.environ.get('ADMIN_USERNAME', '').strip()
        password = os.environ.get('ADMIN_PASSWORD', '')
        if username and password:
            _, created = create_account(
                username=username,
                password=password,
                role='admin',
                name=os.environ.get('ADMIN_NAME', '').strip() or 'Store Administrator',
                email=os.environ.get('ADMIN_EMAIL', '').strip() or None,
            )
            if created:
                created_accounts.append(username)

    db.session.commit()
    return created_accounts


def main():
    parser = argparse.ArgumentParser(description='Initialize the GreenCart database.')
    parser.add_argument(
        '--demo', action='store_true',
        help='Create local demonstration admin, customer, and driver accounts.',
    )
    parser.add_argument(
        '--skip-catalog', action='store_true',
        help='Apply migrations without adding the starter product catalog.',
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        created = initialize_database(
            include_demo_accounts=args.demo,
            include_catalog=not args.skip_catalog,
        )
        print('Database schema is current.')
        if not args.skip_catalog:
            print('Starter catalog is synchronized.')
        if created:
            print(f"Created accounts: {', '.join(created)}")
        elif args.demo:
            print('Demo accounts already exist; no passwords were changed.')
        elif not os.environ.get('ADMIN_PASSWORD'):
            print('No admin was created. Set ADMIN_USERNAME and ADMIN_PASSWORD, then run again.')


if __name__ == '__main__':
    main()
