"""CSV export that is safe to open in Excel/Sheets (CSV/formula injection)."""

from __future__ import annotations

import csv
import io

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def neutralise(value) -> str:
    s = "" if value is None else str(value)
    if s.startswith(_DANGEROUS):
        return "'" + s
    return s


COLUMNS = ["rank", "label", "match_score", "years_experience", "education_level",
           "matched_required", "missing_required", "matched_preferred", "top_skills", "suspicious_hidden_text"]


def results_to_csv(results: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(COLUMNS)
    for r in results:
        row = []
        for col in COLUMNS:
            v = r.get(col)
            if isinstance(v, list):
                v = "; ".join(map(str, v))
            row.append(neutralise(v))
        w.writerow(row)
    return "﻿" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly
