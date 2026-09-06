import os
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from models.database import db_instance
from blueprints.auth import admin_required

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/admin')
@admin_required
def dashboard():
    """Renders the main Admin Dashboard HTML page."""
    return render_template('admin/dashboard.html')

@admin_bp.route('/api/admin/stats')
@admin_required
def get_stats():
    """Returns platform-wide statistics for overview cards and recent activity."""
    stats = db_instance.get_platform_stats()
    return jsonify(stats)

@admin_bp.route('/api/admin/users')
@admin_required
def get_users():
    """Returns registered users list with search and status filtering."""
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    users = db_instance.get_all_users(query=q, status_filter=status_filter)
    return jsonify({'users': users})

@admin_bp.route('/api/admin/users/<user_id>')
@admin_required
def get_user_detail(user_id):
    """Returns user profile and analysis history for admin detail view."""
    user = db_instance.get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
        
    user_data = {
        'id': str(user['_id']),
        'name': user.get('name'),
        'email': user.get('email'),
        'role': user.get('role', 'user'),
        'created_at': user.get('created_at'),
        'last_login': user.get('last_login'),
        'account_status': user.get('account_status', 'active')
    }
    
    analyses = db_instance.get_user_analysis_history(user_id, limit=50)
    return jsonify({'user': user_data, 'analyses': analyses})

@admin_bp.route('/api/admin/users/<user_id>/reset_password', methods=['POST'])
@admin_required
def reset_user_password(user_id):
    """Securely resets user password."""
    data = request.json or {}
    new_password = data.get('new_password')
    
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long.'}), 400
        
    success = db_instance.reset_user_password(user_id, new_password)
    if success:
        return jsonify({'success': True, 'message': 'Password reset successfully.'})
    return jsonify({'error': 'Failed to reset password. User not found.'}), 400

@admin_bp.route('/api/admin/users/<user_id>/status', methods=['POST'])
@admin_required
def update_user_status(user_id):
    """Updates account status to active or suspended."""
    data = request.json or {}
    status = data.get('status')
    if status not in ['active', 'suspended']:
        return jsonify({'error': 'Invalid status. Must be active or suspended.'}), 400
        
    success = db_instance.update_user_status(user_id, status)
    if success:
        return jsonify({'success': True, 'status': status})
    return jsonify({'error': 'Failed to update user status.'}), 400

@admin_bp.route('/api/admin/analyses')
@admin_required
def get_analyses():
    """Returns platform-wide analysis history for Admin."""
    q = request.args.get('q', '').strip()
    jobs = db_instance.get_all_analysis_jobs(query_str=q, limit=100)
    return jsonify({'analyses': jobs})

@admin_bp.route('/api/admin/reports')
@admin_required
def get_reports():
    """Returns platform-wide list of generated reports with download links for Admin."""
    q = request.args.get('q', '').strip()
    jobs = db_instance.get_all_analysis_jobs(query_str=q, limit=100)
    reports = []
    for j in jobs:
        reports.append({
            'analysis_id': j.get('job_id'),
            'user_name': j.get('user_name', 'User'),
            'user_email': j.get('user_email', ''),
            'timestamp': j.get('timestamp'),
            'semester': j.get('semester', 'N/A'),
            'department': j.get('department', 'N/A'),
            'result_type': j.get('result_type', 'regular'),
            'student_count': j.get('student_count', 0),
            'excel_url': f"/download/{j.get('job_id')}",
            'pdf_url': f"/download_pdf/{j.get('job_id')}",
            'csv_url': f"/download_csv/{j.get('job_id')}"
        })
    return jsonify({'reports': reports})
