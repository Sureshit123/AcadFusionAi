import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.database import db_instance

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

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
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        # 1. Check for Super Admin (from environment variables)
        env_admin_user = os.environ.get('ADMIN_USERNAME', '').strip()
        env_admin_pass = os.environ.get('ADMIN_PASSWORD', '').strip()
        
        if env_admin_user and env_admin_pass:
            if email.lower() == env_admin_user.lower() and password == env_admin_pass:
                session['user_id'] = 'admin_super_user'
                session['user_name'] = 'Super Admin'
                session['logged_in'] = True
                return redirect(url_for('main.hub'))

        # 2. Check for regular Database User
        user = db_instance.verify_user(email.lower(), password)
        if user:
            session['user_id'] = str(user['_id'])
            session['user_name'] = user['name']
            session['logged_in'] = True
            return redirect(url_for('main.hub'))
        else:
            flash("Invalid email or password.", "error")
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))

