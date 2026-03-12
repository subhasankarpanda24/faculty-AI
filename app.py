# ==========================================
# Faculty AI - RAG Faculty Search Chatbot
# Copyright (c) 2026 Subha Sankar Panda
# All Rights Reserved.
# Unauthorized copying, modification, or
# distribution of this software is prohibited.
# ==========================================

"""
app.py — Flask application for NIST Faculty AI
================================================
Data source: Excel file (output/NIST_Faculty_Directory.xlsx) — NO JSON.
If Excel doesn't exist, shows helpful error to run scraper first.

Routes:
  /              → Main chat UI
  /ask           → Legacy chat endpoint (backward compatible)
  /api/chat      → New chat endpoint (uses upgraded RAG engine)
  /api/reload    → Reload Excel data without restart
  /api/faculty/all → Return all faculty as JSON
  /faculty       → Legacy faculty list endpoint
  /health        → Health check
  /history/*     → Chat history endpoints
"""

import os
# Render memory optimization
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import sys
from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

# ─── Check for Excel data ────────────────────────────────────────────────────
EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "NIST_Faculty_Directory.xlsx")

if not os.path.exists(EXCEL_PATH):
    print("=" * 65)
    print("[APP] ⚠️  Excel data file not found!")
    print(f"[APP] Expected: {EXCEL_PATH}")
    print("[APP] Please run: python run_scraper.py")
    print("[APP] This will generate the faculty data from NIST website.")
    print("=" * 65)
    # Don't exit — allow app to start but endpoints will return errors

# ─── Chat History (MongoDB with JSON file fallback) ──────────────────────────
from db import save_message, get_history, get_sessions, clear_history, clear_all_history
MONGO_AVAILABLE = True  # db.py handles fallback internally

# ─── RAG Engine (Excel-based semantic search) ────────────────────────────────
try:
    from rag_engine import (
        initialize as rag_initialize,
        handle_chat_message,
        search_faculty,
        get_all_faculty,
        get_all_departments,
        get_faculty_by_name,
        get_faculty_by_department,
        reload_data as rag_reload,
        format_faculty_card,
    )
    faculty_count = rag_initialize(EXCEL_PATH)
    RAG_AVAILABLE = faculty_count > 0
    if RAG_AVAILABLE:
        print(f"[APP] ✅ RAG engine ready with {faculty_count} faculty.")
    else:
        print("[APP] ⚠️  RAG engine loaded but no faculty data. Run scraper first.")
except Exception as e:
    RAG_AVAILABLE = False
    print(f"[APP] ⚠️  RAG engine not available: {e}")
    # Define stub functions so routes don't crash
    def handle_chat_message(msg): return {"type": "error", "reply": "Faculty data not loaded. Run python run_scraper.py first."}
    def get_all_faculty(): return []
    def get_all_departments(): return []
    def rag_reload(path=None): return 0
    def search_faculty(q, top_k=3): return []

# ─── Response Builder ──────────────────────────────────────────────────────
def build_faculty_payload(faculty, score=0, rank=1):
    """Build a structured faculty payload for the frontend."""
    confidence = "high" if score >= 70 else "medium" if score >= 45 else "low"

    # Handle subjects/research that might be strings or lists
    subjects = faculty.get("subjects", "N/A")
    if isinstance(subjects, str) and subjects != "N/A":
        subjects_list = [s.strip() for s in subjects.split(",")]
    elif isinstance(subjects, list):
        subjects_list = subjects
    else:
        subjects_list = []

    research = faculty.get("research_areas", "N/A")
    if isinstance(research, str) and research != "N/A":
        research_list = [r.strip() for r in research.split(",")]
    elif isinstance(research, list):
        research_list = research
    else:
        research_list = []

    modes = faculty.get("consultation_mode", "N/A")
    if isinstance(modes, str) and modes != "N/A":
        modes_str = modes
    elif isinstance(modes, list):
        modes_str = ", ".join(modes)
    else:
        modes_str = "N/A"

    days = faculty.get("available_days", "N/A")
    if isinstance(days, list):
        days_str = ", ".join(days)
    else:
        days_str = str(days)

    return {
        "name": faculty.get("name", "N/A"),
        "designation": faculty.get("designation", "N/A"),
        "department": faculty.get("department", "N/A"),
        "core_subjects": subjects_list,
        "research_areas": research_list,
        "experience": faculty.get("experience", "N/A"),
        "qualification": faculty.get("qualification", "N/A"),
        "email": faculty.get("email", "N/A"),
        "phone": faculty.get("phone", "N/A"),
        "cabin": faculty.get("room_no", "N/A"),
        "available_days": days_str,
        "available_time": faculty.get("available_time", "N/A"),
        "consultation_modes": modes_str,
        "profile_summary": faculty.get("bio", "N/A"),
        "confidence": confidence,
        "score": score,
        "rank": rank,
        "has_phd": faculty.get("has_phd", False),
        "profile_url": faculty.get("profile_url", "N/A"),
    }


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def home():
    """Serve the main chat UI."""
    return render_template("index.html")


