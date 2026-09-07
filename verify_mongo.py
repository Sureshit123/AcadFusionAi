import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

from models.database import clean_mongo_uri

raw_uri = os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI')

if not raw_uri:
    print("No MONGODB_URI or MONGO_URI found in environment.")
    exit(1)

mongo_uri = clean_mongo_uri(raw_uri)

# Safely mask host/credentials in logs
safe_display = mongo_uri.split('@')[-1].split('?')[0] if '@' in mongo_uri else "localhost / hidden"
print(f"Testing connection to cluster: {safe_display}")

try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("Successfully connected to MongoDB cluster!")

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
    print(f"Target Project Database: {db_name}")

    collections = db.list_collection_names()
    print(f"Collections in '{db_name}': {collections}")

    users_count = db['users'].count_documents({}) if 'users' in collections else 0
    print(f"Registered users count in '{db_name}.users': {users_count}")

except Exception as e:
    print(f"Connection failed: {e}")
