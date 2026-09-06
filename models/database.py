import os
import datetime
import re
from pymongo import MongoClient
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

class Database:
    def __init__(self):
        self.mongo_uri = os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI') or 'mongodb://127.0.0.1:27017/'
        self.admin_email = os.environ.get('ADMIN_EMAIL', 'sharmasreshit@gmail.com').strip().lower()
        try:
            self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            
            # Dynamic database selection:
            # 1. Use MONGODB_DB_NAME or MONGO_DB_NAME if explicitly configured
            # 2. Extract database defined in URI connection path (via client.get_default_database())
            # 3. Fallback safely to 'acadfusion_ai' (ensures it never defaults to old project's 'vtu_analyzer')
            explicit_db = os.environ.get('MONGODB_DB_NAME') or os.environ.get('MONGO_DB_NAME')
            if explicit_db and explicit_db.strip():
                self.db_name = explicit_db.strip()
            else:
                try:
                    default_db = self.client.get_default_database()
                    self.db_name = default_db.name if (default_db is not None and default_db.name) else 'acadfusion_ai'
                except Exception:
                    self.db_name = 'acadfusion_ai'
            
            # Safeguard: ensure new project never silently falls back to old project's vtu_analyzer
            if not (explicit_db and explicit_db.strip()) and self.db_name == 'vtu_analyzer':
                self.db_name = 'acadfusion_ai'

            self.db = self.client[self.db_name]
            self.users = self.db['users']
            self.schedules = self.db['schedules']
            self.teachers = self.db['teachers']
            
            # Collections for Institution Profile and configuration
            self.institution_profile = self.db['institution_profile']
            self.departments = self.db['departments']
            self.faculty_master = self.db['faculty_master']
            self.subject_master = self.db['subject_master']
            self.report_settings = self.db['report_settings']
            self.feedbacks = self.db['feedbacks']
            
            # Ping
            self.client.admin.command('ping')
            print(f"Successfully connected to MongoDB (database: {self.db_name}).")
            
            # Ensure creator admin user permissions are initialized
            self.ensure_admin_user()
        except Exception as e:
            print(f"MongoDB Connection Error: {e}")
            self.users = None

    def ensure_admin_user(self):
        """Ensures the official creator account matching ADMIN_EMAIL is granted the admin role without altering password hash."""
        if self.users is None or not self.admin_email: return
        try:
            user = self.users.find_one({'email': {'$regex': f"^{re.escape(self.admin_email)}$", '$options': 'i'}})
            if user:
                if user.get('role') != 'admin':
                    self.users.update_one({'_id': user['_id']}, {'$set': {'role': 'admin'}})
                    print(f"Granted admin role to creator account: {self.admin_email}")
            else:
                print(f"Admin account ({self.admin_email}) configured. It will receive admin role upon registration.")
        except Exception as e:
            print(f"Ensure Admin User Error: {e}")

    def get_user_by_email(self, email):
        if self.users is None or not email: return None
        return self.users.find_one({'email': {'$regex': f"^{re.escape(email.strip())}$", '$options': 'i'}})

    def get_user_by_id(self, user_id):
        if self.users is None or not user_id: return None
        try:
            return self.users.find_one({'_id': ObjectId(user_id)})
        except Exception:
            return self.users.find_one({'_id': str(user_id)})

    def create_user(self, name, email, password, role=None):
        if self.users is None: return False
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        email_clean = email.strip().lower() if email else ''
        
        # Determine role: default 'user', or 'admin' if matching ADMIN_EMAIL or explicitly specified
        if not role:
            role = 'admin' if email_clean == self.admin_email else 'user'
            
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            self.users.insert_one({
                'name': name.strip(),
                'email': email_clean,
                'password_hash': hashed_pw,
                'role': role,
                'created_at': now_iso,
                'account_status': 'active'
            })
            return True
        except Exception as e:
            print(f"Create User Error: {e}")
            return False

    def verify_user(self, email, password):
        user = self.get_user_by_email(email)
        if not user:
            print(f"LOGIN DEBUG: No user found for email '{email}' in database '{self.db_name}'")
            return None
        
        pw_hash = user.get('password_hash') or user.get('password')
        if not pw_hash:
            print(f"LOGIN DEBUG: User '{email}' has no password_hash or password field. Fields: {list(user.keys())}")
            return None
        
        hash_method = pw_hash.split(':')[0] if ':' in pw_hash else pw_hash[:10]
        verify_ok = check_password_hash(pw_hash, password)
        print(f"LOGIN DEBUG: User '{email}' found in '{self.db_name}'. Hash method: {hash_method}. Verify result: {verify_ok}. Status: {user.get('account_status')}")
        
        if verify_ok:
            # Block suspended users
            if user.get('account_status') == 'suspended':
                print(f"Login rejected: Account {email} is suspended.")
                return None
            
            # Update last login timestamp
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.users.update_one({'_id': user['_id']}, {'$set': {'last_login': now_iso}})
            user['last_login'] = now_iso
            return user
        return None

    def save_timetable(self, semester, timetable_data, creator_email):
        try:
            self.schedules.update_one(
                {'semester': semester},
                {'$set': {
                    'data': timetable_data,
                    'created_by': creator_email,
                    'last_updated': os.times()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Save Timetable Error: {e}")
            return False

    def get_teacher_schedule(self, teacher_name):
        teacher = self.teachers.find_one({'name': teacher_name})
        return teacher['busy_slots'] if teacher else []

    def update_teacher_schedule(self, teacher_name, new_slots):
        self.teachers.update_one(
            {'name': teacher_name},
            {'$set': {'busy_slots': new_slots}},
            upsert=True
        )

    def get_department_resources(self, user_id):
        config = self.db['config'].find_one({'key': 'department_resources', 'user_id': user_id})
        if not config: return {'teachers': [], 'lab_rooms': 3}
        return {
            'teachers': config.get('teachers', []),
            'lab_rooms': config.get('lab_rooms', 3)
        }

    def update_department_resources(self, user_id, lab_rooms, teacher_list):
        self.db['config'].update_one(
            {'key': 'department_resources', 'user_id': user_id},
            {'$set': {
                'lab_rooms': lab_rooms,
                'teachers': teacher_list
            }},
            upsert=True
        )

    def save_cycle(self, cycle_type, data, user_id, input_config=None):
        # Saves a new version of the generated timetable cycle
        try:
            self.db['cycles'].insert_one({
                'cycle_type': cycle_type,
                'user_id': user_id,
                'data': data,
                'input_config': input_config,
                'timestamp': datetime.datetime.now(datetime.timezone.utc)
            })
            return True
        except Exception as e:
            print(f"Save Cycle Error: {e}")
            return False

    def get_user_timetable_history(self, user_id, limit=10):
        """Fetches recent generated timetable cycles for a user."""
        try:
            cursor = self.db['cycles'].find(
                {'user_id': user_id},
                {'data': 0} # Don't return huge grid data in list view, but keep _id
            ).sort('timestamp', -1).limit(limit)
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id']) # Convert ObjectId to string for JSON
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Timetable History Error: {e}")
            return []

    def save_analysis_job(self, job_id, usn_list, results, user_id, report_settings=None, status='completed'):
        """Saves a completed or failed result analysis job to MongoDB with user and configuration metadata."""
        try:
            user = self.get_user_by_id(user_id) if user_id and user_id != 'admin_super_user' else None
            user_name = user.get('name', 'Admin') if user else ('Super Admin' if user_id == 'admin_super_user' else 'System User')
            user_email = user.get('email', self.admin_email) if user else ('admin@acadfusion.ai' if user_id == 'admin_super_user' else '')
            
            rs = report_settings or {}
            result_type = rs.get('result_type', 'regular')
            semester = rs.get('semester', 'N/A')
            department = rs.get('department', 'N/A')
            academic_year = rs.get('academic_year', 'N/A')

            doc = {
                'job_id': job_id,
                'user_id': str(user_id) if user_id else 'anonymous',
                'user_name': user_name,
                'user_email': user_email,
                'usn_list': usn_list,
                'results': results or [],
                'timestamp': datetime.datetime.now(datetime.timezone.utc),
                'student_count': len(results) if results else 0,
                'report_settings': rs,
                'result_type': result_type,
                'semester': semester,
                'department': department,
                'academic_year': academic_year,
                'status': status
            }
            self.db['analysis_jobs'].insert_one(doc)
            return True
        except Exception as e:
            print(f"Save Analysis Job Error: {e}")
            return False

    def get_user_analysis_history(self, user_id, limit=20):
        """Retrieves history of analysis jobs for a user, sorted by newest."""
        try:
            cursor = self.db['analysis_jobs'].find(
                {'user_id': str(user_id)},
                {'results': 0}
            ).sort('timestamp', -1).limit(limit)
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get History Error: {e}")
            return []

    # --- ADMIN DB METHODS ---

    def get_all_users(self, query=None, status_filter=None):
        """Retrieves all registered users for Admin panel without sensitive password hashes."""
        if self.users is None: return []
        try:
            q = {}
            if status_filter and status_filter.lower() != 'all':
                q['account_status'] = status_filter.lower()
            if query:
                regex = {'$regex': re.escape(query.strip()), '$options': 'i'}
                q['$or'] = [{'name': regex}, {'email': regex}]

            cursor = self.users.find(q, {'password_hash': 0}).sort('created_at', -1)
            user_list = []
            for doc in cursor:
                u_id = str(doc['_id'])
                doc['_id'] = u_id
                # Count analyses for this user
                analyses_count = self.db['analysis_jobs'].count_documents({'user_id': u_id})
                doc['analyses_count'] = analyses_count
                doc['role'] = doc.get('role', 'user')
                doc['account_status'] = doc.get('account_status', 'active')
                user_list.append(doc)
            return user_list
        except Exception as e:
            print(f"Get All Users Error: {e}")
            return []

    def reset_user_password(self, user_id, new_password):
        """Securely resets user password using Werkzeug password hashing."""
        if self.users is None or not new_password: return False
        try:
            hashed_pw = generate_password_hash(new_password)
            res = self.users.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {'password_hash': hashed_pw}}
            )
            return res.modified_count > 0 or res.matched_count > 0
        except Exception as e:
            print(f"Reset User Password Error: {e}")
            return False

    def update_user_status(self, user_id, status):
        """Updates account status ('active' or 'suspended')."""
        if self.users is None: return False
        try:
            res = self.users.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {'account_status': status}}
            )
            return res.modified_count > 0 or res.matched_count > 0
        except Exception as e:
            print(f"Update User Status Error: {e}")
            return False

    def get_platform_stats(self):
        """Gathers platform-wide metrics for Admin Dashboard overview."""
        try:
            total_users = self.users.count_documents({}) if self.users is not None else 0
            active_users = self.users.count_documents({'account_status': 'active'}) if self.users is not None else 0
            suspended_users = self.users.count_documents({'account_status': 'suspended'}) if self.users is not None else 0
            
            total_analyses = self.db['analysis_jobs'].count_documents({})
            successful_analyses = self.db['analysis_jobs'].count_documents({'status': 'completed'})
            failed_analyses = self.db['analysis_jobs'].count_documents({'status': {'$in': ['failed', 'error']}})
            
            # Recent registered users (top 5)
            recent_users_cursor = self.users.find({}, {'password_hash': 0}).sort('created_at', -1).limit(5)
            recent_users = []
            for u in recent_users_cursor:
                u['_id'] = str(u['_id'])
                recent_users.append(u)

            # Recent logins (top 5)
            recent_logins_cursor = self.users.find({'last_login': {'$ne': None}}, {'password_hash': 0}).sort('last_login', -1).limit(5)
            recent_logins = []
            for u in recent_logins_cursor:
                u['_id'] = str(u['_id'])
                recent_logins.append(u)

            # Recent analyses (top 10)
            recent_analyses_cursor = self.db['analysis_jobs'].find({}, {'results': 0}).sort('timestamp', -1).limit(10)
            recent_analyses = []
            for a in recent_analyses_cursor:
                a['_id'] = str(a['_id'])
                recent_analyses.append(a)

            return {
                'total_users': total_users,
                'active_users': active_users,
                'suspended_users': suspended_users,
                'total_analyses': total_analyses,
                'successful_analyses': successful_analyses,
                'failed_analyses': failed_analyses,
                'recent_users': recent_users,
                'recent_logins': recent_logins,
                'recent_analyses': recent_analyses
            }
        except Exception as e:
            print(f"Get Platform Stats Error: {e}")
            return {}

    def get_all_analysis_jobs(self, query_str=None, limit=100):
        """Retrieves platform-wide analysis jobs across all users for Admin."""
        try:
            q = {}
            if query_str:
                regex = {'$regex': re.escape(query_str.strip()), '$options': 'i'}
                q['$or'] = [
                    {'user_name': regex},
                    {'user_email': regex},
                    {'job_id': regex},
                    {'department': regex},
                    {'result_type': regex}
                ]
            cursor = self.db['analysis_jobs'].find(q, {'results': 0}).sort('timestamp', -1).limit(limit)
            jobs = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                jobs.append(doc)
            return jobs
        except Exception as e:
            print(f"Get All Analysis Jobs Error: {e}")
            return []

    def get_analysis_job_results(self, job_id):
        """Retrieves full results for a specific job."""
        return self.db['analysis_jobs'].find_one({'job_id': job_id})

    def delete_analysis_job(self, job_id, user_id):
        """Deletes an analysis job from MongoDB if it belongs to the user."""
        try:
            res = self.db['analysis_jobs'].delete_one({'job_id': job_id, 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Analysis error: {e}")
            return False

    def delete_timetable_cycle(self, cycle_id, user_id):
        """Deletes a timetable cycle from MongoDB if it belongs to the user."""
        try:
            res = self.db['cycles'].delete_one({'_id': ObjectId(cycle_id), 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Timetable error: {e}")
            return False

    # --- Institution Profile & Configuration CRUD Helpers ---

    def get_institution_profile(self, user_id):
        try:
            profile = self.institution_profile.find_one({'user_id': user_id})
            if profile:
                profile['_id'] = str(profile['_id'])
            return profile
        except Exception as e:
            print(f"Get Profile Error: {e}")
            return None

    def save_institution_profile(self, user_id, data):
        try:
            self.institution_profile.update_one(
                {'user_id': user_id},
                {'$set': data},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Save Profile Error: {e}")
            return False

    def get_departments(self, user_id):
        try:
            cursor = self.departments.find({'user_id': user_id})
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Departments Error: {e}")
            return []

    def add_department(self, user_id, data):
        try:
            data['user_id'] = user_id
            res = self.departments.insert_one(data)
            return str(res.inserted_id)
        except Exception as e:
            print(f"Add Department Error: {e}")
            return None

    def delete_department(self, user_id, dept_id):
        try:
            res = self.departments.delete_one({'_id': ObjectId(dept_id), 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Department Error: {e}")
            return False

    def update_department(self, user_id, dept_id, data):
        try:
            res = self.departments.update_one(
                {'_id': ObjectId(dept_id), 'user_id': user_id},
                {'$set': data}
            )
            return res.modified_count > 0 or res.matched_count > 0
        except Exception as e:
            print(f"Update Department Error: {e}")
            return False

    def get_faculty_members(self, user_id):
        try:
            cursor = self.faculty_master.find({'user_id': user_id})
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Faculty Error: {e}")
            return []

    def add_faculty(self, user_id, data):
        try:
            data['user_id'] = user_id
            res = self.faculty_master.insert_one(data)
            return str(res.inserted_id)
        except Exception as e:
            print(f"Add Faculty Error: {e}")
            return None

    def delete_faculty(self, user_id, fac_id):
        try:
            res = self.faculty_master.delete_one({'_id': ObjectId(fac_id), 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Faculty Error: {e}")
            return False

    def get_subjects(self, user_id, query_filter=None):
        try:
            q = {'user_id': user_id}
            normalized = {}
            if query_filter:
                # Normalize types before querying to avoid string vs int mismatches
                for k, v in query_filter.items():
                    if v is None or v == '':
                        continue  # Skip empty filters so we don't restrict results unnecessarily
                    if k == 'semester':
                        # Store semester as int for consistent matching
                        try:
                            normalized[k] = int(v)
                        except (ValueError, TypeError):
                            normalized[k] = v
                    elif isinstance(v, str):
                        normalized[k] = v.strip()
                    else:
                        normalized[k] = v
                q.update(normalized)

            cursor = list(self.subject_master.find(q))

            # Progressive fallback: if strict query returned 0 results, relax the filters
            if not cursor and normalized:
                # Fallback 1: Try semester + department
                fallback_q = {'user_id': user_id}
                if 'semester' in normalized: fallback_q['semester'] = normalized['semester']
                if 'department' in normalized: fallback_q['department'] = normalized['department']
                if len(fallback_q) > 1:
                    cursor = list(self.subject_master.find(fallback_q))

            if not cursor and normalized:
                # Fallback 2: Try semester only
                if 'semester' in normalized:
                    cursor = list(self.subject_master.find({'user_id': user_id, 'semester': normalized['semester']}))

            if not cursor and normalized:
                # Fallback 3: Return all subject master documents for this user
                cursor = list(self.subject_master.find({'user_id': user_id}))

            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                # Normalize credits to int in the returned data
                if 'credits' in doc:
                    try:
                        doc['credits'] = int(doc['credits'])
                    except (ValueError, TypeError):
                        doc['credits'] = 4
                # Normalize semester to int
                if 'semester' in doc:
                    try:
                        doc['semester'] = int(doc['semester'])
                    except (ValueError, TypeError):
                        pass
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Subjects Error: {e}")
            return []

    def add_subject(self, user_id, data):
        try:
            data['user_id'] = user_id
            # Normalize types for consistent storage
            if 'semester' in data:
                try:
                    data['semester'] = int(data['semester'])
                except (ValueError, TypeError):
                    pass
            if 'credits' in data:
                try:
                    data['credits'] = int(data['credits'])
                except (ValueError, TypeError):
                    pass
            res = self.subject_master.insert_one(data)
            return str(res.inserted_id)
        except Exception as e:
            print(f"Add Subject Error: {e}")
            return None

    def update_subject(self, user_id, sub_id, data):
        try:
            # Normalize types for consistent storage
            if 'semester' in data:
                try:
                    data['semester'] = int(data['semester'])
                except (ValueError, TypeError):
                    pass
            if 'credits' in data:
                try:
                    data['credits'] = int(data['credits'])
                except (ValueError, TypeError):
                    pass
            res = self.subject_master.update_one(
                {'_id': ObjectId(sub_id), 'user_id': user_id},
                {'$set': data}
            )
            return res.modified_count > 0
        except Exception as e:
            print(f"Update Subject Error: {e}")
            return False

    def delete_subject(self, user_id, sub_id):
        try:
            res = self.subject_master.delete_one({'_id': ObjectId(sub_id), 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Subject Error: {e}")
            return False

    def get_report_defaults(self, user_id):
        try:
            defaults = self.report_settings.find_one({'user_id': user_id})
            if defaults:
                defaults['_id'] = str(defaults['_id'])
            return defaults
        except Exception as e:
            print(f"Get Report Defaults Error: {e}")
            return None

    def save_report_defaults(self, user_id, data):
        try:
            self.report_settings.update_one(
                {'user_id': user_id},
                {'$set': data},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Save Report Defaults Error: {e}")
            return False

    def save_feedback(self, user_id, data):
        try:
            doc = dict(data)
            doc['user_id'] = user_id
            doc['created_at'] = datetime.datetime.utcnow().isoformat()
            doc['updated_at'] = doc['created_at']

            # Normalize ratings to ints
            for rating_field in ['overall_rating', 'teaching_rating', 'communication_rating', 'knowledge_rating', 'doubt_rating', 'practical_rating']:
                if rating_field in doc:
                    try:
                        doc[rating_field] = int(doc[rating_field])
                    except (ValueError, TypeError):
                        doc[rating_field] = 5

            res = self.feedbacks.insert_one(doc)
            return str(res.inserted_id)
        except Exception as e:
            print(f"Save Feedback Error: {e}")
            return None

    def check_existing_feedback(self, user_id, student_usn, subject_code, semester):
        try:
            if not student_usn or student_usn.strip().lower() in ['anonymous', 'anon']:
                return False  # Allow multiple if USN is explicitly anonymous
            q = {
                'user_id': user_id,
                'student_usn': student_usn.strip().upper(),
                'subject_code': subject_code.strip().upper()
            }
            if semester:
                try:
                    q['semester'] = int(semester)
                except (ValueError, TypeError):
                    q['semester'] = semester
            existing = self.feedbacks.find_one(q)
            return existing is not None
        except Exception as e:
            print(f"Check Feedback Error: {e}")
            return False

    def get_feedbacks(self, user_id, filters=None):
        try:
            q = {'user_id': user_id}
            if filters:
                if filters.get('department'):
                    q['department'] = filters['department'].strip()
                if filters.get('semester'):
                    try:
                        q['semester'] = int(filters['semester'])
                    except (ValueError, TypeError):
                        q['semester'] = filters['semester']
                if filters.get('subject_code'):
                    q['subject_code'] = filters['subject_code'].strip().upper()

            cursor = self.feedbacks.find(q).sort('created_at', -1)
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Feedbacks Error: {e}")
            return []

    def get_feedback_analytics(self, user_id, filters=None):
        try:
            feedbacks = self.get_feedbacks(user_id, filters)
            if not feedbacks:
                return {
                    'total_feedbacks': 0,
                    'total_teachers': 0,
                    'overall_average': 0.0,
                    'teaching_average': 0.0,
                    'communication_average': 0.0,
                    'knowledge_average': 0.0,
                    'doubt_average': 0.0,
                    'practical_average': 0.0,
                    'top_teacher': None,
                    'lowest_teacher': None,
                    'teacher_stats': [],
                    'department_stats': [],
                    'rating_distribution': {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
                }

            total_fb = len(feedbacks)
            teachers_map = {}
            dept_map = {}
            rating_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

            tot_overall = 0
            tot_teaching = 0
            tot_comm = 0
            tot_know = 0
            tot_doubt = 0
            tot_prac = 0

            for fb in feedbacks:
                ov = int(fb.get('overall_rating', 5))
                t_r = int(fb.get('teaching_rating', ov))
                c_r = int(fb.get('communication_rating', ov))
                k_r = int(fb.get('knowledge_rating', ov))
                d_r = int(fb.get('doubt_rating', ov))
                p_r = int(fb.get('practical_rating', ov))

                tot_overall += ov
                tot_teaching += t_r
                tot_comm += c_r
                tot_know += k_r
                tot_doubt += d_r
                tot_prac += p_r

                # Rating distribution
                if 1 <= ov <= 5:
                    rating_dist[ov] += 1

                t_name = fb.get('faculty_name', 'Unassigned')
                sub_code = fb.get('subject_code', 'N/A')
                dept = fb.get('department', 'General')

                key = (t_name, sub_code)
                if key not in teachers_map:
                    teachers_map[key] = {
                        'faculty_name': t_name,
                        'subject_code': sub_code,
                        'subject_name': fb.get('subject_name', 'N/A'),
                        'department': dept,
                        'semester': fb.get('semester', 'N/A'),
                        'count': 0,
                        'overall_sum': 0,
                        'teaching_sum': 0,
                        'comm_sum': 0,
                        'know_sum': 0,
                        'doubt_sum': 0,
                        'prac_sum': 0,
                        'comments': []
                    }

                t_data = teachers_map[key]
                t_data['count'] += 1
                t_data['overall_sum'] += ov
                t_data['teaching_sum'] += t_r
                t_data['comm_sum'] += c_r
                t_data['know_sum'] += k_r
                t_data['doubt_sum'] += d_r
                t_data['prac_sum'] += p_r
                if fb.get('comments'):
                    t_data['comments'].append({
                        'text': fb['comments'],
                        'anonymous': fb.get('anonymous', True),
                        'student_usn': 'Anonymous' if fb.get('anonymous', True) else fb.get('student_usn', 'Student')
                    })

                # Department map
                if dept not in dept_map:
                    dept_map[dept] = {'department': dept, 'count': 0, 'overall_sum': 0}
                dept_map[dept]['count'] += 1
                dept_map[dept]['overall_sum'] += ov

            teacher_stats = []
            for (t_name, sub_code), t_data in teachers_map.items():
                cnt = t_data['count']
                avg_o = round(t_data['overall_sum'] / cnt, 2)
                teacher_stats.append({
                    'faculty_name': t_data['faculty_name'],
                    'subject_code': t_data['subject_code'],
                    'subject_name': t_data['subject_name'],
                    'department': t_data['department'],
                    'semester': t_data['semester'],
                    'count': cnt,
                    'avg_overall': avg_o,
                    'avg_teaching': round(t_data['teaching_sum'] / cnt, 2),
                    'avg_communication': round(t_data['comm_sum'] / cnt, 2),
                    'avg_knowledge': round(t_data['know_sum'] / cnt, 2),
                    'avg_doubt': round(t_data['doubt_sum'] / cnt, 2),
                    'avg_practical': round(t_data['prac_sum'] / cnt, 2),
                    'comments': t_data['comments']
                })

            teacher_stats.sort(key=lambda x: x['avg_overall'], reverse=True)

            dept_stats = []
            for dept, d_data in dept_map.items():
                dept_stats.append({
                    'department': dept,
                    'count': d_data['count'],
                    'avg_overall': round(d_data['overall_sum'] / d_data['count'], 2)
                })

            return {
                'total_feedbacks': total_fb,
                'total_teachers': len(teachers_map),
                'overall_average': round(tot_overall / total_fb, 2),
                'teaching_average': round(tot_teaching / total_fb, 2),
                'communication_average': round(tot_comm / total_fb, 2),
                'knowledge_average': round(tot_know / total_fb, 2),
                'doubt_average': round(tot_doubt / total_fb, 2),
                'practical_average': round(tot_prac / total_fb, 2),
                'top_teacher': teacher_stats[0] if teacher_stats else None,
                'lowest_teacher': teacher_stats[-1] if teacher_stats else None,
                'teacher_stats': teacher_stats,
                'department_stats': dept_stats,
                'rating_distribution': rating_dist
            }
        except Exception as e:
            print(f"Feedback Analytics Error: {e}")
            return {}

    # --- Timetable Methods ---
    def save_timetable(self, semester, timetable_data, creator_email):
        try:
            self.schedules.update_one(
                {'semester': semester},
                {'$set': {
                    'data': timetable_data,
                    'created_by': creator_email,
                    'last_updated': datetime.datetime.now(datetime.timezone.utc)
                }},
                upsert=True
            )
            return True
        except Exception as e:
            print(f"Save Timetable Error: {e}")
            return False

    def get_teacher_schedule(self, teacher_name):
        teacher = self.teachers.find_one({'name': teacher_name})
        return teacher['busy_slots'] if teacher else []

    def update_teacher_schedule(self, teacher_name, new_slots):
        self.teachers.update_one(
            {'name': teacher_name},
            {'$set': {'busy_slots': new_slots}},
            upsert=True
        )

    def get_department_resources(self, user_id):
        config = self.db['config'].find_one({'key': 'department_resources', 'user_id': user_id})
        if not config: return {'teachers': [], 'lab_rooms': 3}
        return {
            'teachers': config.get('teachers', []),
            'lab_rooms': config.get('lab_rooms', 3)
        }

    def update_department_resources(self, user_id, lab_rooms, teacher_list):
        self.db['config'].update_one(
            {'key': 'department_resources', 'user_id': user_id},
            {'$set': {
                'lab_rooms': lab_rooms,
                'teachers': teacher_list
            }},
            upsert=True
        )

    def save_cycle(self, cycle_type, data, user_id, input_config=None):
        try:
            self.db['cycles'].insert_one({
                'cycle_type': cycle_type,
                'user_id': user_id,
                'data': data,
                'input_config': input_config,
                'timestamp': datetime.datetime.now(datetime.timezone.utc)
            })
            return True
        except Exception as e:
            print(f"Save Cycle Error: {e}")
            return False

    def get_user_timetable_history(self, user_id, limit=10):
        try:
            cursor = self.db['cycles'].find(
                {'user_id': user_id},
                {'data': 0}
            ).sort('timestamp', -1).limit(limit)
            results = []
            for doc in cursor:
                doc['_id'] = str(doc['_id'])
                results.append(doc)
            return results
        except Exception as e:
            print(f"Get Timetable History Error: {e}")
            return []

    def delete_timetable_cycle(self, cycle_id, user_id):
        try:
            res = self.db['cycles'].delete_one({'_id': ObjectId(cycle_id), 'user_id': user_id})
            return res.deleted_count > 0
        except Exception as e:
            print(f"Delete Timetable error: {e}")
            return False


# Global database instance
db_instance = Database()

