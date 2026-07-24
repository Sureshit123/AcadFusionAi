import os
import threading
import uuid
import re
from flask import Blueprint, render_template, request, jsonify, send_file, session, redirect, url_for
import requests
from scraper import initialize_scrape, complete_scrape, get_headers
from processor import generate_excel_report
from models.database import db_instance
import time

analyzer_bp = Blueprint('analyzer', __name__)

# JOBS store - in production this would be Redis/DB
JOBS = {}

def background_scraper(job_id, usn_list, user_id, is_mock=None):
    JOBS[job_id].update({
        'total': len(usn_list),
        'completed': 0,
        'results': [],
        'status': 'Running',
        'current_usn': ''
    })
    
    # Persistent Session for the entire job
    job_session = requests.Session()
    job_session.verify = False
    job_session.headers.update(get_headers())
    
    for usn in usn_list:
        JOBS[job_id]['current_usn'] = usn
        
        while True:
            JOBS[job_id]['status'] = f'Initializing {usn}...'
            req_session, b64_captcha, token_dict, err = initialize_scrape(usn, mock=is_mock, session=job_session)
            
            if err or not req_session or not b64_captcha:
                JOBS[job_id]['results'].append({"usn": usn, "status": f"Init Error: {err}"})
                JOBS[job_id]['completed'] += 1
                break # Move to next USN on network error
                
            JOBS[job_id]['captcha_base64'] = b64_captcha
            JOBS[job_id]['current_session'] = req_session
            JOBS[job_id]['token_dict'] = token_dict
            JOBS[job_id]['captcha_solved'] = False
            
            # Simulation Mode: Auto-solve captcha
            from scraper import VTU_MOCK_MODE
            effective_mock = is_mock if is_mock is not None else VTU_MOCK_MODE
            if effective_mock:
                JOBS[job_id]['captcha_solved'] = True
                JOBS[job_id]['captcha_text'] = 'SIM'
            else:
                JOBS[job_id]['status'] = 'Waiting for Captcha'
            
            timeout_loops = 0
            while not JOBS[job_id]['captcha_solved']:
                time.sleep(1)
                timeout_loops += 1
                if timeout_loops > 60: break
                    
            if not JOBS[job_id]['captcha_solved']:
                 JOBS[job_id]['results'].append({"usn": usn, "status": "Captcha Timeout"})
                 JOBS[job_id]['completed'] += 1
                 break
                 
            JOBS[job_id]['status'] = f'Scraping {usn}...'
            res_dict = complete_scrape(usn, req_session, token_dict, JOBS[job_id]['captcha_text'], mock=is_mock)
            
            if res_dict.get('status') == 'Invalid Captcha':
                JOBS[job_id]['status'] = 'Invalid Captcha. Retrying student...'
                JOBS[job_id]['captcha_solved'] = False
                time.sleep(1)
                continue # Retry SAME USN
                
            if res_dict.get('status') == 'Busy/Redirect':
                JOBS[job_id]['status'] = 'VTU Busy. Retrying student...'
                JOBS[job_id]['captcha_solved'] = False
                time.sleep(3) # Wait longer for redirect/busy
                continue
                
            JOBS[job_id]['results'].append(res_dict)
            JOBS[job_id]['completed'] += 1
            break # Success or terminal error, move to next USN
            
        time.sleep(1)
        
    JOBS[job_id]['status'] = 'Processing Excel'
    try:
        excel_data = generate_excel_report(JOBS[job_id]['results'])
        JOBS[job_id]['excel_file'] = excel_data.getvalue()  # Store as bytes, not BytesIO
        JOBS[job_id]['status'] = 'Completed'
        
        # Save to MongoDB for persistence
        db_instance.save_analysis_job(job_id, usn_list, JOBS[job_id]['results'], user_id)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        JOBS[job_id]['status'] = f'Error during Excel generation: {str(e)}'

def expand_usn_range(start_usn, count):
    usn_list = []
    match = re.match(r"([1-4][A-Z]{2}\d{2}[A-Z]{2})(\d{3})", start_usn, re.IGNORECASE)
    if not match: return [start_usn]
    prefix = match.group(1).upper()
    start_num = int(match.group(2))
    for i in range(count):
        usn_list.append(f"{prefix}{(start_num + i):03d}")
    return usn_list

@analyzer_bp.route('/analyzer')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # Pass config to check for Simulation Mode in UI
    from scraper import VTU_MOCK_MODE
    current_mode = session.get('use_mock')
    if current_mode is None: current_mode = VTU_MOCK_MODE
    
    return render_template('analyzer/dashboard.html', config={'VTU_MOCK_MODE': current_mode})

