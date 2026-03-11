"""
db.py — Chat history storage for Faculty AI.

Strategy:
  1. Load environment variables from .env file
  2. Connect to MongoDB Atlas
  3. Strict MongoDB reliance (no local JSON fallback)
"""

import os
from datetime import datetime

# ─── Load .env file ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    print("[DB] ✅ Loaded .env file.")
except ImportError:
    print("[DB] ⚠️  python-dotenv not installed. Using system environment variables only.")


# ─── MongoDB Connection ─────────────────────────────────────────────────────────
import certifi
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")

if not MONGO_URI:
    raise ValueError("MONGO_URI not set in .env or environment. MongoDB connection is required.")

# Mask URI for safe logging (show user + cluster, hide password)
try:
    masked = MONGO_URI.split("@")[1].split("/")[0] if "@" in MONGO_URI else "unknown"
    print(f"[DB] Connecting to MongoDB Atlas ({masked})...")
except Exception:
    print("[DB] Connecting to MongoDB Atlas...")

try:
    _client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        tls=True,
        tlsCAFile=certifi.where()
    )

    # Startup ping to verify connection
    _client.admin.command("ping")
    print("[DB] ✅ MongoDB Atlas connected successfully!")
except Exception as e:
    print(f"[DB] ❌ CRITICAL: MongoDB connection failed: {e}")
    raise ConnectionError(f"Strict MongoDB mode enabled, but connection failed: {e}")

# Extract database name from URI or use default
db_name = "facultyai"
if "/" in MONGO_URI.split("@")[-1]:
    uri_db = MONGO_URI.split("@")[-1].split("/")[1].split("?")[0]
    if uri_db:
        db_name = uri_db

_db = _client[db_name]
print(f"[DB] ✅ Using database: '{db_name}'")

_db.chat_messages.create_index([("session_id", 1), ("timestamp", 1)])
_db.chat_sessions.create_index([("session_id", 1)], unique=True)
_db.chat_sessions.create_index([("updated_at", -1)])

print("[DB] ✅ Indexes verified. MongoDB is ready.")


# ─── Unified API ──────────────────────────────────────────────────────────────

def save_message(session_id, role, content, response_data=None):
    now = datetime.utcnow()
    
    session_title = content[:50] if role == "user" else "New Chat"

    _db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "session_id": session_id,
                "title": session_title,
                "created_at": now,
            },
        },
        upsert=True,
    )

    message = {
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": now,
    }

    if response_data:
        message["response_data"] = response_data

    _db.chat_messages.insert_one(message)


def get_history(session_id):
    messages = list(
        _db.chat_messages.find(
            {"session_id": session_id},
            {"_id": 0, "session_id": 0},
        ).sort("timestamp", 1)
    )

    for msg in messages:
        msg["timestamp"] = msg["timestamp"].strftime("%H:%M")

    return messages


def get_sessions(limit=20):
    sessions = list(
        _db.chat_sessions.find({}, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )

    for s in sessions:
        s["created_at"] = s["created_at"].strftime("%b %d, %H:%M")
        s["updated_at"] = s["updated_at"].strftime("%b %d, %H:%M")

    return sessions


def clear_history(session_id):
    _db.chat_messages.delete_many({"session_id": session_id})
    _db.chat_sessions.delete_one({"session_id": session_id})


def clear_all_history():
    _db.chat_messages.delete_many({})
    _db.chat_sessions.delete_many({})