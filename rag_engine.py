"""
rag_engine.py - RAG-based semantic search for Faculty AI.
STRICT subject filtering added to prevent random matches.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

_model = None
_faculty_embeddings = None
_faculty_docs = None
_faculty_ids = None
_faculty_map = None

MIN_SEMANTIC_SIMILARITY = 0.55
MIN_SEMANTIC_ONLY_SCORE = 0.75
MIN_HYBRID_SCORE = 35


# ─────────────────────────────────────────────────────────────

def _get_model():
    global _model
    if _model is None:
        print("[RAG] Loading sentence-transformers model...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        print("[RAG] Model loaded.")
    return _model


def _normalize(text):
    return text.lower().strip()


def _build_document(faculty):
    parts = [
        f"Name: {faculty['name']}",
        f"Department: {faculty['department']}",
        f"Designation: {faculty['designation']}",
        f"Core Subjects: {', '.join(faculty.get('core_subjects', []))}",
        f"Research Areas: {', '.join(faculty.get('research_areas', []))}",
        f"Synonym Tags: {', '.join(faculty.get('synonym_tags', []))}",
    ]
    summary = faculty.get("profile_summary", "")
    if summary:
        parts.append(f"Profile: {summary}")
    return " | ".join(parts)


# ─────────────────────────────────────────────────────────────

def build_faculty_embeddings(faculty_data):
    global _faculty_embeddings, _faculty_docs, _faculty_ids, _faculty_map

    model = _get_model()

    _faculty_map = {f["id"]: f for f in faculty_data}
    _faculty_ids = [f["id"] for f in faculty_data]
    _faculty_docs = [_build_document(f) for f in faculty_data]

    print(f"[RAG] Encoding {len(_faculty_docs)} faculty profiles...")
    _faculty_embeddings = model.encode(
        _faculty_docs,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    print("[RAG] Embeddings ready.")


# ─────────────────────────────────────────────────────────────
# STRICT SUBJECT FILTERING
# ─────────────────────────────────────────────────────────────

def _subject_filter(query):
    """
    Hard filter faculty based on core_subjects + synonym_tags.
    Prevents random faculty return.
    """
    if not _faculty_map:
        return []

    query = _normalize(query)

    matched_faculty_ids = []

    for fid, faculty in _faculty_map.items():
        subjects = [s.lower() for s in faculty.get("core_subjects", [])]
        synonyms = [s.lower() for s in faculty.get("synonym_tags", [])]

        for keyword in subjects + synonyms:
            if keyword in query:
                matched_faculty_ids.append(fid)
                break

    return matched_faculty_ids


# ─────────────────────────────────────────────────────────────

def semantic_search(query, candidate_ids=None):
    if _faculty_embeddings is None:
        return []

    model = _get_model()
    query_embedding = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    similarities = np.dot(_faculty_embeddings, query_embedding.T).flatten()

    results = []

    for idx, score in enumerate(similarities):
        fid = _faculty_ids[idx]

        if candidate_ids and fid not in candidate_ids:
            continue

        if score >= MIN_SEMANTIC_SIMILARITY:
            results.append((float(score), _faculty_map[fid]))

    results.sort(key=lambda x: x[0], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────

def hybrid_search(query, keyword_results=None, top_k=5):
    """
    STRICT production-safe hybrid search.
    """

    # STEP 1: HARD FILTER
    candidate_ids = _subject_filter(query)

    # 🚫 If no subject matched → DO NOT run semantic search
    if not candidate_ids:
        return []

    # STEP 2: Semantic only inside filtered faculty
    sem_results = semantic_search(query, candidate_ids=candidate_ids)

    if not sem_results:
        return []

    # Normalize semantic scores
    max_sem = max(s for s, _ in sem_results) or 1

    final_results = []

    for score, fac in sem_results:
        normalized = score / max_sem

        # Strong protection against weak semantic-only matches
        if normalized < MIN_SEMANTIC_ONLY_SCORE:
            continue

        mapped_score = int(normalized * 100)

        if mapped_score >= MIN_HYBRID_SCORE:
            final_results.append((mapped_score, fac))

    return final_results[:top_k]