# ─── NEW: /api/chat endpoint ─────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    New chat endpoint using the upgraded RAG engine.
    Takes: { "message": "...", "session_id": "..." }
    Returns structured response with faculty cards.
    """
    data = request.get_json()
    user_input = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    timestamp = datetime.now().strftime("%H:%M")

    if not user_input:
        return jsonify({"type": "error", "reply": "Please type something.", "timestamp": timestamp})

    # Save user message
    try:
        save_message(session_id, "user", user_input)
    except Exception as e:
        print(f"[DB] Failed to save user message: {e}")

    # Get response from RAG engine
    response = handle_chat_message(user_input)
    response["timestamp"] = timestamp

    # Format faculty results for frontend
    if response.get("results"):
        formatted_results = []
        for i, fac in enumerate(response["results"]):
            formatted_results.append(build_faculty_payload(fac, fac.get("score", 0), rank=i+1))
        response["results"] = formatted_results

    # Build reply text for history
    reply_text = response.get("reply", "")
    if not reply_text and response.get("results"):
        reply_text = f"Found: {response['results'][0].get('name', 'faculty')}"

    # Save bot response
    try:
        save_message(session_id, "bot", reply_text, response_data=response)
    except Exception as e:
        print(f"[DB] Failed to save bot response: {e}")

    return jsonify(response)


# ─── NEW: /api/reload endpoint ───────────────────────────────────────────────
@app.route("/api/reload", methods=["POST"])
def api_reload():
    """Reload faculty data from Excel without restarting the server."""
    global RAG_AVAILABLE
    try:
        count = rag_reload(EXCEL_PATH)
        RAG_AVAILABLE = count > 0
        return jsonify({
            "status": "success",
            "faculty_count": count,
            "message": f"Reloaded {count} faculty records from Excel."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── NEW: /api/faculty/all endpoint ──────────────────────────────────────────
@app.route("/api/faculty/all", methods=["GET"])
def api_faculty_all():
    """Return all faculty data as JSON (from Excel)."""
    try:
        all_fac = get_all_faculty()
        return jsonify([build_faculty_payload(f, f.get("score", 0), i+1)
                        for i, f in enumerate(all_fac)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── LEGACY: /ask endpoint (backward compatible) ─────────────────────────────
@app.route("/ask", methods=["POST"])
def ask():
    """
    Legacy chat endpoint — maintained for backward compatibility.
    Delegates to the new RAG engine.
    """
    data = request.get_json()
    user_input = data.get("question", "").strip()
    session_id = data.get("session_id", "default")
    timestamp = datetime.now().strftime("%H:%M")

    if not user_input:
        return jsonify({"type": "error", "reply": "Please type something.", "timestamp": timestamp})

    # Save user message
    try:
        save_message(session_id, "user", user_input)
    except Exception as e:
        print(f"[DB] Failed to save user message: {e}")

    # Use new RAG engine
    response = handle_chat_message(user_input)

    # Map response to legacy format
    resp_type = response.get("type", "error")
    results = response.get("results", [])

    if resp_type in ("greeting", "farewell", "thanks", "help"):
        reply_data = {
            "type": resp_type if resp_type != "help" else "greeting",
            "reply": response.get("reply", ""),
            "timestamp": timestamp,
        }

    elif resp_type == "faculty_list":
        faculty_list = []
        for fac in results:
            faculty_list.append({
                "name": fac.get("name", "N/A"),
                "designation": fac.get("designation", "N/A"),
                "department": fac.get("department", "N/A"),
                "core_subjects": fac.get("subjects", "N/A").split(",") if isinstance(fac.get("subjects"), str) else [],
                "cabin": fac.get("room_no", "N/A"),
                "email": fac.get("email", "N/A"),
            })
        reply_data = {
            "type": "list_all",
            "faculty_list": faculty_list,
            "timestamp": timestamp,
        }

    elif resp_type == "faculty" and results:
        primary = build_faculty_payload(results[0], results[0].get("score", 0), rank=1)
        alternates = []
        for fac in results[1:3]:
            alternates.append({
                "name": fac.get("name", "N/A"),
                "department": fac.get("department", "N/A"),
                "core_subjects": fac.get("subjects", "N/A").split(",") if isinstance(fac.get("subjects"), str) else [],
                "cabin": fac.get("room_no", "N/A"),
                "email": fac.get("email", "N/A"),
            })
        reply_data = {
            "type": "faculty",
            "faculty": primary,
            "alternates": alternates,
            "timestamp": timestamp,
        }

    elif resp_type == "not_found":
        reply_data = {
            "type": "not_found",
            "reply": response.get("reply", "No match found."),
            "suggestions": response.get("suggestions", []),
            "partial_matches": [build_faculty_payload(f, f.get("score", 0)) for f in results[:2]],
            "timestamp": timestamp,
        }

    else:
        reply_data = {
            "type": "error",
            "reply": response.get("reply", "Something went wrong."),
            "timestamp": timestamp,
        }

    # Save bot response
    try:
        bot_text = reply_data.get("reply", "")
        if not bot_text and reply_data.get("type") == "faculty":
            bot_text = f"Found: {reply_data['faculty']['name']}"
        save_message(session_id, "bot", bot_text, response_data=reply_data)
    except Exception as e:
        print(f"[DB] Failed to save bot response: {e}")

    return jsonify(reply_data)


# ─── LEGACY: /faculty endpoint ───────────────────────────────────────────────
@app.route("/faculty", methods=["GET"])
def list_faculty():
    """Legacy endpoint: list all faculty for sidebar."""
    try:
        all_fac = get_all_faculty()
        return jsonify([
            {
                "id": f"FAC{i+1:03d}",
                "name": f.get("name", "N/A"),
                "designation": f.get("designation", "N/A"),
                "department": f.get("department", "N/A"),
                "core_subjects": f.get("subjects", "N/A").split(",") if isinstance(f.get("subjects"), str) and f.get("subjects") != "N/A" else [],
                "cabin": f.get("room_no", "N/A"),
                "email": f.get("email", "N/A"),
                "available_time": f.get("available_time", "N/A"),
                "profile_summary": f.get("bio", "N/A"),
            }
            for i, f in enumerate(all_fac)
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── History Routes (unchanged) ──────────────────────────────────────────────
@app.route("/history", methods=["GET"])
def history_sessions():
    """Get all chat sessions."""
    try:
        sessions = get_sessions()
        return jsonify(sessions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/history/<session_id>", methods=["GET"])
def history_messages(session_id):
    """Get all messages for a specific session."""
    try:
        messages = get_history(session_id)
        return jsonify(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/history/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    """Delete a specific chat session."""
    try:
        clear_history(session_id)
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/history", methods=["DELETE"])
def delete_all_history():
    """Clear all chat history."""
    try:
        clear_all_history()
        return jsonify({"status": "cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Health Check ─────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    all_fac = get_all_faculty() if RAG_AVAILABLE else []
    return jsonify({
        "status": "ok",
        "faculty_count": len(all_fac),
        "data_source": "Excel",
        "excel_exists": os.path.exists(EXCEL_PATH),
        "mongo_available": MONGO_AVAILABLE,
        "rag_available": RAG_AVAILABLE,
        "timestamp": datetime.now().isoformat()
    })


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)