"""
rag_engine.py — Lightweight RAG-based faculty search engine
============================================================
Data source: Excel file (NIST_Faculty_Directory.xlsx) — NO JSON.
Optimized for Render free tier (512 MB RAM).

Search backend: TF-IDF (scikit-learn) + rapidfuzz fuzzy matching.
NO torch / sentence-transformers — uses 5 % of the memory.

Features:
  - Loads faculty from Excel via pandas
  - Auto re-indexes when Excel file is updated (mtime check)
  - Greeting / help / thanks / bye conversation flow
  - Fuzzy matching via rapidfuzz for typo tolerance
  - TF-IDF + cosine similarity for semantic-like matching
  - Top 3 ranked results scored out of 100%
  - No-match helpful suggestions with top 2 partial matches
  - Follow-up handling: name, department, day, subject queries
"""

import os
import re
import time
from typing import List, Dict, Tuple, Optional

import numpy as np

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("[RAG] WARNING: pandas not installed.")

try:
    from rapidfuzz import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    print("[RAG] WARNING: rapidfuzz not installed. Fuzzy matching disabled.")

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    TFIDF_AVAILABLE = True
except ImportError:
    TFIDF_AVAILABLE = False
    print("[RAG] WARNING: scikit-learn not installed. TF-IDF search disabled.")


# ═══════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════

_faculty_data: List[Dict] = []           # All faculty records loaded from Excel
_excel_path: str = ""                     # Path to the Excel file
_excel_mtime: float = 0.0                # Last modification time of the Excel file
_vectorizer = None                        # TF-IDF vectorizer
_tfidf_matrix = None                      # TF-IDF document-term matrix
_faculty_docs: List[str] = []             # Text documents for TF-IDF

# Excel file location (relative to this script)
DEFAULT_EXCEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "output", "NIST_Faculty_Directory.xlsx"
)


# ═══════════════════════════════════════════════════════════════
# DATA LOADING FROM EXCEL
# ═══════════════════════════════════════════════════════════════

def load_faculty_from_excel(excel_path: str = None) -> List[Dict]:
    """
    Load faculty data from the Excel file.
    Returns a list of dicts, one per faculty member.
    """
    global _faculty_data, _excel_path, _excel_mtime

    if excel_path is None:
        excel_path = DEFAULT_EXCEL_PATH

    if not os.path.exists(excel_path):
        print(f"[RAG] Excel file not found: {excel_path}")
        return []

    if not PANDAS_AVAILABLE:
        print("[RAG] pandas not installed — cannot load Excel.")
        return []

    try:
        df = pd.read_excel(excel_path, sheet_name="All Faculty", engine="openpyxl")
        records = df.to_dict(orient="records")

        # Clean up NaN values → "N/A"
        for record in records:
            for key, value in record.items():
                if pd.isna(value):
                    record[key] = "N/A"
                else:
                    record[key] = str(value).strip()

            # Convert has_phd back to boolean
            has_phd_val = record.get("Has PhD", record.get("has_phd", "No"))
            record["has_phd"] = str(has_phd_val).strip().lower() in ("yes", "true", "1")

            # Convert profile_completeness to int
            try:
                record["profile_completeness"] = int(float(record.get("Profile Completeness %",
                                                                       record.get("profile_completeness", 0))))
            except (ValueError, TypeError):
                record["profile_completeness"] = 0

            # Normalize field names (Excel headers → snake_case keys)
            normalized = {}
            key_map = {
                "Full Name": "name",
                "Title": "title",
                "First Name": "first_name",
                "Last Name": "last_name",
                "Designation": "designation",
                "Department": "department",
                "Core Subjects": "subjects",
                "Research Areas": "research_areas",
                "Qualification": "qualification",
                "Years of Experience": "experience",
                "Room / Cabin No.": "room_no",
                "Available Days": "available_days",
                "Available Timings": "available_time",
                "Consultation Mode": "consultation_mode",
                "Email": "email",
                "Phone": "phone",
                "Profile URL": "profile_url",
                "Photo URL": "photo_url",
                "Short Bio": "bio",
                "Has PhD": "has_phd",
                "Profile Completeness %": "profile_completeness",
            }
            for excel_key, snake_key in key_map.items():
                if excel_key in record:
                    normalized[snake_key] = record[excel_key]
                elif snake_key in record:
                    normalized[snake_key] = record[snake_key]
                else:
                    normalized[snake_key] = "N/A"

            record.update(normalized)

        _faculty_data = records
        _excel_path = excel_path
        _excel_mtime = os.path.getmtime(excel_path)

        print(f"[RAG] Loaded {len(records)} faculty from Excel.")
        return records

    except Exception as e:
        print(f"[RAG] Error loading Excel: {e}")
        return []


