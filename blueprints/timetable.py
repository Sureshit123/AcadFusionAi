import random
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from models.database import db_instance
import pandas as pd
import io
import time
from bson import ObjectId

timetable_bp = Blueprint('timetable', __name__)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
SLOTS = ["09:00-10:00", "10:00-11:00", "11:15-12:15", "12:15-01:15", "02:15-03:15", "03:15-04:15", "04:15-05:15"]

class DepartmentCycleGenerator:
    def __init__(self, cycle_type, semesters_data, max_physical_labs, schedules_cache=None):
        self.cycle_type = cycle_type # 'odd' or 'even'
        self.semesters_data = semesters_data # { '3': {subjects, labs, fixed, sat_holiday}, ... }
        self.max_physical_labs = max_physical_labs
        self.schedules_cache = schedules_cache or {}
        self.grids = {sem: [[None for _ in range(len(SLOTS))] for _ in range(6)] for sem in semesters_data}
        
        # Shared pool tracking [day][slot] -> {teacher_name: activity_type}
        self.global_teacher_usage = [[{} for _ in range(len(SLOTS))] for _ in range(6)]
        self.global_lab_usage = [[0 for _ in range(len(SLOTS))] for _ in range(6)]
        # Track batch usage [sem][day]
        self.batch_lab_usage = {sem: {"B1": set(), "B2": set()} for sem in semesters_data}

    def is_resource_free(self, teachers, activity_type, day, slot_idx, sem=None, batch=None):
        needs_lab = (activity_type == 'lab')
        # 0. Check Saturday Constraint (College closes at 1:15 PM / Slot 3)
        if day == 5: # Saturday
            if slot_idx > 3: return False
            if needs_lab and (slot_idx + 1) > 3: return False

        # 1. Check Teacher availability and Gap constraints
        for t in teachers:
            if not t: continue
            
            # Basic Availability (Across all semesters in this cycle)
            if t in self.global_teacher_usage[day][slot_idx]:
                return False
            
            # External Schedule Availability
            global_busy = self.schedules_cache.get(t, [])
            for busy in global_busy:
                if busy['day'] == day and busy['slot'] == slot_idx:
                    return False

            # NEW: Teacher Gap Constraint for Subject classes
            if activity_type == 'subject':
                # Check for other subjects on the same day
                # A resting slot (Empty or Fixed) must exist between any two subject classes
                
                # Check backwards for previous subject
                for s_prev in range(slot_idx - 1, -1, -1):
                    prev_activity = self.global_teacher_usage[day][s_prev].get(t)
                    if prev_activity == 'subject':
                        # Found another subject class. Check for a gap between them.
                        has_gap = False
                        for s_bet in range(s_prev + 1, slot_idx):
                            # Is slot s_bet a gap for this teacher?
                            # A gap is either Empty (None or not in usage) or a Fixed slot.
                            activity_bet = self.global_teacher_usage[day][s_bet].get(t)
                            if activity_bet is None or activity_bet == 'fixed':
                                has_gap = True
                                break
                        if not has_gap: return False
                        break # Found nearest subject, gap check done
                
                # Check forwards for next subject
                for s_next in range(slot_idx + 1, len(SLOTS)):
                    next_activity = self.global_teacher_usage[day][s_next].get(t)
                    if next_activity == 'subject':
                        has_gap = False
                        for s_bet in range(slot_idx + 1, s_next):
                            activity_bet = self.global_teacher_usage[day][s_bet].get(t)
                            if activity_bet is None or activity_bet == 'fixed':
                                has_gap = True
                                break
                        if not has_gap: return False
                        break

        # 2. Check Physical Lab Room
        if needs_lab:
            if self.global_lab_usage[day][slot_idx] >= self.max_physical_labs:
                return False
            # Check if this batch already had a lab today
            if sem and batch and day in self.batch_lab_usage[sem][batch]:
                return False
        
        return True

    def mark_busy(self, teachers, activity_type, day, slot_idx, sem=None, batch=None):
        used_lab = (activity_type == 'lab')
        for t in teachers:
            if t: self.global_teacher_usage[day][slot_idx][t] = activity_type
        if used_lab:
            self.global_lab_usage[day][slot_idx] += 1
            if sem and batch:
                self.batch_lab_usage[sem][batch].add(day)

    def generate(self, max_attempts=20):
        for attempt in range(max_attempts):
            result = self._attempt_generate()
            if result[0] is not None:
                print(f"Algorithm: Solution found on attempt {attempt + 1}")
                return result
        return None, "All attempts failed. Try reducing constraints or increasing physical labs."

    def _attempt_generate(self):
        # Reset grids and usage trackers for each attempt
        self.grids = {sem: [[None for _ in range(len(SLOTS))] for _ in range(6)] for sem in self.semesters_data}
        self.global_teacher_usage = [[{} for _ in range(len(SLOTS))] for _ in range(6)]
        self.global_lab_usage = [[0 for _ in range(len(SLOTS))] for _ in range(6)]
        self.batch_lab_usage = {sem: {"B1": set(), "B2": set()} for sem in self.semesters_data}
        # 1. Place Fixed Slots
        for sem, data in self.semesters_data.items():
            fixed = data.get('fixed', [])
            for fs in fixed:
                d = DAYS.index(fs['day'])
                s = int(fs['slot_idx'])
                self.grids[sem][d][s] = {"type": "fixed", "name": fs['name'], "teacher": fs.get('teacher', 'N/A')}
                # Ensure fixed slots mark teacher as busy globally
                if fs.get('teacher'):
                    self.mark_busy([fs['teacher']], 'fixed', d, s)

        # 2. Place Labs (Semester-wise Pairing)
        # We group labs by semester to support parallel batching (React/ML at same time)
        for sem, data in self.semesters_data.items():
            sem_labs = list(data.get('labs', []))
            random.shuffle(sem_labs)
            
            # Pair them up
            pairs = []
            while len(sem_labs) >= 2:
                pairs.append((sem_labs.pop(0), sem_labs.pop(0)))
            if sem_labs:
                pairs.append((sem_labs.pop(0), None)) # Singleton
            
            sat_holiday = data.get('sat_holiday', False)
            num_days = 5 if sat_holiday else 6
            
            for lab_a, lab_b in pairs:
                placed = False
                # Try all combinations for Session 1 (D1, S1) and Session 2 (D2, S2)
                # For Session 1: B1(A) and B2(B)
                # For Session 2: B1(B) and B2(A)
                
                # Avoid spanning the 11:00 AM break (between Slot 1 and Slot 2)
                potential_starts = [0, 2, 4, 5]
                random.shuffle(potential_starts)
                
                possible_slots = []
                for d in range(num_days):
                    for s in potential_starts:
                        possible_slots.append((d, s))
                random.shuffle(possible_slots)

                for (d1, s1) in possible_slots:
                    if self.grids[sem][d1][s1] is not None or self.grids[sem][d1][s1+1] is not None: continue
                    
                    # Availability check for Session 1
                    t_a = lab_a.get('teachers', [])
                    t_b = lab_b.get('teachers', []) if lab_b else []
                    
                    # Check resources for BOTH labs in Session 1
                    if not self.is_resource_free(t_a, 'lab', d1, s1, sem, "B1") or \
                       not self.is_resource_free(t_a, 'lab', d1, s1+1, sem, "B1"): continue
                    
                    if lab_b:
                        if not self.is_resource_free(t_b, 'lab', d1, s1, sem, "B2") or \
                           not self.is_resource_free(t_b, 'lab', d1, s1+1, sem, "B2"): continue
                        # Check physical rooms (needs 2 rooms)
                        if self.global_lab_usage[d1][s1] + 1 >= self.max_physical_labs: continue
                        if self.global_lab_usage[d1][s1+1] + 1 >= self.max_physical_labs: continue

                    # Valid Session 1 found! Now find Session 2
                    for (d2, s2) in possible_slots:
                        if d2 == d1: continue # Must be different day
                        if self.grids[sem][d2][s2] is not None or self.grids[sem][d2][s2+1] is not None: continue
                        
                        # Availability check for Session 2: B1(B) and B2(A)
                        if not self.is_resource_free(t_a, 'lab', d2, s2, sem, "B2") or \
                           not self.is_resource_free(t_a, 'lab', d2, s2+1, sem, "B2"): continue
                        
                        if lab_b:
                            if not self.is_resource_free(t_b, 'lab', d2, s2, sem, "B1") or \
                               not self.is_resource_free(t_b, 'lab', d2, s2+1, sem, "B1"): continue
                            if self.global_lab_usage[d2][s2] + 1 >= self.max_physical_labs: continue
                            if self.global_lab_usage[d2][s2+1] + 1 >= self.max_physical_labs: continue
                        
                        # BOTH SESSIONS VALID - PLACE THEM
                        # Day 1
                        self.grids[sem][d1][s1] = {"type": "lab", "name": lab_a['name'], "batch": "B1", "teachers": ", ".join(t_a), "part": 1, 
                                                 "parallel": lab_b['name'] if lab_b else None, "b2_teachers": ", ".join(t_b) if lab_b else None}
                        self.grids[sem][d1][s1+1] = {"type": "lab", "name": lab_a['name'], "batch": "B1", "teachers": ", ".join(t_a), "part": 2, 
                                                   "parallel": lab_b['name'] if lab_b else None, "b2_teachers": ", ".join(t_b) if lab_b else None}
                        self.mark_busy(t_a, 'lab', d1, s1, sem, "B1"); self.mark_busy(t_a, 'lab', d1, s1+1, sem, "B1")
                        if lab_b:
                            self.mark_busy(t_b, 'lab', d1, s1, sem, "B2"); self.mark_busy(t_b, 'lab', d1, s1+1, sem, "B2")

                        # Day 2
                        self.grids[sem][d2][s2] = {"type": "lab", "name": (lab_b['name'] if lab_b else lab_a['name']), "batch": "B1" if lab_b else "B2", 
                                                 "teachers": ", ".join(t_b if lab_b else t_a), "part": 1, 
                                                 "parallel": lab_a['name'] if lab_b else None, "b2_teachers": ", ".join(t_a) if lab_b else None}
                        self.grids[sem][d2][s2+1] = {"type": "lab", "name": (lab_b['name'] if lab_b else lab_a['name']), "batch": "B1" if lab_b else "B2", 
                                                   "teachers": ", ".join(t_b if lab_b else t_a), "part": 2, 
                                                   "parallel": lab_a['name'] if lab_b else None, "b2_teachers": ", ".join(t_a) if lab_b else None}
                        self.mark_busy(t_b if lab_b else t_a, 'lab', d2, s2, sem, "B1" if lab_b else "B2")
                        self.mark_busy(t_b if lab_b else t_a, 'lab', d2, s2+1, sem, "B1" if lab_b else "B2")
                        if lab_b:
                            self.mark_busy(t_a, 'lab', d2, s2, sem, "B2"); self.mark_busy(t_a, 'lab', d2, s2+1, sem, "B2")
                        
                        placed = True; break
                    if placed: break
                if not placed: return None, f"Could not schedule lab {lab_a['name']} for Sem {sem}"

        # 3. Place Subjects
        # Track subjects already assigned to a day for this semester
        # Initialize with 6 slots (Mon-Sat) to accommodate all possible day configurations
        sem_day_subjects = {sem: [set() for _ in range(6)] for sem in self.semesters_data}
        
        for sem, data in self.semesters_data.items():
            sat_holiday = data.get('sat_holiday', False)
            num_days = 5 if sat_holiday else 6
            for sub in data.get('subjects', []):
                credits = int(sub['credits'])
                teacher = sub['teacher']
                for _ in range(credits):
                    placed = False
                    # Prioritize earlier slots to keep timetable compact
                    slot_indices = list(range(len(SLOTS)))
                    # DO NOT shuffle slot_indices to ensure we try to fill from the morning first
                    
                    for s in slot_indices:
                        days_list = list(range(num_days))
                        random.shuffle(days_list)
                        for d in days_list:
                            # CONSTRAINT: One class of ONE subject in ONE day
                            if sub['name'] in sem_day_subjects[sem][d]: continue
                            
                            if self.grids[sem][d][s] is None and self.is_resource_free([teacher], 'subject', d, s):
                                self.grids[sem][d][s] = {"type": "subject", "name": sub['name'], "teacher": teacher}
                                self.mark_busy([teacher], 'subject', d, s)
                                sem_day_subjects[sem][d].add(sub['name'])
                                placed = True; break
                        if placed: break
                    if not placed: return None, f"Could not place subject {sub['name']} (Sem {sem}) - Try increasing slots or allowing Saturday classes."

        # POST-PROCESSING: Fill Idle Gaps with Productive Activities
        for sem in self.semesters_data:
            for d in range(6):
                day_slots = self.grids[sem][d]
                occupied = [i for i, slot in enumerate(day_slots) if slot is not None]
                if not occupied: continue
                
                first, last = min(occupied), max(occupied)
                for i in range(first, last + 1):
                    if day_slots[i] is None:
                        day_slots[i] = {
                            "type": "productive",
                            "name": "Study / Revision / Lab Prep",
                            "teacher": "Self",
                            "room": "Library / Study Hall"
                        }

        # Add holiday reason to the grid metadata if applicable
        for sem, data in self.semesters_data.items():
            if data.get('sat_holiday') and data.get('holiday_reason'):
                self.grids[sem].append({'holiday_reason': data['holiday_reason']})

        return self.grids, None

