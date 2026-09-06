import os
from flask import Flask
from dotenv import load_dotenv

# Load blueprints
from blueprints.auth import auth_bp
from blueprints.main import main_bp
from blueprints.analyzer import analyzer_bp
from blueprints.timetable import timetable_bp
from blueprints.settings import settings_bp
from blueprints.feedback import feedback_bp
from blueprints.admin import admin_bp
from models.database import db_instance

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_dev_key')

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
        print(f"Admin init warning: {e}")

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000, host="0.0.0.0")