def _check_reload():
    """Auto re-index if Excel file has been updated."""
    global _excel_mtime

    if not _excel_path or not os.path.exists(_excel_path):
        return

    current_mtime = os.path.getmtime(_excel_path)
    if current_mtime > _excel_mtime:
        print("[RAG] Excel file updated — reloading...")
        load_faculty_from_excel(_excel_path)
        _build_embeddings()


def reload_data(excel_path: str = None) -> int:
    """Public API: reload data from Excel. Returns count of faculty loaded."""
    records = load_faculty_from_excel(excel_path)
    if records:
        _build_embeddings()
    return len(records)


# ═══════════════════════════════════════════════════════════════
# TF-IDF EMBEDDINGS (lightweight — replaces sentence-transformers)
# ═══════════════════════════════════════════════════════════════

def _build_document(faculty: Dict) -> str:
    """Build a searchable text document from a faculty record."""
    parts = [
        f"Name: {faculty.get('name', '')}",
        f"Department: {faculty.get('department', '')}",
        f"Designation: {faculty.get('designation', '')}",
        f"Subjects: {faculty.get('subjects', '')}",
        f"Research: {faculty.get('research_areas', '')}",
        f"Qualification: {faculty.get('qualification', '')}",
        f"Bio: {faculty.get('bio', '')}",
    ]
    return " | ".join(p for p in parts if "N/A" not in p)


def _build_embeddings():
    """Build TF-IDF index for all loaded faculty."""
    global _vectorizer, _tfidf_matrix, _faculty_docs

    if not _faculty_data:
        return

    if not TFIDF_AVAILABLE:
        print("[RAG] scikit-learn not available — TF-IDF search disabled.")
        return

    _faculty_docs = [_build_document(f) for f in _faculty_data]
    _vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=1,
        stop_words='english'
    )
    _tfidf_matrix = _vectorizer.fit_transform(_faculty_docs)
    print(f"[RAG] TF-IDF index built for {len(_faculty_docs)} faculty.")


# ═══════════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY — old API used by app.py
# ═══════════════════════════════════════════════════════════════

def build_faculty_embeddings(faculty_data_from_json):
    """
    Legacy API: called by old app.py with faculty_data from JSON.
    Now we ignore the JSON data and load from Excel instead.
    """
    if not _faculty_data:
        load_faculty_from_excel()
    _build_embeddings()


def hybrid_search(query, keyword_results=None, top_k=5):
    """
    Legacy API: called by old app.py's smart_search().
    Now delegates to our new search system.
    """
    results = search_faculty(query, top_k=top_k)
    # Convert to old format: list of (score, faculty_dict)
    return [(r["score"], r) for r in results]


# ═══════════════════════════════════════════════════════════════
# GREETING & CONVERSATION FLOW
# ═══════════════════════════════════════════════════════════════

GREETINGS = ["hi", "hello", "hey", "greetings", "good morning", "good afternoon",
             "good evening", "howdy", "namaste", "hii", "helo", "sup", "yo"]

FAREWELLS = ["bye", "goodbye", "see you", "take care", "exit", "quit",
             "cya", "see ya", "later", "good night"]

