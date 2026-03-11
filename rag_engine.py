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
                "name": ["Full Name", "Name", "name"],
                "title": ["Title", "title"],
                "first_name": ["First Name", "first_name"],
                "last_name": ["Last Name", "last_name"],
                "designation": ["Designation", "designation"],
                "department": ["Department", "department"],
                "subjects": ["Core Subjects", "subjects"],
                "research_areas": ["Research Areas", "research_areas"],
                "qualification": ["Qualification", "qualification"],
                "experience": ["Years of Experience", "Experience", "experience"],
                "room_no": ["Room / Cabin No.", "Cabin", "room_no", "Room"],
                "available_days": ["Available Days", "available_days"],
                "available_time": ["Available Timings", "Available Time", "available_time"],
                "consultation_mode": ["Consultation Mode", "Consultation Modes", "consultation_mode"],
                "email": ["Email", "email"],
                "phone": ["Phone", "phone"],
                "profile_url": ["Profile URL", "URL", "profile_url"],
                "photo_url": ["Photo URL", "photo_url"],
                "bio": ["Short Bio", "Bio", "bio"],
                "has_phd": ["Has PhD", "has_phd"],
                "profile_completeness": ["Profile Completeness %", "profile_completeness"],
            }
            for snake_key, possible_keys in key_map.items():
                val = "N/A"
                for k in possible_keys:
                    if k in record and str(record[k]).strip() != "N/A" and str(record[k]).strip() != "":
                        val = record[k]
                        break
                normalized[snake_key] = val

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


# ═══════════════════════════════════════════════════════════════
# SUBJECT ↔ DEPARTMENT BOUNDARY MAPS
# ═══════════════════════════════════════════════════════════════

# Abbreviation / short-form → full subject name
ABBREVIATIONS: Dict[str, str] = {
    "os": "operating systems",
    "dld": "digital logic design",
    "bee": "basic electrical engineering",
    "cn": "computer networks",
    "dsa": "data structures and algorithms",
    "ds": "data structures",
    "dbms": "database management systems",
    "ai": "artificial intelligence",
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "se": "software engineering",
    "cd": "compiler design",
    "vlsi": "vlsi design",
    "emc": "electromagnetic compatibility",
    "dsp": "digital signal processing",
    "ece": "electronics and communication",
    "eee": "electrical engineering",
    "cse": "computer science",
    "me": "mechanical engineering",
    "ce": "civil engineering",
    "mca": "mca computer applications",
    "mba": "management",
    "iot": "internet of things",
    "cc": "cloud computing",
    "wd": "web development",
}

# Keywords → which department string fragment to filter against
SUBJECT_DEPT_MAP: List[Tuple[List[str], str]] = [
    # CS / CSE
    (
        ["java", "python", "c++", "c programming", "data structures", "algorithms",
         "dbms", "database", "operating systems", "computer networks", "artificial intelligence",
         "machine learning", "deep learning", "nlp", "natural language",
         "compiler design", "software engineering", "web development", "cloud computing",
         "internet of things", "iot", "data mining", "software", "programming",
         "computer", "theory of computation", "automata", "cryptography",
         "object oriented", "oops", "dsa", "design patterns", "mobile app",
         "android", "information security", "big data", "hadoop", "spark",
         "cyber security", "blockchain"],
        "computer science"
    ),
    # ECE
    (
        ["digital logic", "dld", "digital electronics", "analog circuits",
         "vlsi", "signals and systems", "communication systems", "microprocessors",
         "embedded systems", "arduino", "raspberry", "wireless", "antenna",
         "dsp", "digital signal processing", "rf", "optical fiber",
         "electromagnetics", "microcontroller", "8051", "fpga",
         "telecommunications", "satellite", "radar", "image processing"],
        "electronics"
    ),
    # EEE / Electrical
    (
        ["basic electrical", "bee", "power systems",
         "electrical machines", "control systems", "power electronics",
         "transformers", "motor", "generator", "high voltage",
         "switchgear", "distribution", "transmission", "induction motor",
         "drives", "plc", "scada", "renewable energy", "solar energy"],
        "electrical"
    ),
    # Mechanical
    (
        ["thermodynamics", "fluid mechanics", "machine design", "manufacturing",
         "heat transfer", "solid mechanics", "kinematics", "dynamics",
         "cad", "cam", "cnc", "refrigeration", "air conditioning",
         "turbine", "compressor", "strength of materials", "tribology",
         "metallurgy", "welding", "casting", "sheet metal"],
        "mechanical"
    ),
    # Civil
    (
        ["structural engineering", "surveying", "geotechnical", "environmental engineering",
         "transportation engineering", "concrete", "hydraulics", "water resources",
         "rcc", "highway", "earthquake engineering", "soil mechanics",
         "construction management", "architecture", "building materials"],
        "civil"
    ),
    # Physics
    (
        ["physics", "quantum", "optics", "mechanics", "thermodynamics physics",
         "nuclear physics", "solid state physics", "laser", "semiconductor physics"],
        "physics"
    ),
    # Chemistry
    (
        ["chemistry", "organic chemistry", "inorganic chemistry", "physical chemistry",
         "analytical chemistry", "polymer", "reaction kinetics"],
        "chemistry"
    ),
    # Mathematics
    (
        ["mathematics", "calculus", "linear algebra", "probability", "statistics",
         "differential equations", "engineering mathematics", "numerical methods",
         "discrete mathematics", "graph theory", "optimization"],
        "mathematics"
    ),
    # Management / MBA
    (
        ["management", "mba", "marketing", "finance", "hrm", "human resource",
         "accounting", "economics", "entrepreneurship", "business",
         "operations management", "strategic management"],
        "management"
    ),
    # Biotechnology
    (
        ["biotechnology", "microbiology", "genetics", "biochemistry",
         "cell biology", "molecular biology", "bioinformatics", "genomics"],
        "biotechnology"
    ),
    # Pharmaceutical
    (
        ["pharmacology", "pharmaceutical", "drug", "medicinal chemistry",
         "pharmacy", "toxicology", "clinical research"],
        "pharmaceutical"
    ),
]


def _expand_abbreviation(text: str) -> str:
    """Replace known abbreviations/short-forms with their full names."""
    words = text.lower().strip().split()
    expanded = [ABBREVIATIONS.get(w, w) for w in words]
    return " ".join(expanded)


def _detect_target_department(query: str) -> Optional[str]:
    """
    Given a query, detect which department it belongs to using the subject map.
    Returns a partial department string (e.g. 'computer science', 'chemistry')
    or None if no mapping found.
    """
    q = _expand_abbreviation(_normalize(query))
    for subject_keywords, dept_fragment in SUBJECT_DEPT_MAP:
        for kw in subject_keywords:
            if kw in q:
                return dept_fragment
    return None


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


