import os
import secrets
from dotenv import load_dotenv

load_dotenv()

class Config:
    APP_ENV = os.environ.get('APP_ENV', 'development')
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY and APP_ENV != 'production':
        # An unpredictable per-process key is safe for local development. Set a
        # persistent value in .env to keep sessions alive across restarts.
        SECRET_KEY = secrets.token_hex(32)
    database_url = os.environ.get('SQLALCHEMY_DATABASE_URI') or 'sqlite:///site.db'
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    SQLALCHEMY_DATABASE_URI = database_url
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'app', 'static', 'uploads', 'products')
    PROFILE_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'app', 'static', 'uploads', 'profiles')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = APP_ENV == 'production'
    WTF_CSRF_TIME_LIMIT = 3600