THANKS = ["thank", "thanks", "thank you", "thx", "appreciated",
          "helpful", "great help", "tysm", "thankful"]

HELP_QUERIES = ["what can you do", "help", "how to use", "what do you do",
                "capabilities", "features", "how does this work"]


def detect_conversation_intent(text: str) -> Optional[str]:
    """
    Detect if the user message is a greeting, farewell, thanks, or help request.
    Returns the intent type or None if it's a search query.
    """
    t = text.lower().strip()
    words = set(re.split(r'[\s!?,.:;]+', t))

    # Greetings — match whole words only and only for short messages
    greeting_words = {"hi", "hello", "hey", "greetings", "howdy", "namaste", "hii", "helo", "sup", "yo"}
    greeting_phrases = ["good morning", "good afternoon", "good evening"]
    if words & greeting_words and len(words) <= 5:
        return "greeting"
    if any(t == g or t.startswith(g + " ") or t.startswith(g + "!") for g in greeting_phrases):
        return "greeting"

    # Farewells
    farewell_phrases = ["bye", "goodbye", "see you", "take care", "exit", "quit",
                        "cya", "see ya", "later", "good night"]
    if any(f in t for f in farewell_phrases) and len(words) <= 6:
        return "farewell"

    # Thanks — whole word match
    thanks_words = {"thank", "thanks", "thx", "tysm", "thankful"}
    if words & thanks_words:
        return "thanks"
    if "thank you" in t or "appreciated" in t or "great help" in t:
        return "thanks"

    # Help
    if any(h in t for h in HELP_QUERIES):
        return "help"

    return None  # It's a search query


def get_conversation_response(intent: str) -> str:
    """Return a themed response for conversation intents."""
    if intent == "greeting":
        return (
            "👋 Hello! Welcome to NIST Faculty Finder!\n"
            "I can help you find the perfect faculty member based on:\n"
            "📚 Subject or Topic  |  🔬 Research Area  |  🏢 Department\n"
            "Just tell me what you're looking for!"
        )
    elif intent == "farewell":
        return (
            "👋 Goodbye! It was great helping you today.\n"
            "Come back anytime you need to find a faculty member.\n"
            "Have a wonderful day! 😊"
        )
    elif intent == "thanks":
        return (
            "😊 You're welcome! I'm glad I could help.\n"
            "Feel free to ask about any subject, department, or faculty member anytime!"
        )
    elif intent == "help":
        return (
            "🤖 Here's what I can do:\n\n"
            "📚 **Find by Subject** — \"Who teaches Machine Learning?\"\n"
            "🔬 **Find by Research** — \"Faculty working on IoT\"\n"
            "🏢 **Find by Department** — \"Show all CSE faculty\"\n"
            "👤 **Find by Name** — \"Tell me about Dr. Mishra\"\n"
            "📅 **Find by Availability** — \"Who is available on Monday?\"\n"
            "📋 **List All** — \"Show all faculty\"\n\n"
            "Just type your question naturally and I'll find the best match!"
        )
    return ""


# ═══════════════════════════════════════════════════════════════
# FUZZY MATCHING ENGINE
# ═══════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalize text for matching."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\+#]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fuzzy_score(query: str, target: str) -> int:
    """
    Calculate fuzzy match score between query and target.
    Returns 0-100.
    """
    if not FUZZY_AVAILABLE:
        # Fallback: simple substring matching
        if query.lower() in target.lower():
            return 80
        return 0

    # Use multiple fuzzy strategies and take the best
    scores = [
        fuzz.ratio(query, target),
        fuzz.partial_ratio(query, target),
        fuzz.token_sort_ratio(query, target),
        fuzz.token_set_ratio(query, target),
    ]
    return max(scores)


