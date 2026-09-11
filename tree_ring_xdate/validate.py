"""Parsing and validation of tree-ring width series.

Two payload formats are accepted:

JSON -- a sample object or ``{"samples": [...]}``::

    {"sample_id": "A01", "unit": "mm", "start_year": 1950,
     "rings": [{"width": 1.2, "missing": false}, {"width": 0, "missing": true}]}

Shorthand forms are supported too: ``"widths": [1.2, 0, ...]`` together with
``"missing": [2, 5]`` (1-based positions) or ``"missing_years": [1957]``.

CSV -- one header row plus one ring per line; multiple samples may share a
file, blank ``sample_id`` cells continue the previous sample::

    sample_id,unit,start_year,year,width,missing
    A01,mm,1950,,1.20,0

Every error is localised with the sample id and the original row (physical
CSV line / JSON item index), and the accepted samples keep their verbatim
payload so the raw input is never lost.

Error codes (``E_*`` are fatal for that sample, ``W_*`` are warnings):

    E_NO_SAMPLE_ID      row/sample without a sample id
    E_NO_UNIT           unit missing
    E_UNIT_CONFLICT     different units inside one sample
    E_BAD_WIDTH         width is not a number
    E_NONPOSITIVE_WIDTH width <= 0 without a missing-ring marker
    E_BAD_YEAR          year is not an integer
    E_DUPLICATE_YEAR    same calendar year twice in one sample
    E_START_CONFLICT    declared start year contradicts explicit years
    E_PARTIAL_YEARS     only some rows carry explicit years
    E_CONFLICTING_MARK  missing marker set on a positive width / zero width
                        without the marker
    E_BAD_MARK          marker value not understood
    E_EMPTY             no ring rows
    W_IMPLICIT_GAP      unlisted year(s) inside an explicitly dated range,
                        stored as width-0 missing rings
    W_START_INFERRED    start year taken from the first explicit year
"""

from __future__ import annotations

import csv
import io
import json

TRUE_MARKS = {"1", "true", "t", "yes", "y", "m", "x", "*",
              "missing", "是", "缺", "缺失", "有"}
FALSE_MARKS = {"0", "false", "f", "no", "n", "", "否", "无", "正常"}

ALIASES = {
    "sample_id": {"sample_id", "sample", "id", "样本编号", "样本", "编号"},
    "unit": {"unit", "units", "单位", "宽度单位", "计量单位"},
    "start_year": {"start_year", "known_start", "year_start", "begin_year",
                   "起始年份", "开始年份", "始年", "已知起始年"},
    "year": {"year", "年份", "年", "日历年"},
    "width": {"width", "widths", "ring_width", "rw", "宽度", "轮宽",
              "树轮宽度"},
    "missing": {"missing", "missing_ring", "marker", "mark", "flag",
                "缺失环", "缺轮", "标记", "是否缺失"},
}


class ValidationError(Exception):
    def __init__(self, code: str, message: str, *, sample_id=None,
                 line=None, row=None, field=None, raw=None):
        super().__init__(message)
        self.detail = {
            "code": code,
            "message": message,
            "sample_id": sample_id,
            "line": line,        # physical CSV line (header is line 1)
            "row": row,          # 1-based data row / JSON item index
            "field": field,
            "raw": raw,
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _err(code, message, **kw):
    return ValidationError(code, message, **kw).detail


def _parse_number(value, *, sample_id, line, row, field="width", raw=None):
    if value is None:
        raise ValidationError(
            "E_BAD_WIDTH", "width is empty",
            sample_id=sample_id, line=line, row=row, field=field, raw=raw)
    if isinstance(value, bool):
        raise ValidationError(
            "E_BAD_WIDTH", f"width is not a number: {value!r}",
            sample_id=sample_id, line=line, row=row, field=field, raw=raw)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        raise ValidationError(
            "E_BAD_WIDTH", f"width is not a number: {value!r}",
            sample_id=sample_id, line=line, row=row, field=field, raw=raw)


def _parse_year(value, *, sample_id, line, row, field="year", raw=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValidationError(
            "E_BAD_YEAR", f"year is not an integer: {value!r}",
            sample_id=sample_id, line=line, row=row, field=field, raw=raw)
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text.lstrip("-").isdigit():
                raise ValueError
            return int(text)
        return int(value)
    except (ValueError, TypeError):
        raise ValidationError(
            "E_BAD_YEAR", f"year is not an integer: {value!r}",
            sample_id=sample_id, line=line, row=row, field=field, raw=raw)


def _parse_mark(value, *, sample_id, line, row, raw=None):
    """Return (is_present, was_explicit)."""
    if value is None:
        return False, False
    text = str(value).strip().lower()
    if text in FALSE_MARKS:
        return False, True
    if text in TRUE_MARKS:
        return True, True
    raise ValidationError(
        "E_BAD_MARK", f"unrecognised missing-ring marker: {value!r}",
        sample_id=sample_id, line=line, row=row, field="missing", raw=raw)


# ---------------------------------------------------------------------------
# Shared finalisation: rows (one per listed measurement) -> stored rings
# ---------------------------------------------------------------------------

def finalize_sample(sample_id, unit, declared_start, rows, *,
                    payload_format, raw_payload):
    """Validate one sample and build the stored ring list.

    ``rows`` is a list of dicts with keys seq (1-based, contiguous),
    year (int|None), width (float|None), missing (bool), mark_explicit
    (bool), raw_line, raw.
    """
    errors = []
    warnings = []

    if not sample_id:
        errors.append(_err("E_NO_SAMPLE_ID", "sample id is missing",
                           line=rows[0]["raw_line"] if rows else None))
    if not unit:
        errors.append(_err("E_NO_UNIT", "width unit is missing",
                           sample_id=sample_id,
                           line=rows[0]["raw_line"] if rows else None))
    if not rows:
        errors.append(_err("E_EMPTY", "no ring rows", sample_id=sample_id))
    if errors:
        return None, errors, warnings

    # -- per-row width / marker consistency --------------------------------
    for r in rows:
        if r["missing"]:
            if r["width"] is None:
                r["width"] = 0.0
            elif r["width"] > 0:
                errors.append(_err(
                    "E_CONFLICTING_MARK",
                    f"row marked as a missing ring but width is {r['width']}",
                    sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                    field="width", raw=r["raw"]))
            elif r["width"] < 0:
                errors.append(_err(
                    "E_NONPOSITIVE_WIDTH",
                    f"non-positive width {r['width']}",
                    sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                    field="width", raw=r["raw"]))
        else:
            if r["width"] is None:
                errors.append(_err(
                    "E_BAD_WIDTH", "width is empty and ring not marked missing",
                    sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                    field="width", raw=r["raw"]))
            elif r["width"] == 0:
                errors.append(_err(
                    "E_CONFLICTING_MARK",
                    "width is 0 but the row is not marked as a missing ring",
                    sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                    field="missing", raw=r["raw"]))
            elif r["width"] < 0:
                errors.append(_err(
                    "E_NONPOSITIVE_WIDTH",
                    f"negative width {r['width']} without missing marker",
                    sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                    field="width", raw=r["raw"]))
    if errors:
        return None, errors, warnings

    # -- year placement ----------------------------------------------------
    explicit = {r["seq"]: r["year"] for r in rows if r["year"] is not None}
    start = declared_start

    if explicit:
        if len(explicit) < len(rows):
            # Some rows have years, others do not.
            if start is not None:
                # Dense placement: every explicit year must match start+seq-1.
                for seq, yr in explicit.items():
                    if yr != start + seq - 1:
                        errors.append(_err(
                            "E_PARTIAL_YEARS",
                            f"only some rows give years and row {seq} year "
                            f"{yr} does not follow start year {start}",
                            sample_id=sample_id,
                            line=rows[seq - 1]["raw_line"], row=seq,
                            field="year", raw=rows[seq - 1]["raw"]))
            else:
                errors.append(_err(
                    "E_PARTIAL_YEARS",
                    "year given on some rows but neither all rows nor a "
                    "start year",
                    sample_id=sample_id))
        else:
            years = [r["year"] for r in rows]
            seen = set()
            for r in rows:
                if r["year"] in seen:
                    errors.append(_err(
                        "E_DUPLICATE_YEAR",
                        f"duplicate year {r['year']}",
                        sample_id=sample_id, line=r["raw_line"], row=r["seq"],
                        field="year", raw=r["raw"]))
                seen.add(r["year"])
            first_year = years[0]
            if start is not None and first_year != start:
                errors.append(_err(
                    "E_START_CONFLICT",
                    f"declared start year {start} but first explicit year "
                    f"is {first_year}",
                    sample_id=sample_id, line=rows[0]["raw_line"],
                    row=rows[0]["seq"], field="start_year",
                    raw=rows[0]["raw"]))
            if not errors:
                if start is None:
                    start = min(years)
                    if start != first_year:
                        warnings.append({
                            "code": "W_START_INFERRED",
                            "message": f"start year inferred as {start}",
                            "sample_id": sample_id})
                dense_expected = [start + i for i in range(len(rows))]
                if years == dense_expected:
                    for r in rows:
                        r["year"] = start + r["seq"] - 1
                else:
                    # Sparse listing: unlisted calendar years become
                    # implicit missing rings.
                    lo, hi = min(years), max(years)
                    by_year = {r["year"]: r for r in rows}
                    implicit = sorted(y for y in range(lo, hi + 1)
                                      if y not in by_year)
                    expanded = []
                    for y in range(lo, hi + 1):
                        if y in by_year:
                            src = by_year[y]
                            expanded.append({
                                "seq": y - lo + 1, "year": y,
                                "width": src["width"],
                                "missing": src["missing"],
                                "raw_line": src["raw_line"],
                                "raw": src["raw"]})
                        else:
                            expanded.append({
                                "seq": y - lo + 1, "year": y,
                                "width": 0.0, "missing": True,
                                "raw_line": None, "raw": None})
                    rows = expanded
                    warnings.append({
                        "code": "W_IMPLICIT_GAP",
                        "message": ("unlisted years %s stored as missing "
                                    "rings (width 0)"
                                    % ",".join(map(str, implicit))),
                        "sample_id": sample_id,
                        "years": implicit})
    else:
        for r in rows:
            r["year"] = None if start is None else start + r["seq"] - 1

    if errors:
        return None, errors, warnings

    has_missing = any(r["missing"] for r in rows)
    record = {
        "sample_id": sample_id,
        "unit": unit,
        "known_start": start,
        "has_missing": has_missing,
        "warnings": warnings,
        "rings": [
            {"seq": r["seq"], "year": r["year"], "width": float(r["width"]),
             "missing": bool(r["missing"]), "raw_line": r["raw_line"]}
            for r in rows
        ],
        "raw_payload": raw_payload,
        "payload_format": payload_format,
    }
    return record, [], warnings


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _json_sample(obj, index, raw_text=None, ring_texts=None):
    if not isinstance(obj, dict):
        return None, [_err("E_BAD_JSON",
                           f"sample #{index} is not a JSON object",
                           row=index, raw=raw_text)]
    sample_id = obj.get("sample_id") or obj.get("sample") or obj.get("id")
    unit = obj.get("unit") or obj.get("units")
    declared_start = obj.get("start_year", obj.get("known_start",
                                                   obj.get("begin_year")))
    if declared_start is not None:
        try:
            declared_start = int(declared_start)
        except (ValueError, TypeError):
            return None, [_err("E_BAD_YEAR",
                               f"start_year not an integer: "
                               f"{declared_start!r}",
                               sample_id=sample_id, field="start_year",
                               row=index, raw=raw_text)]

    raw_rows = obj.get("rings")
    rows = []
    errors = []
    if isinstance(raw_rows, list):
        for i, item in enumerate(raw_rows, start=1):
            # verbatim source text of this ring (falls back to re-encoding)
            raw = ring_texts[i - 1] if ring_texts and i <= len(ring_texts) \
                else _safe_json(item)
            try:
                if isinstance(item, dict):
                    width_in = item.get("width", item.get("w"))
                    year_in = item.get("year", item.get("y"))
                    mark_in = item.get("missing", item.get("mark",
                                       item.get("missing_ring")))
                elif isinstance(item, (int, float)) and not isinstance(
                        item, bool):
                    width_in, year_in, mark_in = item, None, None
                elif item is None:
                    width_in, year_in, mark_in = None, None, True
                else:
                    raise ValidationError(
                        "E_BAD_WIDTH", f"ring entry not understood: {item!r}",
                        sample_id=sample_id, row=i, raw=raw)
                year = _parse_year(year_in, sample_id=sample_id,
                                   line=None, row=i, raw=raw)
                missing, _ = _parse_mark(mark_in, sample_id=sample_id,
                                         line=None, row=i, raw=raw)
                width = None
                if width_in is not None or not missing:
                    width = _parse_number(width_in, sample_id=sample_id,
                                          line=None, row=i, raw=raw)
                rows.append({"seq": i, "year": year, "width": width,
                             "missing": missing, "raw_line": i, "raw": raw})
            except ValidationError as e:
                errors.append(e.detail)
    elif "widths" in obj:
        try:
            widths = obj["widths"]
            if not isinstance(widths, list):
                raise ValidationError(
                    "E_BAD_WIDTH", "'widths' must be a list",
                    sample_id=sample_id, row=index,
                    raw=raw_text if raw_text is not None else _safe_json(obj))
            missing_pos = set()
            for v in obj.get("missing", []) or []:
                missing_pos.add(int(v))
            for i, w in enumerate(widths, start=1):
                missing = i in missing_pos or w is None
                width = None if w is None else _parse_number(
                    w, sample_id=sample_id, line=None, row=i)
                year = None
                my = None
                if declared_start is not None and "missing_years" in obj:
                    pass
                rows.append({"seq": i, "year": year, "width": width,
                             "missing": missing, "raw_line": i,
                             "raw": _safe_json(w)})
            if declared_start is not None:
                missing_years = set()
                for v in obj.get("missing_years", []) or []:
                    missing_years.add(int(v))
                for r in rows:
                    cy = declared_start + r["seq"] - 1
                    if cy in missing_years:
                        if r["width"] is not None and r["width"] > 0:
                            errors.append(_err(
                                "E_CONFLICTING_MARK",
                                f"year {cy} listed in missing_years but "
                                f"width is {r['width']}",
                                sample_id=sample_id, row=r["seq"],
                                field="missing_years"))
                        r["missing"] = True
                        r["width"] = 0.0
        except ValidationError as e:
            errors.append(e.detail)
        except (ValueError, TypeError) as e:
            errors.append(_err("E_BAD_WIDTH", str(e), sample_id=sample_id,
                               row=index,
                               raw=raw_text if raw_text is not None
                               else _safe_json(obj)))
    else:
        return None, [_err("E_EMPTY",
                           "sample has no 'rings' or 'widths' list",
                           sample_id=sample_id, row=index,
                           raw=raw_text if raw_text is not None
                           else _safe_json(obj))]

    if errors:
        return None, errors, []

    return finalize_sample(sample_id, unit, declared_start, rows,
                           payload_format="json",
                           raw_payload=raw_text if raw_text is not None
                           else _safe_json(obj))


def _safe_json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)


# ---------------------------------------------------------------------------
# Verbatim JSON slicing
# ---------------------------------------------------------------------------

def _skip_ws(text, i):
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _find_key_value(text, key):
    """Index of the value following ``"key":`` in a JSON object text."""
    needle = json.dumps(key)
    i = 0
    while True:
        j = text.find(needle, i)
        if j < 0:
            return -1
        k = _skip_ws(text, j + len(needle))
        if k < len(text) and text[k] == ":":
            return _skip_ws(text, k + 1)
        i = j + 1


def _array_element_spans(text, start):
    """``(start)`` points at '['; return list of (a, b) element spans."""
    assert text[start] == "["
    spans = []
    dec = json.JSONDecoder()
    i = _skip_ws(text, start + 1)
    while i < len(text) and text[i] != "]":
        obj, end = dec.raw_decode(text, i)
        spans.append((i, end))
        i = _skip_ws(text, end)
        if i < len(text) and text[i] == ",":
            i = _skip_ws(text, i + 1)
    return spans


def parse_json(content: str):
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return {"format": "json", "accepted": [], "errors": [{
            "code": "E_BAD_JSON",
            "message": f"invalid JSON: {e.msg}",
            "line": e.lineno, "row": None, "field": None,
            "raw": None}]}

    # Locate the sample objects in the *verbatim* request text.
    sample_texts, ring_text_lists = [], []
    stripped = content.lstrip()
    if isinstance(data, list):
        try:
            spans = _array_element_spans(stripped, 0)
            sample_texts = [stripped[a:b] for a, b in spans]
        except json.JSONDecodeError:
            sample_texts = []
    elif isinstance(data, dict):
        if "samples" in data:
            v = _find_key_value(stripped, "samples")
            if v >= 0 and stripped[v] == "[":
                try:
                    spans = _array_element_spans(stripped, v)
                    sample_texts = [stripped[a:b] for a, b in spans]
                except json.JSONDecodeError:
                    sample_texts = []
        else:
            sample_texts = [content]
    if not sample_texts:
        # malformed structure: fall back to re-encoded text
        sample_texts = [None] * len(
            data if isinstance(data, list)
            else data.get("samples", []) if isinstance(data, dict)
            else [data])

    if isinstance(data, dict) and "samples" in data:
        samples = data["samples"]
    elif isinstance(data, list):
        samples = data
    else:
        samples = [data]
    if not isinstance(samples, list):
        return {"format": "json", "accepted": [], "errors": [{
            "code": "E_BAD_JSON", "message": "'samples' must be a list",
            "line": None, "row": None, "field": "samples", "raw": None}]}

    accepted, errors = [], []
    for i, obj in enumerate(samples):
        raw_text = sample_texts[i] if i < len(sample_texts) else None
        # per-ring verbatim text inside this sample's "rings" array
        ring_texts = None
        if raw_text is not None:
            rv = _find_key_value(raw_text, "rings")
            if rv >= 0 and raw_text[rv] == "[":
                try:
                    ring_texts = [raw_text[a:b]
                                  for a, b in
                                  _array_element_spans(raw_text, rv)]
                except json.JSONDecodeError:
                    ring_texts = None
        record, errs, _warnings = _json_sample(
            obj, i + 1, raw_text=raw_text, ring_texts=ring_texts)
        if record is not None:
            accepted.append(record)
        errors.extend(errs)
    return {"format": "json", "accepted": accepted, "errors": errors}


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _map_header(header):
    """Map canonical field name -> column index."""
    mapping = {}
    for i, name in enumerate(header):
        key = name.strip().lower()
        for canonical, aliases in ALIASES.items():
            if key in {a.lower() for a in aliases} and canonical not in mapping:
                mapping[canonical] = i
    return mapping


def parse_csv(content: str):
    buf = io.StringIO(content)
    reader = csv.reader(buf)
    try:
        header = next(reader)
    except StopIteration:
        return {"format": "csv", "accepted": [], "errors": [{
            "code": "E_BAD_CSV", "message": "empty CSV (no header row)",
            "line": 1, "row": None, "field": None, "raw": None}]}

    mapping = _map_header(header)
    if "width" not in mapping:
        return {"format": "csv", "accepted": [], "errors": [{
            "code": "E_BAD_CSV",
            "message": "CSV header has no width column (recognised names: "
                       + ", ".join(sorted(ALIASES["width"])) + ")",
            "line": 1, "row": None, "field": "width",
            "raw": ",".join(header)}]}

    def cell(row, name):
        idx = mapping.get(name)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    # group state
    groups = []  # list of mutable dicts
    current = None
    accepted, errors = [], []
    # sample ids with at least one row-level/parse error: never accepted,
    # but finalization still runs to collect *all* structural errors.
    bad_samples = set()

    for row in reader:
        physical = reader.line_num
        if not any(c.strip() for c in row):
            continue  # blank separator line
        raw_line = ",".join(row)

        sid = (cell(row, "sample_id") or "").strip() or None
        unit = (cell(row, "unit") or "").strip() or None

        if sid is not None or current is None:
            match = None
            if sid is not None:
                for g in groups:
                    if g["sample_id"] == sid:
                        match = g
                        break
            if match is None:
                match = {"sample_id": sid, "unit": None,
                         "declared_start": None, "rows": [],
                         "raw_lines": [",".join(header)]}
                groups.append(match)
            current = match

        seq = len(current["rows"]) + 1

        def _record(e):
            errors.append(e.detail)
            if e.detail.get("sample_id"):
                bad_samples.add(e.detail["sample_id"])

        # start_year inheritance / conflict
        try:
            start = _parse_year(cell(row, "start_year"),
                                sample_id=current["sample_id"],
                                line=physical, row=seq,
                                field="start_year", raw=raw_line)
        except ValidationError as e:
            _record(e)
            start = None
            bad_samples.add(current["sample_id"])
        if start is not None:
            if current["declared_start"] is None:
                current["declared_start"] = start
            elif current["declared_start"] != start:
                _record(ValidationError(
                    "E_START_CONFLICT",
                    f"start year {start} differs from earlier "
                    f"{current['declared_start']}",
                    sample_id=current["sample_id"], line=physical,
                    row=seq, field="start_year", raw=raw_line))

        # unit inheritance / conflict
        if unit is not None:
            if current["unit"] is None:
                current["unit"] = unit
            elif current["unit"] != unit:
                _record(ValidationError(
                    "E_UNIT_CONFLICT",
                    f"unit {unit!r} differs from earlier unit "
                    f"{current['unit']!r}",
                    sample_id=current["sample_id"], line=physical,
                    row=seq, field="unit", raw=raw_line))

        try:
            year = _parse_year(cell(row, "year"),
                               sample_id=current["sample_id"],
                               line=physical, row=seq, raw=raw_line)
            missing, _explicit = _parse_mark(cell(row, "missing"),
                                             sample_id=current["sample_id"],
                                             line=physical, row=seq,
                                             raw=raw_line)
            width_raw = cell(row, "width")
            width = None
            if (width_raw is not None and str(width_raw).strip() != "") \
                    or not missing:
                width = _parse_number(width_raw,
                                      sample_id=current["sample_id"],
                                      line=physical, row=seq, raw=raw_line)
            current["rows"].append({
                "seq": seq, "year": year, "width": width,
                "missing": missing, "raw_line": physical, "raw": raw_line})
            current["raw_lines"].append(raw_line)
        except ValidationError as e:
            _record(e)
            bad_samples.add(current["sample_id"])
            # keep a placeholder so subsequent row numbers stay aligned
            current["rows"].append({
                "seq": seq, "year": None, "width": 1.0,
                "missing": False, "raw_line": physical, "raw": raw_line})

    for g in groups:
        record, errs, _w = finalize_sample(
            g["sample_id"], g["unit"], g["declared_start"], g["rows"],
            payload_format="csv",
            raw_payload="\n".join(g["raw_lines"]))
        if record is None:
            errors.extend(errs)
            if g["sample_id"]:
                bad_samples.add(g["sample_id"])
        elif g["sample_id"] not in bad_samples:
            accepted.append(record)
    return {"format": "csv", "accepted": accepted, "errors": errors}


def _parse_optional_int(value):
    """Strict integer parse used outside the row loop (None/blank -> None)."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not text.lstrip("-").isdigit():
        raise ValueError(f"not an integer: {value!r}")
    return int(text)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def parse_payload(content: str, content_type: str | None = None,
                  filename: str | None = None):
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    stripped = content.lstrip()
    looks_json = stripped[:1] in {"{", "["}
    if "json" in ct or (name.endswith(".json") and "csv" not in ct):
        return parse_json(content)
    if "csv" in ct or name.endswith(".csv"):
        return parse_csv(content)
    if looks_json:
        return parse_json(content)
    return parse_csv(content)
