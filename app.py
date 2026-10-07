import os
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Ensure environment variables are loaded immediately before any blueprints or modules
load_dotenv()

APP_ENV = os.environ.get('APP_ENV', 'development').strip().lower()
IS_PRODUCTION = APP_ENV == 'production'
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', '')

if IS_PRODUCTION:
    required_settings = {
        'FLASK_SECRET_KEY': SECRET_KEY,
        'MONGODB_URI or MONGO_URI': os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI'),
        'ADMIN_EMAIL': os.environ.get('ADMIN_EMAIL'),
        'ADMIN_PASSWORD': os.environ.get('ADMIN_PASSWORD'),
    }
    missing_settings = [name for name, value in required_settings.items() if not value]
    if missing_settings:
        raise RuntimeError(
            'Production configuration is incomplete: ' + ', '.join(missing_settings)
        )
    placeholders = [
        name for name, value in required_settings.items()
        if 'replace-with-' in value.lower() or '<' in value or '>' in value
    ]
    if os.environ.get('ADMIN_EMAIL', '').strip().lower() == 'admin@example.com':
        placeholders.append('ADMIN_EMAIL')
    if placeholders:
        raise RuntimeError(
            'Replace sample production settings before startup: ' + ', '.join(placeholders)
        )
    if len(SECRET_KEY) < 32:
        raise RuntimeError('FLASK_SECRET_KEY must contain at least 32 characters in production.')

if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)

from flask import Flask, render_template, send_file

# Load blueprints
from blueprints.auth import auth_bp
from blueprints.main import main_bp
from blueprints.analyzer import analyzer_bp
from blueprints.timetable import timetable_bp
from blueprints.settings import settings_bp
from blueprints.feedback import feedback_bp
from blueprints.admin import admin_bp
from models.database import db_instance

def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=SECRET_KEY,
        DEBUG=not IS_PRODUCTION and os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes'},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=IS_PRODUCTION,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    @app.context_processor
    def inject_template_globals():
        return {'current_year': datetime.now().year}

    @app.errorhandler(404)
    def page_not_found(_error):
        return render_template('404.html'), 404

    @app.get('/favicon.ico')
    def favicon():
        return send_file(
            os.path.join(app.static_folder, 'img', 'logo.png'),
            mimetype='image/png',
        )

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(analyzer_bp)
    app.register_blueprint(timetable_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(admin_bp)

    # Ensure creator admin user permissions are initialized
    try:
        db_instance.ensure_admin_user()
    except Exception as e:
        app.logger.error("Admin initialization failed (%s).", type(e).__name__)

    if IS_PRODUCTION and (
        db_instance.users is None or db_instance.admin_initialization_failed
    ):
        raise RuntimeError(
            'Production database or administrator initialization failed; check server configuration.'
        )

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=app.config['DEBUG'], port=5000, host="0.0.0.0")