def search_faculty(query: str, top_k: int = 3) -> List[Dict]:
    """
    Main search: fuzzy match across all searchable fields,
    boosted by TF-IDF cosine similarity.
    Returns top_k results, each with a 'score' field (0-100).
    """
    _check_reload()  # Auto-reload if Excel changed

    if not _faculty_data:
        return []

    normalized_query = _normalize(query)
    if not normalized_query:
        return []

    scored_results = []

    for faculty in _faculty_data:
        best_score = 0

        # Match against various fields with different weights
        match_fields = [
            (faculty.get("name", ""),           1.0),
            (faculty.get("subjects", ""),        0.95),
            (faculty.get("department", ""),      0.85),
            (faculty.get("research_areas", ""),  0.9),
            (faculty.get("designation", ""),     0.7),
            (faculty.get("qualification", ""),   0.5),
            (faculty.get("bio", ""),             0.4),
        ]

        for field_value, weight in match_fields:
            if not field_value or field_value == "N/A":
                continue

            score = _fuzzy_score(normalized_query, _normalize(field_value))
            weighted = int(score * weight)

            if weighted > best_score:
                best_score = weighted

        # Bonus: exact name match
        if normalized_query in _normalize(faculty.get("name", "")):
            best_score = max(best_score, 95)

        # Bonus: exact department abbreviation match
        dept = _normalize(faculty.get("department", ""))
        dept_abbrs = {
            "cse": "computer science",
            "ece": "electronics & communication",
            "eee": "electrical engineering",
            "me": "mechanical engineering",
            "ce": "civil engineering",
            "mba": "mba / management",
            "it": "information technology",
        }
        for abbr, full in dept_abbrs.items():
            if abbr in normalized_query.split() and full in dept:
                best_score = max(best_score, 85)

        if best_score >= 40:
            result = dict(faculty)
            result["score"] = best_score
            scored_results.append(result)

    # Sort by score descending
    scored_results.sort(key=lambda x: x["score"], reverse=True)

    # TF-IDF cosine similarity boost (replaces sentence-transformers)
    if TFIDF_AVAILABLE and _tfidf_matrix is not None and _vectorizer is not None:
        try:
            query_vec = _vectorizer.transform([query])
            sims = cosine_similarity(query_vec, _tfidf_matrix).flatten()

            for i, sim in enumerate(sims):
                if sim >= 0.15:  # TF-IDF scores are typically lower than dense embeddings
                    fac = _faculty_data[i]
                    # Check if already in results
                    existing = next((r for r in scored_results if r.get("name") == fac.get("name")), None)
                    if existing:
                        # Boost existing score
                        existing["score"] = min(100, existing["score"] + int(sim * 25))
                    elif sim >= 0.25:
                        # Add new result from TF-IDF search
                        result = dict(fac)
                        result["score"] = int(sim * 80)
                        scored_results.append(result)

            # Re-sort after TF-IDF boost
            scored_results.sort(key=lambda x: x["score"], reverse=True)
        except Exception as e:
            print(f"[RAG] TF-IDF search error: {e}")

    return scored_results[:top_k]


# ═══════════════════════════════════════════════════════════════
# FOLLOW-UP HANDLING
# ═══════════════════════════════════════════════════════════════

def get_faculty_by_name(name: str) -> Optional[Dict]:
    """Find a specific faculty member by name (fuzzy)."""
    _check_reload()
    if not _faculty_data:
        return None

    best_match = None
    best_score = 0

    for fac in _faculty_data:
        score = _fuzzy_score(_normalize(name), _normalize(fac.get("name", "")))
        if score > best_score and score >= 60:
            best_score = score
            best_match = fac

    if best_match:
        result = dict(best_match)
        result["score"] = best_score
        return result
    return None