@timetable_bp.route('/timetable')
def index():
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    return render_template('timetable/generator.html', days=DAYS, slots=SLOTS)

@timetable_bp.route('/api/generate_cycle', methods=['POST'])
def generate_cycle():
    data = request.json
    cycle_type = data.get('cycle_type')
    semesters_data = data.get('semesters', {})
    max_labs = int(data.get('max_labs', 3))
    # Extract teacher pool from input data
    teacher_pool = set()
    for sem_id, sem_val in semesters_data.items():
        for sub in sem_val.get('subjects', []):
            if sub.get('teacher'): teacher_pool.add(sub['teacher'])
        for lab in sem_val.get('labs', []):
            for t in lab.get('teachers', []):
                if t: teacher_pool.add(t)
                
    # Pre-fetch all teacher schedules to avoid thousands of DB calls during generation
    schedules_cache = {}
    for t in teacher_pool:
        schedules_cache[t] = db_instance.get_teacher_schedule(t)
    
    # Save teacher pool to config for persistence (per user)
    db_instance.update_department_resources(session['user_id'], max_labs, list(teacher_pool))
    
    gen = DepartmentCycleGenerator(cycle_type, semesters_data, max_labs, schedules_cache)
    grids, err = gen.generate()
    
    if err: return jsonify({'error': err}), 400
    
    # Save cycle with full input configuration for future modification
    db_instance.save_cycle(cycle_type, grids, session['user_id'], input_config=data)
    return jsonify({'success': True, 'grids': grids})

