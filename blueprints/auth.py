import os
import functools
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import db_instance

auth_bp = Blueprint('auth', __name__)

def is_admin():
    """Helper function to check if current logged in user has admin privileges."""
    if not session.get('user_id'):
        return False
    if session.get('user_id') == 'admin_super_user':
        return True
    return session.get('user_role') == 'admin'

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized: Login required'}), 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized: Login required'}), 401
            return redirect(url_for('auth.login'))
        if not is_admin():
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden: Admin access required'}), 403
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for('main.hub'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not name or not email or not password:
            flash("All fields are required.", "error")
            return render_template('auth/signup.html')
        
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template('auth/signup.html')

        if db_instance.get_user_by_email(email):
            flash("Email already registered.", "error")
            return render_template('auth/signup.html')

        if db_instance.create_user(name, email, password):
            flash("Account created successfully. Please login.", "success")
            return redirect(url_for('auth.login'))
        else:
            flash("Failed to create account. Try again.", "error")
    
    return render_template('auth/signup.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('main.hub'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        # 1. Check for Super Admin (from environment variables)
        env_admin_user = os.environ.get('ADMIN_USERNAME')
        env_admin_pass = os.environ.get('ADMIN_PASSWORD')
        
        if env_admin_user and env_admin_pass:
            if email == env_admin_user and password == env_admin_pass:
                session['user_id'] = 'admin_super_user'
                session['user_name'] = 'Super Admin'
                session['user_role'] = 'admin'
                session['logged_in'] = True
                return redirect(url_for('main.hub'))

        # 2. Check for regular Database User
        user = db_instance.verify_user(email, password)
        if user:
            session['user_id'] = str(user['_id'])
            session['user_name'] = user['name']
            session['user_email'] = user.get('email', '')
            session['user_role'] = user.get('role', 'user')
            session['logged_in'] = True
            return redirect(url_for('main.hub'))
        else:
            flash("Invalid email/password or account is suspended.", "error")
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