@analyzer_bp.route('/api/start_analysis', methods=['POST'])
def start_analysis():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    job_id = str(uuid.uuid4())
    usn_list = []

    if 'file' in request.files and request.files['file'].filename:
        file = request.files['file']
        filename = file.filename.lower()
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            import pandas as pd
            try:
                df = pd.read_excel(file, header=None)
                if not df.empty:
                    usn_pattern = re.compile(r"^[1-4][a-zA-Z]{2}\d{2}[a-zA-Z]{2}\d{3}$")
                    for col_idx in range(df.shape[1]):
                        col_data = df.iloc[:, col_idx].dropna().astype(str).str.strip().str.upper()
                        valid_usns = [val for val in col_data if usn_pattern.match(val)]
                        if valid_usns:
                            usn_list = valid_usns
                            break
            except Exception: return jsonify({'error': 'Failed to read Excel'}), 400
        else:
            content = file.read().decode('utf-8', errors='ignore').splitlines()
            usn_list = [line.split(',')[0].strip() for line in content if line.strip()]
    else:
        start_usn = request.form.get('start_usn')
        count = int(request.form.get('count', 1))
        if start_usn: usn_list = expand_usn_range(start_usn, count)
            
    if not usn_list: return jsonify({'error': 'No input provided'}), 400

    is_mock = session.get('use_mock')
    JOBS[job_id] = {'excel_file': None, 'captcha_solved': True} 
    thread = threading.Thread(target=background_scraper, args=(job_id, usn_list, session.get('user_id'), is_mock))
    thread.daemon = True
    thread.start()
    return jsonify({'job_id': job_id})

@analyzer_bp.route('/api/progress/<job_id>')
def get_progress(job_id):
    job = JOBS.get(job_id)
    if not job: return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'status': job.get('status'),
        'total': job.get('total', 0),
        'completed': job.get('completed', 0),
        'current_usn': job.get('current_usn'),
        'captcha_base64': job.get('captcha_base64') if job.get('status') == 'Waiting for Captcha' else None
    })

@analyzer_bp.route('/api/submit_captcha/<job_id>', methods=['POST'])
def submit_captcha(job_id):
    job = JOBS.get(job_id)
    if not job: return jsonify({'error': 'Job not found'}), 404
    captcha = request.json.get('captcha')
    if captcha:
        job['captcha_text'] = captcha
        job['captcha_solved'] = True
        return jsonify({'success': True})
    return jsonify({'error': 'No captcha provided'}), 400

@analyzer_bp.route('/download/<job_id>')
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get('excel_file'): return "File not found", 404
    # Wrap bytes in a fresh BytesIO object to prevent "closed file" errors
    import io
    file_obj = io.BytesIO(job['excel_file'])
    file_obj.seek(0)
    return send_file(
        file_obj,
        as_attachment=True,
        download_name=f'VTU_Results_{job_id[:8]}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@analyzer_bp.route('/api/history')
def get_history():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    history = db_instance.get_user_analysis_history(session['user_id'])
    return jsonify(history)

@analyzer_bp.route('/download_history/<job_id>')
def download_history(job_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    
    # Check JOBS cache first
    job = JOBS.get(job_id)
    if job and job.get('excel_file'):
        # Wrap bytes in a fresh BytesIO object to prevent "closed file" errors
        import io
        file_obj = io.BytesIO(job['excel_file'])
        file_obj.seek(0)
        return send_file(
            file_obj,
            as_attachment=True,
            download_name=f'VTU_Results_{job_id[:8]}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    # Fallback to Database
    db_job = db_instance.get_analysis_job_results(job_id)
    if not db_job: return "Job Not Found", 404
    
    try:
        excel_data = generate_excel_report(db_job['results'])
        return send_file(
            excel_data,
            as_attachment=True,
            download_name=f'VTU_Results_{job_id[:8]}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return f"Error regenerating report: {str(e)}", 500

@analyzer_bp.route('/api/toggle_mock', methods=['POST'])
def toggle_mock():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    use_mock = request.json.get('use_mock')
    session['use_mock'] = use_mock
    return jsonify({'success': True, 'current_mode': 'Simulation' if use_mock else 'Live'})

@analyzer_bp.route('/api/job_results/<job_id>')
def get_job_results(job_id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    # Check cache first
    job = JOBS.get(job_id)
    if job and job.get('results'):
        return jsonify(job['results'])
    
    # Fallback to DB
    db_job = db_instance.get_analysis_job_results(job_id)
    if db_job: return jsonify(db_job['results'])
    
    return jsonify({'error': 'Job results not found'}), 404
