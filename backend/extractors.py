import re
import json
import datetime

SCORED_FIELDS = [
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description", "tags",
]
# Exactly 9 fields. extraction_method and confidence_score are NOT in this list.


def compute_confidence(merged: dict, text_extracted: bool) -> float:
    filled = sum(
        1 for f in SCORED_FIELDS
        if merged.get(f) not in (None, "", [], "null", "None")
    )
    score = (filled / 9) * 0.7 + (1.0 if text_extracted else 0.0) * 0.3
    return round(min(max(score, 0.0), 1.0), 4)


def merge_metadata(results: list[dict]) -> dict:
    """Merge field-by-field from multiple extraction passes.

    Pass order for tie-breaking: pdfplumber > ocr > filename_heuristic > ebooklib
    """
    SOURCE_PRIORITY = ["pdfplumber", "ocr", "filename_heuristic", "ebooklib"]

    if not results:
        return {}

    def filled_count(d: dict) -> int:
        return sum(
            1 for f in SCORED_FIELDS
            if d.get(f) not in (None, "", [], "null", "None")
        )

    # Sort by (filled count desc, source priority asc) for title/author/year
    def sort_key(d: dict):
        src = d.get("extraction_method", "")
        priority = SOURCE_PRIORITY.index(src) if src in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
        return (-filled_count(d), priority)

    ranked = sorted(results, key=sort_key)
    best = ranked[0]

    merged: dict = {}

    # title, author, year: from the result with the most filled fields (tie-break by priority)
    for field in ("title", "author", "year"):
        for r in ranked:
            val = r.get(field)
            if val not in (None, "", "null", "None"):
                merged[field] = val
                break
        else:
            merged[field] = None

    # language: prefer valid ISO 639-1 two-letter code
    merged["language"] = None
    for r in ranked:
        lang = r.get("language", "")
        if isinstance(lang, str) and len(lang) == 2 and lang.isalpha():
            merged["language"] = lang.lower()
            break

    # tags: union all lists, deduplicate, lowercase
    all_tags: list[str] = []
    seen: set[str] = set()
    for r in results:
        tags = r.get("tags", [])
        if isinstance(tags, list):
            for t in tags:
                t_lower = str(t).lower().strip()
                if t_lower and t_lower not in seen:
                    seen.add(t_lower)
                    all_tags.append(t_lower)
    merged["tags"] = all_tags if all_tags else []

    # description: longest non-empty value wins
    merged["description"] = None
    best_desc = ""
    for r in results:
        desc = r.get("description") or ""
        if len(desc) > len(best_desc):
            best_desc = desc
    merged["description"] = best_desc if best_desc else None

    # category, subcategory, difficulty: priority order
    for field in ("category", "subcategory", "difficulty"):
        merged[field] = None
        for src_name in SOURCE_PRIORITY:
            for r in results:
                if r.get("extraction_method") == src_name:
                    val = r.get(field)
                    if val not in (None, "", "null", "None"):
                        merged[field] = val
                        break
            if merged[field] is not None:
                break
        # fallback: any result that has it
        if merged[field] is None:
            for r in ranked:
                val = r.get(field)
                if val not in (None, "", "null", "None"):
                    merged[field] = val
                    break

    # extraction_method
    sources_used = [
        r.get("extraction_method", "")
        for r in results
        if any(
            r.get(f) not in (None, "", [], "null", "None")
            for f in SCORED_FIELDS
        )
    ]
    unique_sources = list(dict.fromkeys(s for s in sources_used if s))
    if len(unique_sources) > 1:
        merged["extraction_method"] = "merged"
    elif len(unique_sources) == 1:
        merged["extraction_method"] = unique_sources[0]
    else:
        merged["extraction_method"] = None

    return merged


def repair_json(raw: str) -> dict:
    # Step 1: strip markdown code fences
    text = re.sub(r"```(?:json)?", "", raw).strip()
    # Step 2: direct parse
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    # Step 3: extract first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            return result if isinstance(result, dict) else {}
        except Exception:
            pass
    # Step 4: json_repair library fallback
    try:
        from json_repair import repair_json as _repair
        result = json.loads(_repair(text))
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    return {}


CURRENT_YEAR = datetime.datetime.now().year


def validate_fields(raw: dict) -> dict:
    out = dict(raw)
    # year: must be int in [1800, current+1]
    try:
        y = int(out.get("year", 0))
        out["year"] = y if 1800 <= y <= CURRENT_YEAR + 1 else None
    except (TypeError, ValueError):
        out["year"] = None
    # language: must be exactly 2 lowercase letters
    lang = out.get("language", "")
    out["language"] = (
        lang.lower()
        if (isinstance(lang, str) and len(lang) == 2 and lang.isalpha())
        else None
    )
    # difficulty: whitelist
    out["difficulty"] = (
        out.get("difficulty")
        if out.get("difficulty") in ("Beginner", "Intermediate", "Advanced")
        else None
    )
    # tags: must be a list of strings, lowercase
    tags = out.get("tags", [])
    out["tags"] = (
        [str(t).lower().strip() for t in tags if t]
        if isinstance(tags, list) else []
    )
    return out