def get_faculty_by_department(dept: str) -> List[Dict]:
    """Find all faculty in a department (fuzzy)."""
    _check_reload()
    results = []

    dept_norm = _normalize(dept)
    dept_abbrs = {
        "cse": "computer science",
        "ece": "electronics & communication",
        "eee": "electrical engineering",
        "me": "mechanical engineering",
        "ce": "civil engineering",
        "mba": "mba / management",
        "it": "information technology",
    }

    # Expand abbreviation
    expanded = dept_abbrs.get(dept_norm, dept_norm)

    for fac in _faculty_data:
        fac_dept = _normalize(fac.get("department", ""))
        if expanded in fac_dept or dept_norm in fac_dept:
            result = dict(fac)
            result["score"] = 90
            results.append(result)

    return results


def get_faculty_by_day(day: str) -> List[Dict]:
    """Find faculty available on a specific day."""
    _check_reload()
    results = []
    day_norm = day.lower().strip()

    for fac in _faculty_data:
        available = str(fac.get("available_days", "N/A")).lower()
        if day_norm in available:
            result = dict(fac)
            result["score"] = 85
            results.append(result)

    return results


def get_all_faculty() -> List[Dict]:
    """Return all loaded faculty data."""
    _check_reload()
    return list(_faculty_data)


def get_all_departments() -> List[str]:
    """Return unique department names."""
    _check_reload()
    depts = set()
    for fac in _faculty_data:
        dept = fac.get("department", "N/A")
        if dept and dept != "N/A":
            depts.add(dept)
    return sorted(depts)


# ═══════════════════════════════════════════════════════════════
# SMART CHAT HANDLER (main entry point for /api/chat)
# ═══════════════════════════════════════════════════════════════

def handle_chat_message(user_input: str) -> Dict:
    """
    Process a user chat message and return a structured response.

    Returns a dict with:
      - type: "greeting" | "farewell" | "thanks" | "help" | "faculty" |
              "faculty_list" | "not_found" | "department_list"
      - reply: text message (for greeting/farewell/thanks/help/not_found)
      - results: list of faculty dicts (for faculty/faculty_list)
      - query: the original user input
    """
    user_input = user_input.strip()
    if not user_input:
        return {"type": "error", "reply": "Please type something."}

    # Step 1: Check conversation intents
    intent = detect_conversation_intent(user_input)
    if intent:
        return {
            "type": intent,
            "reply": get_conversation_response(intent),
            "query": user_input,
        }

    lower = user_input.lower()

    # Step 2: Check for "list all" / "show all faculty"
    list_patterns = ["list all", "show all", "all faculty", "all professors",
                     "all teachers", "faculty list", "show faculty", "who are the faculty"]
    if any(p in lower for p in list_patterns):
        all_fac = get_all_faculty()
        return {
            "type": "faculty_list",
            "results": all_fac,
            "reply": f"Here are all {len(all_fac)} faculty members:",
            "query": user_input,
        }

    # Step 3: Department query — "show all CSE faculty"
    dept_match = re.search(r"(?:show|list|all)\s+(\w+)\s+faculty", lower)
    if dept_match:
        dept_query = dept_match.group(1)
        dept_results = get_faculty_by_department(dept_query)
        if dept_results:
            return {
                "type": "faculty_list",
                "results": dept_results,
                "reply": f"Found {len(dept_results)} faculty in {dept_query.upper()}:",
                "query": user_input,
            }

    # Step 4: "tell me more about [name]"
    more_match = re.search(r"(?:tell me (?:more )?about|who is|details (?:of|about)|profile of)\s+(.+)", lower)
    if more_match:
        name_query = more_match.group(1).strip()
        fac = get_faculty_by_name(name_query)
        if fac:
            return {
                "type": "faculty",
                "results": [fac],
                "reply": f"Here's the full profile for {fac.get('name', name_query)}:",
                "query": user_input,
            }

    # Step 5: "who is available on [day]"
    day_match = re.search(
        r"(?:available|free|who.*on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        lower
    )
    if day_match:
        day = day_match.group(1).capitalize()
        day_results = get_faculty_by_day(day)
        if day_results:
            return {
                "type": "faculty_list",
                "results": day_results,
                "reply": f"Faculty available on {day}:",
                "query": user_input,
            }

    # Step 6: General search (fuzzy + TF-IDF)
    results = search_faculty(user_input, top_k=3)

    if results:
        return {
            "type": "faculty",
            "results": results,
            "reply": "Here are the best faculty matches I found:",
            "query": user_input,
        }

    # Step 7: No match — provide helpful suggestions
    # Get top 2 partial matches with lower threshold
    partial = search_faculty(user_input, top_k=2) if False else []

    # Try with lower threshold
    _check_reload()
    partial_results = []
    if _faculty_data:
        for fac in _faculty_data:
            score = 0
            for field in ["name", "subjects", "department", "research_areas"]:
                val = _normalize(fac.get(field, ""))
                if val and val != "n/a":
                    s = _fuzzy_score(_normalize(user_input), val)
                    score = max(score, s)
            if score >= 25:
                r = dict(fac)
                r["score"] = score
                partial_results.append(r)

        partial_results.sort(key=lambda x: x["score"], reverse=True)
        partial_results = partial_results[:2]

    departments = get_all_departments()
    dept_suggestions = departments[:5] if departments else ["CSE", "ECE", "Mechanical", "Civil", "EEE"]

    reply = (
        f"🔍 I couldn't find an exact match for '{user_input}', but here's what I suggest:\n\n"
        f"• Try searching by subject name instead of topic (e.g., 'Machine Learning' instead of 'AI')\n"
        f"• Try searching by department (e.g., {', '.join(repr(d) for d in dept_suggestions[:3])})\n"
        f"• Or describe what help you need and I'll find the closest faculty!"
    )

    if partial_results:
        reply += "\n\nMeanwhile, here are some faculty you might find helpful:"

    return {
        "type": "not_found",
        "reply": reply,
        "results": partial_results,
        "suggestions": dept_suggestions,
        "query": user_input,
    }


