from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect, CSRFError

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()

def create_app(config_object='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_object)
    if not app.config.get('SECRET_KEY'):
        raise RuntimeError('SECRET_KEY must be configured when APP_ENV=production.')

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)

    # Set the login view for unauthorized users
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'info'

    from .routes import main
    app.register_blueprint(main)

    @app.template_filter('money')
    def money(cents):
        return f"${(cents or 0) / 100:,.2f}"

    app.jinja_env.filters['currency'] = money

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        from flask import flash, redirect, request, url_for
        flash('Your form expired. Please try again.', 'danger')
        return redirect(request.referrer or url_for('main.home'))

    @app.errorhandler(403)
    def forbidden(error):
        from flask import render_template
        return render_template(
            'error.html', code=403, title='Access denied',
            message='You do not have permission to open this page.'
        ), 403

    @app.errorhandler(404)
    def not_found(error):
        from flask import render_template
        return render_template(
            'error.html', code=404, title='Page not found',
            message='The page may have moved or the link may be outdated.'
        ), 404

    return app
