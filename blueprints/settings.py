from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from models.database import db_instance

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings')
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('settings/index.html')

@settings_bp.route('/api/settings/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.method == 'POST':
        data = request.json
        success = db_instance.save_institution_profile(user_id, data)
        return jsonify({'success': success})
    
    profile_data = db_instance.get_institution_profile(user_id)
    return jsonify(profile_data or {})

@settings_bp.route('/api/settings/departments', methods=['GET', 'POST', 'PUT', 'DELETE'])
def departments():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.method == 'POST':
        data = request.json
        dept_id = db_instance.add_department(user_id, data)
        return jsonify({'success': bool(dept_id), 'id': dept_id})
    elif request.method == 'PUT':
        dept_id = request.args.get('id')
        if not dept_id: return jsonify({'error': 'Missing id'}), 400
        data = request.json
        success = db_instance.update_department(user_id, dept_id, data)
        return jsonify({'success': success})
    elif request.method == 'DELETE':
        dept_id = request.args.get('id')
        if not dept_id: return jsonify({'error': 'Missing id'}), 400
        success = db_instance.delete_department(user_id, dept_id)
        return jsonify({'success': success})
    
    depts = db_instance.get_departments(user_id)
    return jsonify(depts)

@settings_bp.route('/api/settings/faculty', methods=['GET', 'POST', 'DELETE'])
def faculty():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.method == 'POST':
        data = request.json
        fac_id = db_instance.add_faculty(user_id, data)
        return jsonify({'success': bool(fac_id), 'id': fac_id})
    elif request.method == 'DELETE':
        fac_id = request.args.get('id')
        if not fac_id: return jsonify({'error': 'Missing id'}), 400
        success = db_instance.delete_faculty(user_id, fac_id)
        return jsonify({'success': success})
    
    faculties = db_instance.get_faculty_members(user_id)
    return jsonify(faculties)

@settings_bp.route('/api/settings/subjects', methods=['GET', 'POST', 'PUT', 'DELETE'])
def subjects():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.method == 'POST':
        data = request.json
        sub_id = db_instance.add_subject(user_id, data)
        return jsonify({'success': bool(sub_id), 'id': sub_id})
    elif request.method == 'PUT':
        sub_id = request.args.get('id')
        if not sub_id: return jsonify({'error': 'Missing id'}), 400
        data = request.json
        success = db_instance.update_subject(user_id, sub_id, data)
        return jsonify({'success': success})
    elif request.method == 'DELETE':
        sub_id = request.args.get('id')
        if not sub_id: return jsonify({'error': 'Missing id'}), 400
        success = db_instance.delete_subject(user_id, sub_id)
        return jsonify({'success': success})
    
    # Filter subjects by department/semester/scheme if requested
    query_filter = {}
    dept = request.args.get('department')
    sem = request.args.get('semester')
    ay = request.args.get('academic_year')
    scheme = request.args.get('scheme')
    if dept: query_filter['department'] = dept
    if sem: query_filter['semester'] = int(sem) if sem.isdigit() else sem
    if ay: query_filter['academic_year'] = ay
    if scheme: query_filter['scheme'] = scheme
        
    subs = db_instance.get_subjects(user_id, query_filter)
    return jsonify(subs)

@settings_bp.route('/api/settings/defaults', methods=['GET', 'POST'])
def defaults():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.method == 'POST':
        data = request.json
        success = db_instance.save_report_defaults(user_id, data)
        return jsonify({'success': success})
    
    report_defaults = db_instance.get_report_defaults(user_id)
    return jsonify(report_defaults or {})
