"""
rag_engine.py - RAG-based semantic search for Faculty AI.

Uses sentence-transformers to generate embeddings for faculty profiles
and performs cosine similarity search for semantic matching.
Combined with keyword scores for hybrid ranking.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

_model = None
_faculty_embeddings = None   # np.ndarray of shape (N, dim)
_faculty_docs = None         # list of document strings
_faculty_ids = None          # list of faculty IDs (parallel to embeddings)
_faculty_map = None          # id -> faculty dict

MIN_SEMANTIC_SIMILARITY = 0.50
MIN_SEMANTIC_ONLY_SCORE = 0.70
MIN_HYBRID_SCORE = 25


def _get_model():
    """Lazy-load the sentence transformer model."""
    global _model
    if _model is None:
        print("[RAG] Loading sentence-transformers model (first time may download ~90MB)...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        print("[RAG] Model loaded successfully.")
    return _model


def _build_document(faculty):
    """Build a single text document from a faculty record for embedding."""
    parts = [
        f"Name: {faculty['name']}",
        f"Department: {faculty['department']}",
        f"Designation: {faculty['designation']}",
        f"Core Subjects: {', '.join(faculty.get('core_subjects', []))}",
        f"Research Areas: {', '.join(faculty.get('research_areas', []))}",
        f"Synonym Tags: {', '.join(faculty.get('synonym_tags', [])[:20])}",
    ]
    summary = faculty.get("profile_summary", "")
    if summary:
        parts.append(f"Profile: {summary}")
    return " | ".join(parts)


def build_faculty_embeddings(faculty_data):
    """Pre-compute embeddings for all faculty profiles. Call once on startup."""
    global _faculty_embeddings, _faculty_docs, _faculty_ids, _faculty_map

    model = _get_model()

    _faculty_map = {f["id"]: f for f in faculty_data}
    _faculty_ids = [f["id"] for f in faculty_data]
    _faculty_docs = [_build_document(f) for f in faculty_data]

    print(f"[RAG] Encoding {len(_faculty_docs)} faculty profiles...")
    _faculty_embeddings = model.encode(_faculty_docs, convert_to_numpy=True, normalize_embeddings=True)
    print(f"[RAG] Faculty embeddings ready. Shape: {_faculty_embeddings.shape}")


def semantic_search(query, top_k=5, min_similarity=MIN_SEMANTIC_SIMILARITY):
    """
    Perform semantic search: encode the query and find closest faculty embeddings.

    Returns: list of (similarity_score, faculty_dict) sorted by score descending.
    """
    if _faculty_embeddings is None:
        return []

    model = _get_model()
    query_embedding = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)

    # Cosine similarity (embeddings are already normalized, so dot product = cosine sim)
    similarities = np.dot(_faculty_embeddings, query_embedding.T).flatten()

    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(similarities[idx])
        if score >= min_similarity:
            fid = _faculty_ids[idx]
            results.append((score, _faculty_map[fid]))

    return results


def hybrid_search(
    query,
    keyword_results,
    semantic_weight=0.6,
    keyword_weight=0.4,
    top_k=5,
    min_semantic_similarity=MIN_SEMANTIC_SIMILARITY,
    min_semantic_only_score=MIN_SEMANTIC_ONLY_SCORE,
    min_hybrid_score=MIN_HYBRID_SCORE,
):
    """
    Combine keyword search scores with semantic search scores.

    Returns: list of (combined_score, faculty_dict) sorted by score descending.
    """
    sem_results = semantic_search(
        query,
        top_k=len(_faculty_ids) if _faculty_ids else 5,
        min_similarity=min_semantic_similarity,
    )

    kw_scores = {}
    if keyword_results:
        max_kw = max(s for s, _ in keyword_results) or 1
        for score, fac in keyword_results:
            kw_scores[fac["id"]] = score / max_kw

    sem_scores = {}
    if sem_results:
        max_sem = max(s for s, _ in sem_results) or 1
        for score, fac in sem_results:
            sem_scores[fac["id"]] = score / max_sem

    all_ids = set(kw_scores.keys()) | set(sem_scores.keys())
    faculty_map = {
        f["id"]: f
        for f in [fac for _, fac in keyword_results] + [fac for _, fac in sem_results]
    }

    combined = []
    for fid in all_ids:
        kw = kw_scores.get(fid, 0.0)
        sem = sem_scores.get(fid, 0.0)
        hybrid_score = (semantic_weight * sem) + (keyword_weight * kw)
        combined.append((hybrid_score, faculty_map[fid], kw, sem))

    combined.sort(key=lambda x: x[0], reverse=True)

    results = []
    for hybrid_score, fac, kw, sem in combined[:top_k]:
        # If a match comes only from semantic retrieval, require stronger confidence.
        if kw == 0.0 and sem < min_semantic_only_score:
            continue

        mapped_score = int(hybrid_score * 100)
        if mapped_score >= min_hybrid_score:
            results.append((mapped_score, fac))

    return results
