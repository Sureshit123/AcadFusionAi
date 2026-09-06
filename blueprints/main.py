from flask import Blueprint, render_template, session, redirect, url_for, jsonify
from models.database import db_instance

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('main.hub'))
    return redirect(url_for('auth.login'))

@main_bp.route('/hub')
def hub():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    return render_template('hub.html', name=session.get('user_name'))

@main_bp.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    return render_template('history.html')

@main_bp.route('/api/history/all')
def get_all_history():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    results = db_instance.get_user_analysis_history(user_id)
    timetables = db_instance.get_user_timetable_history(user_id)
    return jsonify({
        'results': results,
        'timetables': timetables
    })

@main_bp.route('/api/history/delete_analysis/<id>', methods=['POST', 'DELETE'])
def delete_analysis(id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    success = db_instance.delete_analysis_job(id, session['user_id'])
    return jsonify({'success': success})

@main_bp.route('/api/history/delete_timetable/<id>', methods=['POST', 'DELETE'])
def delete_timetable(id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    success = db_instance.delete_timetable_cycle(id, session['user_id'])
    return jsonify({'success': success})
