# GreenCart Express

GreenCart Express is a full-stack Flask demonstration marketplace that covers the complete flow from browsing and checkout to dispatch, live delivery tracking, administration, customer support, and reporting.

The project is designed as a portfolio, university, or product-demonstration application. Payments, wallet funds, refunds, driver earnings, and delivery movement are simulated. It does **not** contact real payment providers or transfer real money.

## Contents

- [Main capabilities](#main-capabilities)
- [How the system works](#how-the-system-works)
- [Technology](#technology)
- [Project structure](#project-structure)
- [Quick start](#quick-start)
- [Fresh database behavior](#fresh-database-behavior)
- [Demo accounts](#demo-accounts)
- [Configuration](#configuration)
- [Database and migrations](#database-and-migrations)
- [Payments and virtual money](#payments-and-virtual-money)
- [Dispatch and delivery tracking](#dispatch-and-delivery-tracking)
- [Images and uploads](#images-and-uploads)
- [Testing](#testing)
- [Production deployment](#production-deployment)
- [Security and limitations](#security-and-limitations)
- [Troubleshooting](#troubleshooting)

## Main capabilities

### Public experience

- Responsive marketing homepage and featured catalog.
- Customer and driver account registration.
- Driver applications with an approval-pending screen.
- Secure sign-in, sign-out, CSRF-protected forms, and role-based authorization.
- Responsive navigation, notification toasts, mobile menus, professional footer, and custom error pages.

### Customer workspace

- Category-organized storefront with search and category filtering.
- Product detail pages with pricing, stock, descriptions, ratings, related products, and quantity controls.
- Persistent database-backed shopping cart that survives sign-out and later sign-in.
- Quantity increase/decrease controls throughout the shopping experience.
- Multi-item checkout with delivery details, phone validation, address selection, and an embedded map.
- Demo payment choices: credit/debit card, JazzCash, Easypaisa, NayaPay, and SadaPay.
- Virtual wallet top-ups that require a demo payment method and confirmation.
- Automatic formatting and server-side validation for card and mobile numbers.
- Coupon codes, loyalty-point redemption, delivery charges, discounts, and balance previews.
- Paid order creation using virtual funds and automatic stock reduction.
- Professional order numbers, invoices, payment summaries, discount savings, and order timelines.
- Cancellation with virtual refunds and inventory restoration instead of deleting order history.
- Embedded live delivery tracking with route progress, ETA, and last-update information.
- Reviews and ratings restricted to products from delivered orders.
- Rewards page with available coupons, loyalty balance, qualification information, and promotional offers.
- Notifications for order events, driver assignments, delivery progress, refunds, and announcements.
- Customer support tickets linked to orders, payments, or deliveries.
- Profile photo, personal information, password management, wallet activity, and account settings.

### Driver workspace

- Driver registration followed by administrator approval.
- Approval status polling so applicants learn when access is granted or declined.
- Available-delivery offer pool shared between eligible drivers.
- Atomic order acceptance: after one driver accepts, the offer disappears for other drivers.
- Support for accepting and carrying multiple active deliveries.
- Automatic Busy status while active work exists and automatic Available status after all work is completed.
- Separate availability control and working-schedule management.
- Browser geolocation sharing with a retry button after permission denial.
- Professional delivery detail pages with customer, package, route, ETA, and driver-fee information.
- Embedded road route from the driver’s current location to the customer destination.
- Recommended multi-stop route queue.
- Delivery status progression from Assigned to In Transit to Delivered.
- Separate completed-delivery history.
- Virtual earnings, daily totals, transaction history, and total earnings in navigation.
- Performance metrics including completed work, average delivery time, and completion rate.
- Driver notifications for offers, assignments, reassignments, cancellations, and approval decisions.
- Support tickets and structured operational issue reports.

### Administrator workspace

- Operational dashboard with revenue, active orders, driver locations, recent orders, and low-stock warnings.
- Complete order management with search, filters, status updates, tracking, cancellation, and refunds.
- Separate assignment and reassignment workflows.
- Manual dispatch remains available alongside driver acceptance and automatic fallback assignment.
- Customer management with profiles, account status, order history, reward points, and virtual-balance adjustment.
- Driver management with approval history, availability, schedule, active workload, performance, earnings, location, and reported issues.
- Separate driver-approval page.
- Inventory management with prices, stock, low-stock thresholds, descriptions, categories, images, and activation status.
- Full product editing, image replacement/removal, image URLs, and instant upload previews.
- Category creation, renaming, activation/deactivation, and product organization.
- Coupon and promotion management with percentage/fixed discounts, minimum spend, expiry, and usage limits.
- Analytics for revenue, order status, popular products, and category performance.
- Announcement broadcasting to customers, drivers, or all active accounts.
- Customer and driver support-ticket management.
- Human-readable audit history for important administrative and automated actions.
- Application settings for store details, delivery fees, minimum order, dispatch timing, demo speed, and maintenance mode.

## How the system works

1. A customer creates an account, browses products, and stores quantities in a persistent cart.
2. Checkout validates delivery details, a map destination, a demo payment method, optional coupons, and reward points.
3. The order is paid immediately from the customer’s virtual balance and receives an invoice and public-facing order code.
4. All eligible on-shift drivers see the delivery offer.
5. The first driver to accept receives the order. If nobody accepts before the configured deadline, the system automatically chooses an eligible driver with the lowest active workload, using proximity as a secondary preference.
6. The driver starts delivery. The simulated marker follows road geometry toward the selected destination while the completed portion of the route disappears.
7. Delivery updates appear in customer, driver, and administrator views.
8. Completion credits virtual driver earnings, closes the delivery, and allows the customer to review delivered products.

Administrators can manually assign or reassign orders at any point where that action is valid.

## Technology

### Backend

- Python
- Flask
- Flask-SQLAlchemy and SQLAlchemy
- Flask-Migrate and Alembic
- Flask-Login
- Flask-WTF CSRF protection
- Werkzeug password hashing and file utilities
- Pillow image normalization
- Requests for road-routing calls

### Frontend

- Server-rendered Jinja templates
- Tailwind CSS through the CDN
- Custom responsive CSS
- Vanilla JavaScript
- Leaflet maps
- OpenStreetMap tiles
- OSRM road routing
- Google Fonts

### Databases

- SQLite by default for local development
- PostgreSQL support through `SQLALCHEMY_DATABASE_URI` and Psycopg for deployment

## Project structure

```text
GreenCart-Express/
├── app/
│   ├── __init__.py              # Flask application factory and extensions
│   ├── models.py                # Database models and display helpers
│   ├── routes.py                # Public, customer, driver, admin, and API routes
│   ├── static/
│   │   ├── app.css              # Application styling
│   │   ├── app.js               # Shared browser behavior
│   │   ├── catalog/              # 30 permanent optimized catalog images
│   │   ├── favicon.png
│   │   └── uploads/             # Runtime user/product uploads; contents ignored
│   └── templates/               # Jinja pages for every role
├── instance/                    # Runtime SQLite databases; contents ignored
├── migrations/                  # Alembic schema history
├── tests/                       # Pytest workflow and authorization tests
├── .env.example                 # Safe configuration template
├── .gitignore
├── config.py                    # Runtime configuration
├── initialize.py                # Idempotent database initializer
├── Procfile                     # Generic Gunicorn start command
├── pytest.ini
├── requirements.txt             # Runtime and deployment dependencies
├── requirements-dev.txt         # Runtime dependencies plus testing tools
├── run.py                       # Flask/Gunicorn application entry point
└── seed_catalog.py              # Idempotent 30-product starter catalog
```

No database, backup, virtual environment, cache, secret `.env` file, or user-uploaded media belongs in version control.

## Quick start

Python 3.11 or newer is recommended.

### Windows PowerShell

```powershell
git clone <your-repository-url>
cd <repository-folder>

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

Copy-Item .env.example .env
python initialize.py --demo
python run.py
```

### macOS or Linux

```bash
git clone <your-repository-url>
cd <repository-folder>

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

cp .env.example .env
python initialize.py --demo
python run.py
```

Open `http://127.0.0.1:5000`.

## Fresh database behavior

Database files are deliberately excluded from Git. Every clone or new deployment starts with no application records.

Initialize a local demonstration database with:

```bash
python initialize.py --demo
```

The initializer:

1. Applies every Alembic migration to the configured empty database.
2. Synchronizes the 30-product starter catalog.
3. Creates the admin, customer, and driver demo accounts only when `--demo` is used.
4. Leaves existing accounts, passwords, orders, and other records unchanged when run again.

For a non-demo database, configure secure administrator variables and run:

```bash
python initialize.py
```

To create only the schema and administrator without starter products:

```bash
python initialize.py --skip-catalog
```

Do not delete or recreate a deployed database on every release. Run the initializer or `flask --app run.py db upgrade`; both migration operations are intended to preserve existing deployed data.

## Demo accounts

These accounts are created only by `python initialize.py --demo`:

| Role | Username | Password |
| --- | --- | --- |
| Administrator | `admin` | `admin123` |
| Customer | `customer` | `customer123` |
| Driver | `driver` | `driver123` |

Never enable these credentials on a public or production deployment.

## Configuration

Copy `.env.example` to `.env` for local development. `.env` is ignored by Git.

| Variable | Required | Purpose |
| --- | --- | --- |
| `FLASK_APP` | Local convenience | Flask CLI entry point; normally `run.py`. |
| `APP_ENV` | Recommended | Use `development` locally and `production` when deployed. |
| `SECRET_KEY` | Required in production | Signs sessions and CSRF tokens. Use a long random value. |
| `SQLALCHEMY_DATABASE_URI` | Optional locally | Defaults to `sqlite:///site.db`; use a managed PostgreSQL URL in production. |
| `ADMIN_USERNAME` | Deployment initialization | Username created by `python initialize.py` when it does not already exist. |
| `ADMIN_PASSWORD` | Deployment initialization | Strong initial administrator password. No admin is created without it. |
| `ADMIN_NAME` | Optional | Display name for the initial administrator. |
| `ADMIN_EMAIL` | Optional | Email for the initial administrator. |

Generate a secret key with Python:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The application refuses to start in production if `SECRET_KEY` is missing.

## Database and migrations

SQLite databases are created under Flask’s `instance/` directory. The directory is retained with `.gitkeep`, while all runtime contents are ignored.

Apply existing migrations:

```bash
flask --app run.py db upgrade
```

After changing a model, generate and review a migration:

```bash
flask --app run.py db migrate -m "describe the schema change"
flask --app run.py db upgrade
```

Show the current revision:

```bash
flask --app run.py db current
```

Important data groups include:

- Users, driver profiles, approval states, schedules, and wallet balances.
- Inventory, categories, product media, stock, and reviews.
- Persistent carts, orders, immutable order-item snapshots, and status timelines.
- Payment transactions, coupons, redemptions, reward points, refunds, and driver earnings.
- Notifications, support tickets, driver issues, audit records, and application settings.
- Route geometry, coordinates, simulation timing, assignment offers, and driver responses.

## Payments and virtual money

All payment behavior is simulated.

- Checkout and wallet top-up forms validate realistic card/mobile-number lengths.
- Spaces are inserted automatically while typing.
- Payment identifiers entered by users are never stored.
- A payment transaction stores only the selected provider, amount, resulting virtual balance, reference, and time.
- A paid order debits the customer’s virtual balance.
- An eligible cancellation creates a refund transaction and returns funds and product stock.
- Driver delivery fees and earnings are virtual.
- Revenue analytics summarize successful virtual order payments.

Supported demonstration labels are credit/debit card, JazzCash, Easypaisa, NayaPay, and SadaPay. There are no Stripe or wallet-provider API calls.

## Dispatch and delivery tracking

Delivery routes use OSRM when available. Requests have a timeout and fall back to a local distance-based estimate if the routing service cannot respond.

Leaflet and OpenStreetMap render maps directly in the application. Tracking does not redirect customers, drivers, or administrators to an external map page.

The demo simulation:

- Calculates customer-facing distance and ETA from the selected location.
- Stores route geometry with the order.
- Starts from the driver’s latest shared location when available.
- Moves the marker along road points rather than a straight flying line.
- Removes the completed section of the displayed route.
- Automatically completes the simulated movement when its accelerated timer finishes.
- Keeps a last-location timestamp for operational views.

The dispatch heartbeat currently runs from authenticated browser pages every 15 seconds. This is appropriate for a demonstration. A production system should move timed dispatch work to a background worker or scheduled job.

## Images and uploads

Accepted formats are PNG, JPG/JPEG, WEBP, and GIF. The application-wide request limit is 5 MB.

- Profile photos are EXIF-corrected, center-cropped, resized to 512 × 512, and stored as WebP.
- Product uploads detect large uniform studio margins, keep the complete subject visible, fit it prominently inside a 1200 × 900 canvas, and store it as WebP.
- The starter catalog ships with 30 original, locally hosted, optimized product images under `app/static/catalog/`; a fresh database references them automatically and does not depend on placeholder-image services.
- Upload previews appear before saving.
- Administrators can replace or remove product images.
- Users can replace or remove profile photos.
- Old local files are removed when they are replaced through the application.

Uploaded files are runtime data and are ignored by Git. Local filesystem uploads may disappear on hosts with ephemeral disks. For a durable production deployment, replace local storage with an object-storage service such as Amazon S3, Cloudinary, or an equivalent provider.

## Testing

Run the complete suite:

```bash
python -m pytest -q
```

The tests use an isolated in-memory database and isolated temporary upload directories. They do not use or modify the local development database.

Coverage includes:

- Safe customer registration and driver applications.
- Approval workflow and role authorization.
- Persistent carts and AJAX add-to-cart behavior.
- Multi-item virtual checkout, payment validation, invoices, and balances.
- Demo wallet top-ups that require a payment method and consent.
- Coupons, rewards, cancellations, refunds, and stock restoration.
- Product and profile image processing.
- Driver multi-order acceptance and automatic availability changes.
- Automatic fallback assignment and atomic offer acceptance.
- Delivery status progression, route simulation, earnings, and history.
- Reviews, notifications, support, admin pages, and access controls.

## Production deployment

### Required production choices

1. Provision a persistent PostgreSQL database.
2. Set `APP_ENV=production`.
3. Set a strong `SECRET_KEY`.
4. Set `SQLALCHEMY_DATABASE_URI` to the provider’s PostgreSQL URL.
5. Set secure `ADMIN_USERNAME` and `ADMIN_PASSWORD` values for the first initialization.
6. Use persistent/object storage if uploaded images must survive deployments.

### Generic build and release commands

Build/install command:

```bash
pip install -r requirements.txt
```

First release or migration command:

```bash
python initialize.py
```

Normal schema-only migration command for later releases:

```bash
flask --app run.py db upgrade
```

Start command:

```bash
gunicorn run:app
```

The included `Procfile` contains the same Gunicorn start command and can be used by compatible hosting platforms.

Do not use SQLite on a multi-instance production deployment. SQLite is suitable for local demonstrations but not for horizontally scaled hosting.

## Security and limitations

Implemented safeguards include password hashing, CSRF protection, role checks, ownership checks, upload type/size validation, normalized uploaded filenames, secure session-cookie settings in production, server-side payment validation, and a required production secret.

Before adapting the project for real commerce, add or replace:

- A real PCI-compliant payment provider and webhook verification.
- Transactional email/SMS/push delivery.
- Background jobs for dispatch, notifications, and long-running work.
- Object storage and upload malware scanning.
- Rate limiting, login throttling, password recovery, and optional multi-factor authentication.
- Structured logging, error monitoring, backups, observability, and disaster recovery.
- A production Tailwind build instead of the CDN.
- Provider terms, privacy policy, accessibility audit, and legal/compliance review.
- Expanded unit, integration, browser, load, and security testing.

This repository must not be represented as processing real money. The UI clearly identifies virtual transactions and requires customer confirmation.

## Troubleshooting

### The database or tables do not exist

```bash
python initialize.py --demo
```

For a non-demo deployment, set the administrator environment variables and omit `--demo`.

### The app reports that the secret key is missing

Set `SECRET_KEY` in the deployment environment. It is mandatory when `APP_ENV=production`.

### PowerShell will not activate the virtual environment

For the current terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Maps are blank

The browser must be online because Leaflet assets, OpenStreetMap tiles, and routing data are loaded from external services. Check browser developer tools for blocked content or network errors.

### Browser location is denied

Change location permission for the site in the browser, then use the driver dashboard’s location-sharing button again.

### Product or profile uploads disappear after deployment

The host likely uses an ephemeral filesystem. Configure durable object storage before relying on uploaded media in production.

### A deployment receives a PostgreSQL URL beginning with `postgres://`

`config.py` automatically normalizes `postgres://` and `postgresql://` URLs to the Psycopg SQLAlchemy driver format.

---

GreenCart Express is intentionally a demonstration system: the shopping, operations, and delivery workflows are functional, while financial transactions remain completely virtual.