# ═══════════════════════════════════════════════════════════════
# FORMATTING HELPERS (for text-based output)
# ═══════════════════════════════════════════════════════════════

def format_faculty_card(faculty: Dict, rank: int = 1) -> str:
    """Format a single faculty record as a card string with emojis."""
    name = faculty.get("name", "Unknown")
    designation = faculty.get("designation", "N/A")
    department = faculty.get("department", "N/A")
    score = faculty.get("score", 0)
    subjects = faculty.get("subjects", "N/A")
    research = faculty.get("research_areas", "N/A")
    qualification = faculty.get("qualification", "N/A")
    experience = faculty.get("experience", "N/A")
    room = faculty.get("room_no", "N/A")
    days = faculty.get("available_days", "N/A")
    time_slot = faculty.get("available_time", "N/A")
    consultation = faculty.get("consultation_mode", "N/A")
    email = faculty.get("email", "N/A")
    phone = faculty.get("phone", "N/A")
    bio = faculty.get("bio", "N/A")

    card = (
        f"👨‍🏫 {name}\n"
        f"🏷️ {designation} | {department}\n"
        f"⭐ Match Score: {score}%\n\n"
        f"📚 Core Subjects: {subjects}\n"
        f"🔬 Research Areas: {research}\n"
        f"🎓 Qualification: {qualification}\n"
        f"🏅 Experience: {experience}\n"
        f"🏢 Cabin: {room}\n"
        f"📅 Available: {days} | ⏰ {time_slot}\n"
        f"💬 Consultation: {consultation}\n"
        f"📧 {email} | 📞 {phone}\n"
    )

    if bio and bio != "N/A":
        card += f"\n{bio}\n"

    card += "─────────────────────────────"

    return card


# ═══════════════════════════════════════════════════════════════
# INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def initialize(excel_path: str = None):
    """Initialize the RAG engine: load data and build TF-IDF index."""
    records = load_faculty_from_excel(excel_path)
    if records:
        _build_embeddings()
        print(f"[RAG] Engine initialized with {len(records)} faculty.")
    else:
        print("[RAG] WARNING: No faculty data loaded. Run scraper first.")
    return len(records)