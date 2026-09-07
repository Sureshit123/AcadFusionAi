import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

raw_uri = os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI')

if not raw_uri:
    print("No MONGODB_URI or MONGO_URI found in environment.")
    exit(1)

mongo_uri = raw_uri.strip().strip("'\"").replace(r'\n', '').replace(r'\r', '').replace('\n', '').replace('\r', '').replace('\t', '').strip()

try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    
    # Resolve target database
    explicit_db = os.environ.get('MONGODB_DB_NAME') or os.environ.get('MONGO_DB_NAME')
    if explicit_db and explicit_db.strip():
        db_name = explicit_db.strip()
    else:
        try:
            default_db = client.get_default_database()
            db_name = default_db.name if (default_db is not None and default_db.name) else 'acadfusion_ai'
        except Exception:
            db_name = 'acadfusion_ai'

    if not (explicit_db and explicit_db.strip()) and db_name == 'vtu_analyzer':
        db_name = 'acadfusion_ai'

    db = client[db_name]
    coll = db['users']
    
    users = list(coll.find({}, {'password_hash': 0})) # Never load password hashes in inspection
    print(f"Database '{db_name}' - Registered users count: {len(users)}")
    
    for u in users:
        print(f"User: {u.get('name')} | Email: {u.get('email')} | Role: {u.get('role', 'user')} | Status: {u.get('account_status', 'active')}")

except Exception as e:
    print(f"Error: {e}")