@timetable_bp.route('/api/cycle/<id>/config')
def get_cycle_config(id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    try:
        cycle = db_instance.db['cycles'].find_one({'_id': ObjectId(id), 'user_id': session['user_id']})
        if not cycle: return jsonify({'error': 'Cycle not found'}), 404
        # Return only the input_config part
        return jsonify(cycle.get('input_config', {}))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    
    # Save cycle and teachers status
    db_instance.save_cycle(cycle_type, grids)
    # Update global teacher busy slots would go here...
    
@timetable_bp.route('/api/get_config', methods=['GET'])
def get_config():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    resources = db_instance.get_department_resources(session['user_id'])
    return jsonify({
        'teachers': resources.get('teachers', []),
        'max_labs': resources.get('lab_rooms', 3)
    })

@timetable_bp.route('/api/export_cycle', methods=['POST'])
def export_cycle():
    data = request.json
    grids = data.get('grids') # { '3': grid, '5': grid ... }
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sem, grid in grids.items():
            df_data = []
            cols = ["Day", "09:00-10:00", "10:00-11:00", "RECESS", "11:15-12:15", "12:15-01:15", "LUNCH", "02:15-03:15", "03:15-04:15", "04:15-05:15"]
            for d_idx, day_name in enumerate(DAYS):
                # Check for holiday reason metadata at the end of the grid list
                holiday_metadata = next((x for x in grid if isinstance(x, dict) and 'holiday_reason' in x), None)
                
                if d_idx >= 6: continue # Safety
                
                # Check if this day exists in grid as a list of slots
                if d_idx < len(grid) and isinstance(grid[d_idx], list):
                    row = [day_name]
                    for s_idx in range(len(SLOTS)):
                        if s_idx == 2: row.append("RECESS")
                        if s_idx == 4: row.append("LUNCH")
                        cell = grid[d_idx][s_idx]
                        if not cell: row.append("-")
                        elif cell['type'] == 'lab': 
                            base = f"{cell['name']} ({cell['batch']}: {cell['teachers']})"
                            if cell.get('parallel'):
                                base += f" & {cell['parallel']} (B2: {cell['b2_teachers']})"
                            row.append(base)
                        else: row.append(f"{cell['name']} ({cell.get('teacher', 'FIXED')})")
                    df_data.append(row)
                elif day_name == "Saturday" and holiday_metadata:
                    # Insert a row for Saturday holiday
                    row = [day_name] + ["OFF DAY: " + holiday_metadata['holiday_reason'].upper()] + [""] * (len(cols) - 2)
                    df_data.append(row)
            df = pd.DataFrame(df_data, columns=cols)
            df.to_excel(writer, index=False, sheet_name=f"Semester_{sem}")
            
            try:
                if writer.book and writer.book.worksheets:
                    for ws in writer.book.worksheets:
                        ws.sheet_state = 'visible'
                    writer.book.active = 0
            except Exception: pass

    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Department_Timetable.xlsx")
@timetable_bp.route('/api/download_historical_timetable/<id>')
def download_historical_timetable(id):
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        cycle = db_instance.db['cycles'].find_one({'_id': ObjectId(id), 'user_id': session['user_id']})
        if not cycle: return "Timetable not found", 404
        
        grids = cycle['data']
        cycle_type = cycle.get('cycle_type', 'generated')
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for sem, grid in grids.items():
                df_data = []
                cols = ["Day", "09:00-10:00", "10:00-11:00", "RECESS", "11:15-12:15", "12:15-01:15", "LUNCH", "02:15-03:15", "03:15-04:15", "04:15-05:15"]
                for d_idx, day_name in enumerate(DAYS):
                    holiday_metadata = next((x for x in grid if isinstance(x, dict) and 'holiday_reason' in x), None)
                    
                    if d_idx >= 6: continue
                    
                    if d_idx < len(grid) and isinstance(grid[d_idx], list):
                        row = [day_name]
                        for s_idx in range(len(SLOTS)):
                            if s_idx == 2: row.append("RECESS")
                            if s_idx == 4: row.append("LUNCH")
                            cell = grid[d_idx][s_idx]
                            if not cell: row.append("-")
                            elif cell['type'] == 'lab': 
                                base = f"{cell['name']} ({cell['batch']}: {cell['teachers']})"
                                if cell.get('parallel'):
                                    base += f" & {cell['parallel']} (B2: {cell['b2_teachers']})"
                                row.append(base)
                            else: row.append(f"{cell['name']} ({cell.get('teacher', 'FIXED')})")
                        df_data.append(row)
                    elif day_name == "Saturday" and holiday_metadata:
                        row = [day_name] + ["OFF DAY: " + holiday_metadata['holiday_reason'].upper()] + [""] * (len(cols) - 2)
                        df_data.append(row)
                df = pd.DataFrame(df_data, columns=cols)
                df.to_excel(writer, index=False, sheet_name=f"Semester_{sem}")
            
            try:
                if writer.book and writer.book.worksheets:
                    for ws in writer.book.worksheets:
                        ws.sheet_state = 'visible'
                    writer.book.active = 0
            except Exception: pass

        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"AcadFusion_{cycle_type.capitalize()}_Timetable.xlsx")
        
    except Exception as e:
        print(f"Historical Download Error: {e}")
        return "Internal Server Error", 500
