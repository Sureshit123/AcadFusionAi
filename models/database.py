import os
import datetime
from pymongo import MongoClient
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

class Database:
    def __init__(self):
        self.mongo_uri = os.environ.get('MONGO_URI', 'mongodb://127.0.0.1:27017/')
        try:
            self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            self.db = self.client['vtu_analyzer']
            self.users = self.db['users']
            self.schedules = self.db['schedules']
            self.teachers = self.db['teachers']
            # Ping
            self.client.admin.command('ping')
            print("Successfully connected to MongoDB.")
        except Exception as e:
            print(f"MongoDB Connection Error: {e}")
            self.users = None

    def get_user_by_email(self, email):
        if self.users is None or not email: return None
        return self.users.find_one({'email': email.strip().lower()})

    def create_user(self, name, email, password):
        if self.users is None: return False
        hashed_pw = generate_password_hash(password)
        try:
            self.users.insert_one({
                'name': name.strip(),
                'email': email.strip().lower(),
                'password_hash': hashed_pw
            })
            return True
        except Exception as e:
            print(f"Create User Error: {e}")
            return False

    def verify_user(self, email, password):
        user = self.get_user_by_email(email)
        if user and check_password_hash(user['password_hash'], password):
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

    def save_analysis_job(self, job_id, usn_list, results, user_id):
        """Saves a completed result analysis job to MongoDB."""
        try:
            self.db['analysis_jobs'].insert_one({
                'job_id': job_id,
                'user_id': user_id,
                'usn_list': usn_list,
                'results': results, # Consider if results are too big for one doc (>16MB) - usually not for <500 students
                'timestamp': datetime.datetime.now(datetime.timezone.utc),
                'student_count': len(results)
            })
            return True
        except Exception as e:
            print(f"Save Analysis Job Error: {e}")
            return False

    def get_user_analysis_history(self, user_id, limit=10):
        """Retrieves history of analysis jobs for a user, sorted by newest."""
        try:
            cursor = self.db['analysis_jobs'].find(
                {'user_id': user_id},
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


# Global database instance
db_instance = Database()