def search_faculty(query: str, top_k: int = 3, dept_filter: str = None) -> List[Dict]:
    """
    Main search: fuzzy match across all searchable fields boosted by TF-IDF.
    Applies strict subject→department boundary if a mapping is detected.
    Returns top_k results, each with a 'score' field (0-100).
    """
    _check_reload()  # Auto-reload if Excel changed

    if not _faculty_data:
        return []

    # Expand abbreviations first
    expanded_query = _expand_abbreviation(query)
    normalized_query = _normalize(expanded_query)
    if not normalized_query:
        return []

    # Detect if query maps to a specific department (strict boundary enforcement)
    target_dept = dept_filter or _detect_target_department(expanded_query)

    scored_results = []

    for faculty in _faculty_data:
        fac_dept = _normalize(faculty.get("department", ""))

        # STRICT BOUNDARY: if we detected a department, only search within it
        if target_dept and target_dept not in fac_dept:
            continue

        best_score = 0

        # Priority 1: exact subject match (highest weight)
        subj_val = faculty.get("subjects", "")
        if subj_val and subj_val != "N/A":
            score = _fuzzy_score(normalized_query, _normalize(subj_val))
            best_score = max(best_score, int(score * 1.0))

        # Priority 2: research area match
        res_val = faculty.get("research_areas", "")
        if res_val and res_val != "N/A":
            score = _fuzzy_score(normalized_query, _normalize(res_val))
            best_score = max(best_score, int(score * 0.9))

        # Priority 3: name match
        name_val = faculty.get("name", "")
        if name_val and name_val != "N/A":
            score = _fuzzy_score(normalized_query, _normalize(name_val))
            best_score = max(best_score, int(score * 1.0))

        # Priority 4: department / designation / qualification as fallback
        for field, weight in [
            (faculty.get("department", ""),  0.6),
            (faculty.get("designation", ""), 0.5),
            (faculty.get("qualification", ""), 0.4),
        ]:
            if field and field != "N/A":
                score = _fuzzy_score(normalized_query, _normalize(field))
                best_score = max(best_score, int(score * weight))

        # Bonus: if query term appears directly in subjects
        if subj_val and normalized_query in _normalize(subj_val):
            best_score = max(best_score, 90)

        # Bonus: exact name match
        if normalized_query in _normalize(name_val):
            best_score = max(best_score, 95)

        threshold = 35 if target_dept else 40
        if best_score >= threshold:
            result = dict(faculty)
            result["score"] = best_score
            scored_results.append(result)

    # Sort by score descending
    scored_results.sort(key=lambda x: x["score"], reverse=True)

    # TF-IDF cosine similarity boost (only when no strict dept filter)
    if not target_dept and TFIDF_AVAILABLE and _tfidf_matrix is not None and _vectorizer is not None:
        try:
            query_vec = _vectorizer.transform([expanded_query])
            sims = cosine_similarity(query_vec, _tfidf_matrix).flatten()

            for i, sim in enumerate(sims):
                if sim >= 0.15:
                    fac = _faculty_data[i]
                    existing = next((r for r in scored_results if r.get("name") == fac.get("name")), None)
                    if existing:
                        existing["score"] = min(100, existing["score"] + int(sim * 25))
                    elif sim >= 0.25:
                        result = dict(fac)
                        result["score"] = int(sim * 80)
                        scored_results.append(result)

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
    """Find all faculty in a department (robust matching against verbose dept names)."""
    _check_reload()
    results = []

    dept_norm = _normalize(_expand_abbreviation(dept))

    # Map common abbreviations / short names → fragment to search in dept string
    dept_fragment_map = {
        "cse": "computer science",
        "computer science": "computer science",
        "ece": "electronics",
        "electronics": "electronics",
        "eee": "electrical",
        "electrical": "electrical",
        "mechanical": "mechanical",
        "me": "mechanical",
        "civil": "civil",
        "ce": "civil",
        "mba": "management",
        "management": "management",
        "mca": "mca",
        "physics": "physics",
        "chemistry": "chemistry",
        "mathematics": "mathematics",
        "math": "mathematics",
        "maths": "mathematics",
        "biotechnology": "biotechnology",
        "bio": "biotechnology",
        "pharmaceutical": "pharmaceutical",
        "pharma": "pharmaceutical",
        "english": "english",
        "admission": "admission",
        "it infrastructure": "it infrastructure",
        "pharmacy": "pharmaceutical",
    }

    # Find the best fragment to match
    search_fragment = None
    for key, fragment in dept_fragment_map.items():
        if key in dept_norm:
            search_fragment = fragment
            break

    # Fallback: use the raw input
    if not search_fragment:
        search_fragment = dept_norm

    for fac in _faculty_data:
        fac_dept = _normalize(fac.get("department", ""))
        if search_fragment in fac_dept:
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

    # Step 2: Department-specific query — MUST be checked BEFORE generic "show all"
    # e.g. "Show all CSE faculty", "Show all Chemistry faculty"
    dept_match = re.search(r"(?:show|list|all)\s+(\w[\w\s]*?)\s+faculty", lower)
    if dept_match:
        dept_query = dept_match.group(1).strip()
        # Only proceed if it's not purely "all" (which would be "all all faculty" edge case)
        if dept_query not in ("all", "the"):
            dept_results = get_faculty_by_department(dept_query)
            if dept_results:
                return {
                    "type": "faculty_list",
                    "results": dept_results,
                    "reply": f"Found {len(dept_results)} faculty in {dept_query.upper()} department:",
                    "query": user_input,
                }

    # Step 3: Generic "list all / show all faculty" (no specific dept)
    list_patterns = ["list all faculty", "show all faculty", "all faculty", "all professors",
                     "all teachers", "faculty list", "show faculty", "who are the faculty",
                     "list all"]
    if any(p in lower for p in list_patterns):
        all_fac = get_all_faculty()
        return {
            "type": "faculty_list",
            "results": all_fac,
            "reply": f"Here are all {len(all_fac)} faculty members:",
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