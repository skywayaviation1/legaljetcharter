#!/usr/bin/env python3
"""Sync FAA Part 135 certificate data into the static site.

Sources
-------
1. The workbook linked from
   https://www.faa.gov/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders
2. The Air Operator 14 CFR search embedded at
   https://www.faa.gov/av-info/air-operator-far-search
   (Tableau workbook AVInfo_AirOperators, Part 135, Download sheet).

The job checks both sources every run. It downloads and reparses a source
only when that source's link, date, or content hash changed. It commits
nothing itself; the caller commits. On any doubt (missing columns, empty
extract, row count collapsing versus the previous publish) it exits non-zero
and does not replace the published files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCES = DATA / "sources"
OPERATORS_PATH = DATA / "operators.json"
META_PATH = DATA / "meta.json"
PART_PATH = SOURCES / "part135.json"
AIR_PATH = SOURCES / "air-operator.json"

HOLDERS_PAGE = (
    "https://www.faa.gov/about/officeorg/headquartersoffices/avs/"
    "faa-certificated-aircraft-operators-legal-part-135-holders"
)
AIR_PAGE = "https://www.faa.gov/av-info/air-operator-far-search"
AIR_DOWNLOAD = "https://explore.dot.gov/t/FAA/views/AVInfo_AirOperators/Download.csv"
AIR_LETTERS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
STAFF_FIELDS = {
    "PIC Captains": "picCaptains",
    "Inspectors": "inspectors",
    "Designated Inspectors": "designatedInspectors",
    "NonCertificated Mechanics": "nonCertificatedMechanics",
    "Certificated Mechanics": "certificatedMechanics",
    "Repairmen": "repairmen",
    "Total Number of Employees": "totalEmployees",
}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Floors taken from the FAA's own published scale (about 1,800 holders and
# about 11,000 authorized aircraft). A publish below these, or a sharp drop
# from the previous publish, is treated as a truncated extract.
MIN_LIST_OPERATORS = 1200
MIN_LIST_AIRCRAFT = 8000
MIN_AIR_OPERATORS = 800
MAX_AIR_OPERATORS = 6000
DROP_RATIO = 0.75

PART_HEADERS = {
    "cfr": ("cfr", "far part", "far", "part"),
    "chdo": ("chdo", "fsdo", "district office", "faa district office", "office code"),
    "designator": ("dsgn", "designator", "designator code", "desig", "certificate designator"),
    "name": (
        "name",
        "certificate holder",
        "certificate holder name",
        "operator name",
        "air operator name",
        "holder name",
    ),
    "model": (
        "aircraft m/m/s",
        "aircraft mms",
        "m/m/s",
        "make model series",
        "aircraft model",
        "model",
        "aircraft type",
    ),
    "registration": (
        "registration no",
        "registration no.",
        "registration number",
        "registration",
        "n-number",
        "n number",
        "tail number",
    ),
    "serial": ("serial no", "serial no.", "serial number", "serial"),
}
REQUIRED_PART = ("name", "designator", "registration")

AIR_FIELDS = {
    "name": (
        "air operator name",
        "operator name",
        "certificate holder name",
        "certificate holder",
        "legal name",
        "name",
    ),
    "dba": ("dba", "authorized dba", "authorized dba name", "doing business as", "dba name"),
    "designator": ("designator code", "designator", "dsgn", "desig"),
    "certificateNumber": (
        "certificate number",
        "certificate no",
        "certificate no.",
        "air carrier certificate",
        "certificate",
    ),
    "issueDate": ("issue date", "certificate issue date", "original issue date"),
    "address": ("address", "combined address", "street", "business address", "address line"),
    "city": ("city", "operator city"),
    "state": ("state", "operator state", "st"),
    "postalCode": ("postal code", "zip", "zip code", "postal"),
    "cfr": ("cfr", "far", "far part", "14 cfr part", "part", "operator far"),
    "fsdo": ("fsdo", "chdo", "district office", "faa office"),
    "status": ("status", "certificate status", "operating status"),
    "aircraft": ("aircraft", "aircraft mms", "aircraft m/m/s", "make model series", "aircraft type"),
}

NAME_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "L.L.C", "LLP", "LP", "CO", "COMPANY",
    "LTD", "LIMITED", "CORP", "CORPORATION", "PLLC", "PC",
}


class SyncError(RuntimeError):
    """A problem that must stop the publish."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(raw)


def norm_header(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def norm_designator(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", clean_text(value).upper())


def norm_name(value) -> str:
    text = clean_text(value).upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    tokens = text.split()
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def norm_state(value) -> str:
    text = clean_text(value).upper()
    if re.fullmatch(r"[A-Z]{2}", text):
        return text
    return ""


def parse_city_state_zip(line: str):
    match = re.fullmatch(
        r"(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)",
        clean_text(line).upper(),
    )
    if not match:
        return None
    city = re.sub(r"\s+", " ", match.group("city")).strip(" .")
    return {"city": city, "state": match.group("state"), "postalCode": match.group("zip")}


def request(url: str, method: str = "GET", timeout: int = 90) -> tuple[bytes, dict]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = b"" if method == "HEAD" else resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            final = resp.geturl()
    except urllib.error.HTTPError as exc:
        raise SyncError(f"FAA request failed {method} {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"FAA request failed {method} {url}: {exc.reason}") from exc
    return body, {**headers, "final-url": final}


def find_workbook_link(html: str, page_url: str) -> str:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    candidates = []
    for href in hrefs:
        low = href.lower().split("?")[0]
        if low.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
            candidates.append(href)
    if not candidates:
        raise SyncError(
            "The FAA Part 135 holders page has no spreadsheet or CSV link. "
            "Refusing to publish rather than reuse an old file silently."
        )
    # Prefer a link whose text or path mentions the holders page / part 135.
    def rank(href: str) -> tuple:
        low = href.lower()
        return (
            0 if "part-135" in low or "part_135" in low or "135" in low else 1,
            0 if low.endswith((".xlsx", ".xlsm")) else 1,
            len(href),
        )

    href = sorted(candidates, key=rank)[0]
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.faa.gov" + href
    if href.startswith("http"):
        return href
    base = page_url.rsplit("/", 1)[0]
    return base + "/" + href


def file_date_from_header(last_modified: str) -> str:
    if not last_modified:
        return ""
    try:
        return parsedate_to_datetime(last_modified).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def map_headers(headers, aliases, required):
    normalized = [norm_header(h) for h in headers]
    mapping = {}
    used = set()
    for index, header in enumerate(normalized):
        if not header:
            continue
        for field, names in aliases.items():
            if header in names or any(header == name or header.endswith(" " + name) for name in names):
                if field in mapping:
                    raise SyncError(f'Column "{headers[index]}" and "{headers[mapping[field]]}" both look like {field}.')
                mapping[field] = index
                used.add(index)
                break
    missing = [field for field in required if field not in mapping]
    if missing:
        raise SyncError(
            "Required column(s) "
            + ", ".join(missing)
            + " not found. Headers were: "
            + ", ".join(str(h) for h in headers if h)
        )
    extras = [i for i in range(len(headers)) if i not in used and normalized[i]]
    return mapping, extras


def parse_part135_workbook(blob: bytes) -> dict:
    try:
        import openpyxl
    except ImportError as exc:
        raise SyncError("openpyxl is required to read the FAA workbook. pip install -r scripts/requirements.txt") from exc
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
        handle.write(blob)
        temp_name = handle.name
    try:
        try:
            workbook = openpyxl.load_workbook(temp_name, read_only=True, data_only=True)
        except Exception as exc:
            raise SyncError(f"The FAA file is not a readable workbook: {exc}") from exc
        sheet = workbook[workbook.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows)
        except StopIteration as exc:
            raise SyncError("The FAA workbook is empty.") from exc
        headers = [clean_text(cell) for cell in header_row]
        mapping, extras = map_headers(headers, PART_HEADERS, REQUIRED_PART)
        grouped = {}
        aircraft_count = 0
        for raw in rows:
            if raw is None or all(cell is None or clean_text(cell) == "" for cell in raw):
                continue
            values = list(raw) + [None] * max(0, len(headers) - len(raw))
            name = clean_text(values[mapping["name"]])
            designator = norm_designator(values[mapping["designator"]])
            registration = clean_text(values[mapping["registration"]]).upper().replace(" ", "")
            model = clean_text(values[mapping["model"]]) if "model" in mapping else ""
            serial = clean_text(values[mapping["serial"]]) if "serial" in mapping else ""
            if not name and not designator and not registration and not model and not serial:
                continue
            if not name or not designator:
                raise SyncError(
                    "A Part 135 row is missing the operator name or designator. "
                    f"Row starts: {values[:7]!r}"
                )
            extra = {}
            for index in extras:
                text = clean_text(values[index]) if index < len(values) else ""
                if text:
                    extra[headers[index]] = text
            aircraft = None
            if registration or model or serial:
                aircraft_count += 1
                aircraft = {
                    "registration": registration,
                    "model": model,
                    "serial": serial,
                    "cfr": clean_text(values[mapping["cfr"]]) if "cfr" in mapping else "",
                }
                if extra:
                    aircraft["extra"] = extra
            bucket = grouped.get(designator)
            if bucket is None:
                bucket = {
                    "designator": designator,
                    "names": [],
                    "cfrs": [],
                    "chdos": [],
                    "aircraft": [],
                }
                grouped[designator] = bucket
            if name not in bucket["names"]:
                bucket["names"].append(name)
            cfr = clean_text(values[mapping["cfr"]]) if "cfr" in mapping else ""
            if cfr and cfr not in bucket["cfrs"]:
                bucket["cfrs"].append(cfr)
            if "chdo" in mapping:
                chdo = clean_text(values[mapping["chdo"]]).upper()
                if chdo and chdo not in bucket["chdos"]:
                    bucket["chdos"].append(chdo)
            if aircraft:
                bucket["aircraft"].append(aircraft)
        workbook.close()
    finally:
        os.unlink(temp_name)
    if not grouped:
        raise SyncError("The Part 135 workbook parsed to zero operators.")
    operators = []
    for designator in sorted(grouped):
        bucket = grouped[designator]
        names = bucket["names"]
        operators.append({
            "designator": designator,
            "name": names[0],
            "nameVariants": names[1:],
            "cfrs": bucket["cfrs"],
            "chdos": bucket["chdos"],
            "aircraft": bucket["aircraft"],
        })
    return {
        "columns": [h for h in headers if h],
        "unmappedColumns": [headers[i] for i in extras],
        "operatorCount": len(operators),
        "aircraftCount": aircraft_count,
        "operators": operators,
    }


def cell(row, mapping, field):
    if field not in mapping:
        return ""
    index = mapping[field]
    if index >= len(row):
        return ""
    return clean_text(row[index])


def table_records(table: dict) -> list[dict]:
    columns = table.get("columns") or []
    if not columns:
        return []
    # Map what we can. Unknown columns stay in extra. A table with no recognized
    # field at all is ignored only when it is empty; otherwise the caller checks.
    try:
        mapping, extras = map_headers(columns, AIR_FIELDS, required=())
    except SyncError:
        raise
    records = []
    for row in table.get("rows") or []:
        item = {field: cell(row, mapping, field) for field in mapping}
        extra = {}
        for index in extras:
            if index < len(row) and clean_text(row[index]):
                extra[columns[index]] = clean_text(row[index])
        if extra:
            item["extra"] = extra
        if any(item.get(field) for field in mapping):
            records.append(item)
    return records


def choose_table(tables: dict, needles: tuple[str, ...]) -> list[dict]:
    for name, table in tables.items():
        low = name.lower()
        if any(needle and needle in low for needle in needles):
            return table_records(table)
    return []


def part_tokens(value: str) -> set[str]:
    found = set(re.findall(r"135|121|125|129|133|137|91", value or ""))
    return found


def interpret_air_payload(payload: dict) -> list[dict]:
    if payload.get("unchanged"):
        raise SyncError("Internal: unchanged air payload passed to the parser.")
    tables = payload.get("tables") or {}
    if not tables:
        raise SyncError("Air Operator export contained no tables.")
    operators = choose_table(tables, ("air operator", "operator"))
    if not operators:
        best = []
        for table in tables.values():
            records = table_records(table)
            if any(record.get("name") for record in records) and len(records) > len(best):
                best = records
        operators = best
    dbas = choose_table(tables, ("dba",))
    aircraft = choose_table(tables, ("aircraft",))
    if not operators:
        described = {name: (table.get("columns") or [])[:15] for name, table in tables.items()}
        raise SyncError(f"Air Operator export had no operator table. Sheets and columns: {described}")

    by_key = {}
    order = []

    def key_for(record):
        designator = norm_designator(record.get("designator"))
        certificate = clean_text(record.get("certificateNumber")).upper()
        name = norm_name(record.get("name"))
        if designator:
            return ("dsgn", designator)
        if certificate:
            return ("cert", certificate)
        if name:
            return ("name", name)
        return None

    for record in operators:
        key = key_for(record)
        if key is None:
            continue
        current = by_key.get(key)
        if current is None:
            current = {
                "name": record.get("name") or "",
                "designator": norm_designator(record.get("designator")),
                "certificateNumber": clean_text(record.get("certificateNumber")),
                "issueDate": record.get("issueDate") or "",
                "address": record.get("address") or "",
                "city": record.get("city") or "",
                "state": norm_state(record.get("state")),
                "postalCode": clean_text(record.get("postalCode")),
                "fsdo": clean_text(record.get("fsdo")).upper(),
                "status": record.get("status") or "",
                "cfrs": sorted(part_tokens(record.get("cfr") or "")),
                "dbas": [],
                "aircraft": [],
                "extra": {},
            }
            if record.get("dba"):
                current["dbas"].append(record["dba"])
            if record.get("aircraft"):
                current["aircraft"].append({"model": record["aircraft"], "registration": "", "source": "air-operator"})
            current["extra"] = dict(record.get("extra") or {})
            if not current["city"] or not current["state"]:
                parsed = parse_city_state_zip(record.get("address") or "")
                # Address often holds the street only. Also try the last extra line.
                if parsed is None:
                    parsed = parse_city_state_zip(record.get("city") or "")
                if parsed:
                    current["city"] = current["city"] or parsed["city"]
                    current["state"] = current["state"] or parsed["state"]
                    current["postalCode"] = current["postalCode"] or parsed["postalCode"]
            by_key[key] = current
            order.append(key)
        else:
            for field in ("name", "certificateNumber", "issueDate", "address", "city", "status", "fsdo"):
                if not current.get(field) and record.get(field):
                    current[field] = record[field]
            if record.get("dba") and record["dba"] not in current["dbas"]:
                current["dbas"].append(record["dba"])
            current["cfrs"] = sorted(set(current["cfrs"]) | part_tokens(record.get("cfr") or ""))
            current["extra"].update(record.get("extra") or {})

    def attach(records, kind):
        for record in records:
            designator = norm_designator(record.get("designator"))
            certificate = clean_text(record.get("certificateNumber")).upper()
            target = None
            if designator and ("dsgn", designator) in by_key:
                target = by_key[("dsgn", designator)]
            elif certificate and ("cert", certificate) in by_key:
                target = by_key[("cert", certificate)]
            if target is None:
                continue
            if kind == "dba":
                name = record.get("dba") or record.get("name")
                if name and name not in target["dbas"]:
                    target["dbas"].append(name)
            else:
                model = record.get("aircraft") or record.get("model") or record.get("name")
                registration = clean_text(record.get("registration")).upper().replace(" ", "")
                item = {"model": model or "", "registration": registration, "source": "air-operator"}
                if item["model"] or item["registration"]:
                    if item not in target["aircraft"]:
                        target["aircraft"].append(item)

    attach(dbas, "dba")
    attach(aircraft, "aircraft")

    published = [by_key[key] for key in order]
    # Keep Part 135. If the export has no part column at all, the parameter
    # filter is the only selector and every row is kept, but a huge extract
    # is rejected by the sanity check.
    with_part = [op for op in published if op["cfrs"]]
    if with_part:
        published = [op for op in published if "135" in op["cfrs"]]
    if not published:
        raise SyncError("Air Operator export contained no Part 135 operators after filtering.")
    return published


def sourced(value, source):
    if value is None or value == "" or value == [] or value == {}:
        return None
    return {"value": value, "source": source}


def merge_records(part_ops: list[dict], air_ops: list[dict]) -> dict:
    air_by_designator = defaultdict(list)
    for op in air_ops:
        if op.get("designator"):
            air_by_designator[op["designator"]].append(op)
    ambiguous_designators = {key for key, rows in air_by_designator.items() if len(rows) > 1}

    used_air = set()
    merged = []
    conflicts = []
    ambiguous = []

    def add_conflict(field, part_value, air_value, designator):
        conflicts.append({
            "field": field,
            "designator": designator,
            "part135List": part_value,
            "airOperator": air_value,
        })

    for part in part_ops:
        designator = part["designator"]
        air = None
        match = "unmatched"
        if designator and designator not in ambiguous_designators and len(air_by_designator.get(designator, [])) == 1:
            air = air_by_designator[designator][0]
            match = "designator"
            used_air.add(id(air))
        elif designator in ambiguous_designators:
            ambiguous.append({
                "reason": "duplicate-designator-in-air-operator-search",
                "designator": designator,
                "part135Name": part["name"],
            })

        record = combine(part, air, match, add_conflict)
        merged.append(record)

    remaining = [op for op in air_ops if id(op) not in used_air]
    part_only = [rec for rec in merged if rec["presence"] == "part135-list" and rec["designator"]["value"] not in ambiguous_designators]
    by_name = defaultdict(list)
    for rec in part_only:
        by_name[norm_name(rec["legalName"]["value"])].append(rec)
    air_by_name = defaultdict(list)
    for op in remaining:
        if op.get("designator") and op["designator"] in {rec["designator"]["value"] for rec in merged if rec["presence"] != "air-operator"}:
            continue
        air_by_name[norm_name(op.get("name"))].append(op)

    newly_used = set()
    for name, airs in air_by_name.items():
        if not name:
            continue
        parts = by_name.get(name, [])
        if len(airs) == 1 and len(parts) == 1:
            air = airs[0]
            part_rec = parts[0]
            air_d = air.get("designator") or ""
            part_d = part_rec["designator"]["value"]
            if air_d and part_d and air_d != part_d:
                continue
            if air.get("state") and False:
                pass
            # Part 135 list has no city. Name match is accepted only when unique.
            attach_air_onto(part_rec, air, "unique-legal-name")
            newly_used.add(id(air))
            if norm_name(part_rec["legalName"]["value"]) != norm_name(air.get("name")):
                pass
        elif len(airs) >= 1 and len(parts) > 1:
            ambiguous.append({
                "reason": "legal-name-matches-multiple-part135-certificates",
                "name": airs[0].get("name") or name,
                "designators": [rec["designator"]["value"] for rec in parts],
            })
        elif len(airs) > 1 and len(parts) == 1:
            ambiguous.append({
                "reason": "legal-name-matches-multiple-air-operator-records",
                "name": parts[0]["legalName"]["value"],
                "designator": parts[0]["designator"]["value"],
            })

    for op in remaining:
        if id(op) in newly_used:
            continue
        duplicate = bool(op.get("designator") and any(
            rec["designator"] and rec["designator"]["value"] == op["designator"] for rec in merged
        ))
        if duplicate:
            # Keep the extra row visible instead of dropping it, with its own id.
            ambiguous.append({
                "reason": "duplicate-designator-in-air-operator-search",
                "designator": op["designator"],
                "airOperatorName": op.get("name"),
            })
            op = dict(op)
            op["_duplicate"] = True
        merged.append(air_only(op))

    merged.sort(key=lambda rec: ((rec["legalName"]["value"] or "").upper(), (rec["designator"]["value"] if rec["designator"] else "")))
    in_both = sum(1 for rec in merged if rec["presence"] == "both")
    only_list = sum(1 for rec in merged if rec["presence"] == "part135-list")
    only_air = sum(1 for rec in merged if rec["presence"] == "air-operator")
    record_conflicts = [item for rec in merged for item in rec.get("conflicts") or []]
    field_counts = defaultdict(int)
    for item in record_conflicts:
        field_counts[item["field"]] += 1
    return {
        "operators": merged,
        "reconciliation": {
            "inBoth": in_both,
            "onlyPart135List": only_list,
            "onlyAirOperator": only_air,
            "conflictCount": len(record_conflicts),
            "conflictFields": dict(sorted(field_counts.items())),
            "conflicts": record_conflicts,
            "ambiguous": ambiguous,
        },
    }


def combine(part, air, match, add_conflict) -> dict:
    designator = part["designator"]
    conflicts = []
    if air and norm_name(part["name"]) != norm_name(air.get("name")):
        item = {
            "field": "legalName",
            "designator": designator,
            "part135List": part["name"],
            "airOperator": air.get("name") or "",
        }
        conflicts.append(item)
        add_conflict("legalName", part["name"], air.get("name") or "", designator)

    record = {
        "id": f"dsgn:{designator}",
        "presence": "both" if air else "part135-list",
        "match": match if air else "part135-list-only",
        "legalName": sourced(part["name"], "part135-list"),
        "airOperatorName": sourced(air.get("name") if air else "", "air-operator"),
        "dbas": sourced(air.get("dbas") if air else [], "air-operator"),
        "designator": sourced(designator, "part135-list" if not air or not air.get("designator") else "both"),
        "certificateNumber": sourced(air.get("certificateNumber") if air else "", "air-operator"),
        "issueDate": sourced(air.get("issueDate") if air else "", "air-operator"),
        "address": sourced(air.get("address") if air else "", "air-operator"),
        "city": sourced(air.get("city") if air else "", "air-operator"),
        "state": sourced(air.get("state") if air else "", "air-operator"),
        "postalCode": sourced(air.get("postalCode") if air else "", "air-operator"),
        "fsdo": sourced(part.get("chdos") or [], "part135-list"),
        "status": sourced(air.get("status") if air else "", "air-operator"),
        "cfrOnList": sourced(part.get("cfrs") or [], "part135-list"),
        "cfrOnSearch": sourced(air.get("cfrs") if air else [], "air-operator"),
        "aircraft": [
            {
                "registration": craft["registration"],
                "model": craft.get("model") or "",
                "serial": craft.get("serial") or "",
                "cfr": craft.get("cfr") or "",
                "source": "part135-list",
                **({"extra": craft["extra"]} if craft.get("extra") else {}),
            }
            for craft in part["aircraft"]
        ],
        "searchAircraft": (air.get("aircraft") if air else []) or [],
        "extra": air.get("extra") if air else {},
        "conflicts": conflicts,
        "listNameVariants": part.get("nameVariants") or [],
    }
    copy_air_details(record, air)
    if air and part.get("chdos") and air.get("fsdo") and air["fsdo"] not in part["chdos"]:
        record["conflicts"].append({
            "field": "fsdo",
            "designator": designator,
            "part135List": ", ".join(part["chdos"]),
            "airOperator": air["fsdo"],
        })
        add_conflict("fsdo", ", ".join(part["chdos"]), air["fsdo"], designator)
    list_parts = set()
    for cfr in part.get("cfrs") or []:
        list_parts |= part_tokens(cfr)
    search_parts = set(air.get("cfrs") or []) if air else set()
    if air and list_parts and search_parts and list_parts.isdisjoint(search_parts):
        record["conflicts"].append({
            "field": "cfr",
            "designator": designator,
            "part135List": ", ".join(part.get("cfrs") or []),
            "airOperator": ", ".join(sorted(search_parts)),
        })
        add_conflict("cfr", ", ".join(part.get("cfrs") or []), ", ".join(sorted(search_parts)), designator)
    return record


def copy_air_details(record, air):
    if not air:
        return
    for field in ("ceo", "chiefPilot", "directorOfMaintenance", "directorOfOperations", "phone", "country"):
        record[field] = sourced(air.get(field) or "", "air-operator")
    record["staff"] = sourced(air.get("staff") or {}, "air-operator")


def attach_air_onto(record, air, method):
    record["presence"] = "both"
    record["match"] = method
    record["airOperatorName"] = sourced(air.get("name") or "", "air-operator")
    record["dbas"] = sourced(air.get("dbas") or [], "air-operator")
    record["certificateNumber"] = sourced(air.get("certificateNumber") or "", "air-operator")
    record["issueDate"] = sourced(air.get("issueDate") or "", "air-operator")
    record["address"] = sourced(air.get("address") or "", "air-operator")
    record["city"] = sourced(air.get("city") or "", "air-operator")
    record["state"] = sourced(air.get("state") or "", "air-operator")
    record["postalCode"] = sourced(air.get("postalCode") or "", "air-operator")
    record["status"] = sourced(air.get("status") or "", "air-operator")
    record["cfrOnSearch"] = sourced(air.get("cfrs") or [], "air-operator")
    record["searchAircraft"] = air.get("aircraft") or []
    record["extra"] = air.get("extra") or {}
    copy_air_details(record, air)
    if norm_name(record["legalName"]["value"]) != norm_name(air.get("name") or ""):
        record["conflicts"].append({
            "field": "legalName",
            "designator": record["designator"]["value"],
            "part135List": record["legalName"]["value"],
            "airOperator": air.get("name") or "",
        })
    if air.get("designator") and record["designator"]["value"] == air["designator"]:
        record["designator"] = sourced(record["designator"]["value"], "both")


def air_only(op) -> dict:
    designator = op.get("designator") or ""
    identity = f"dsgn:{designator}" if designator else "name:" + norm_name(op.get("name"))
    if op.get("_duplicate"):
        identity += ":duplicate"
    record = {
        "id": identity,
        "presence": "air-operator",
        "match": "air-operator-only",
        "legalName": sourced(op.get("name") or "", "air-operator"),
        "airOperatorName": sourced(op.get("name") or "", "air-operator"),
        "dbas": sourced(op.get("dbas") or [], "air-operator"),
        "designator": sourced(designator, "air-operator"),
        "certificateNumber": sourced(op.get("certificateNumber") or "", "air-operator"),
        "issueDate": sourced(op.get("issueDate") or "", "air-operator"),
        "address": sourced(op.get("address") or "", "air-operator"),
        "city": sourced(op.get("city") or "", "air-operator"),
        "state": sourced(op.get("state") or "", "air-operator"),
        "postalCode": sourced(op.get("postalCode") or "", "air-operator"),
        "fsdo": sourced([op["fsdo"]] if op.get("fsdo") else [], "air-operator"),
        "status": sourced(op.get("status") or "", "air-operator"),
        "cfrOnList": None,
        "cfrOnSearch": sourced(op.get("cfrs") or [], "air-operator"),
        "aircraft": [],
        "searchAircraft": op.get("aircraft") or [],
        "extra": op.get("extra") or {},
        "conflicts": [],
    }
    copy_air_details(record, op)
    return record


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if not text.endswith("\n"):
        text += "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text)
    temp.replace(path)


def sanity_check(part: dict, air_ops: list[dict], merged: dict, previous: dict, enforce_floors: bool = True):
    problems = []
    if enforce_floors and part["operatorCount"] < MIN_LIST_OPERATORS:
        problems.append(f"Part 135 operator count {part['operatorCount']} is below {MIN_LIST_OPERATORS}.")
    if enforce_floors and part["aircraftCount"] < MIN_LIST_AIRCRAFT:
        problems.append(f"Part 135 aircraft count {part['aircraftCount']} is below {MIN_LIST_AIRCRAFT}.")
    if enforce_floors and len(air_ops) < MIN_AIR_OPERATORS:
        problems.append(f"Air Operator Part 135 count {len(air_ops)} is below {MIN_AIR_OPERATORS}.")
    if enforce_floors and len(air_ops) > MAX_AIR_OPERATORS:
        problems.append(
            f"Air Operator Part 135 count {len(air_ops)} is above {MAX_AIR_OPERATORS}. "
            "The part filter may have been ignored."
        )
    if not merged["operators"]:
        problems.append("Merged dataset is empty.")
    prev = previous or {}
    prev_part_ops = prev.get("part135", {}).get("operatorCount")
    prev_aircraft = prev.get("part135", {}).get("aircraftCount")
    prev_air = prev.get("airOperator", {}).get("operatorCount")
    prev_merged = prev.get("operatorCount")
    if prev_part_ops and part["operatorCount"] < prev_part_ops * DROP_RATIO:
        problems.append(
            f"Part 135 operators fell from {prev_part_ops} to {part['operatorCount']}."
        )
    if prev_aircraft and part["aircraftCount"] < prev_aircraft * DROP_RATIO:
        problems.append(
            f"Part 135 aircraft fell from {prev_aircraft} to {part['aircraftCount']}."
        )
    if prev_air and len(air_ops) < prev_air * DROP_RATIO:
        problems.append(f"Air Operator rows fell from {prev_air} to {len(air_ops)}.")
    if prev_merged and len(merged["operators"]) < prev_merged * DROP_RATIO:
        problems.append(
            f"Published operators fell from {prev_merged} to {len(merged['operators'])}."
        )
    if problems:
        raise SyncError("Sanity check failed; nothing was published.\n- " + "\n- ".join(problems))


def fingerprint_of(headers: dict, url: str) -> dict:
    return {
        "url": url,
        "lastModified": headers.get("last-modified", ""),
        "contentLength": headers.get("content-length", ""),
        "etag": headers.get("etag", ""),
    }


def load_part_source(meta: dict, force: bool) -> tuple[dict, bool]:
    html, _ = request(HOLDERS_PAGE)
    page = html.decode("utf-8", "replace")
    if "Access Denied" in page and "part 135" not in page.lower():
        raise SyncError("The FAA Part 135 holders page returned Access Denied.")
    link = find_workbook_link(page, HOLDERS_PAGE)
    _body, headers = request(link, method="HEAD")
    fingerprint = fingerprint_of(headers, link)
    file_date = file_date_from_header(fingerprint["lastModified"])
    if not file_date:
        raise SyncError("The FAA workbook response has no Last-Modified date, so the file date cannot be shown.")
    stored = (meta.get("part135") or {}).get("fingerprint") or {}
    cached = load_json(PART_PATH, None) if PART_PATH.exists() else None
    same_pointer = stored == fingerprint and cached and cached.get("contentHash")
    if same_pointer and not force:
        cached["fileDate"] = file_date
        return cached, False
    blob, get_headers = request(link)
    digest = sha256_bytes(blob)
    get_fp = fingerprint_of(get_headers, link)
    # HEAD and GET should agree; trust the GET headers for the stored fingerprint.
    fingerprint = get_fp or fingerprint
    file_date = file_date_from_header(fingerprint["lastModified"]) or file_date
    if cached and cached.get("contentHash") == digest and not force:
        cached["fingerprint"] = fingerprint
        cached["fileDate"] = file_date
        cached["byteLength"] = len(blob)
        return cached, False
    parsed = parse_part135_workbook(blob)
    parsed.update({
        "sourcePage": HOLDERS_PAGE,
        "fileUrl": link,
        "fileDate": file_date,
        "lastModified": fingerprint["lastModified"],
        "fingerprint": fingerprint,
        "contentHash": digest,
        "byteLength": len(blob),
    })
    return parsed, True


def iso_date(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def parse_air_operator_csv(text: str, slice_name: str) -> list[dict]:
    """Pivot the Download-sheet CSV (one row per staffing measure) into operators."""
    sample = text.lstrip("\ufeff")[:400]
    if "Access Denied" in sample or "<html" in sample.lower():
        raise SyncError(f"Air Operator download for {slice_name} was blocked. Nothing was published.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    rows = []
    for raw in reader:
        row = {(key or "").strip(): clean_text(value) for key, value in raw.items()}
        if any(row.values()):
            rows.append(row)
    if not rows or "Air_Operator_Name" not in rows[0] or "DSGN_CODE" not in rows[0]:
        raise SyncError(f"Air Operator download for {slice_name} did not have the expected columns.")
    if len(rows) % len(STAFF_FIELDS) != 0:
        raise SyncError(
            f"Air Operator download for {slice_name} has {len(rows)} rows, "
            f"not a multiple of {len(STAFF_FIELDS)}. Refusing a truncated file."
        )
    grouped = {}
    order = []
    for row in rows:
        part = row.get("14 CFR Part", "")
        tokens = part_tokens(part)
        if part and "135" not in tokens:
            raise SyncError(f"Air Operator slice {slice_name} included part {part}, which is not Part 135.")
        designator = norm_designator(row.get("DSGN_CODE"))
        name = row.get("Air_Operator_Name", "")
        key = ("dsgn", designator) if designator else ("name", norm_name(name))
        current = grouped.get(key)
        if current is None:
            address = ", ".join(bit for bit in (row.get("Addr1"), row.get("Addr2"), row.get("Addr3")) if bit)
            current = {
                "name": name,
                "designator": designator,
                "certificateNumber": row.get("Certificate_Number", ""),
                "issueDate": iso_date(row.get("Cert_Issue_Date", "")),
                "address": address,
                "city": row.get("City", ""),
                "state": norm_state(row.get("St", "")),
                "postalCode": row.get("Zip/Postal Code", ""),
                "country": row.get("Country", ""),
                "phone": row.get("Phone", ""),
                "ceo": row.get("CEO Name", ""),
                "chiefPilot": row.get("Chief Pilot", ""),
                "directorOfMaintenance": row.get("Director of Maintenance", ""),
                "directorOfOperations": row.get("Director of Operations", ""),
                "fsdo": "",
                "status": "",
                "cfrs": sorted(tokens) if tokens else ["135"],
                "dbas": [],
                "aircraft": [],
                "staff": {},
                "extra": {},
            }
            grouped[key] = current
            order.append(key)
        measure = row.get("Measure Names", "")
        field = STAFF_FIELDS.get(measure)
        if field and row.get("Measure Values"):
            current["staff"][field] = row["Measure Values"]
    operators = [grouped[key] for key in order]
    if slice_name in AIR_LETTERS and not operators:
        raise SyncError(f"Air Operator slice {slice_name} was empty.")
    firsts = {(op["name"][:1].upper() if op["name"] else "") for op in operators}
    if slice_name in AIR_LETTERS and firsts != {slice_name}:
        raise SyncError(
            f"Air Operator slice {slice_name} returned names starting with {sorted(firsts)}. "
            "The alphabet filter was not applied."
        )
    return operators


def download_air_operators() -> list[dict]:
    print("Downloading Air Operator Part 135 slices…", file=sys.stderr)
    operators = []
    for index, letter in enumerate(AIR_LETTERS):
        if index:
            time.sleep(0.8)
        url = f"{AIR_DOWNLOAD}?Operator%20FAR%20Parameter=135&Alphabet%20Parameter={letter}"
        body, _headers = request(url)
        operators.extend(parse_air_operator_csv(body.decode("utf-8", "replace"), letter))
    return operators


def load_air_slices(texts_by_slice: dict[str, str]) -> list[dict]:
    operators = []
    for letter in AIR_LETTERS:
        if letter not in texts_by_slice:
            raise SyncError(f"Air Operator slice {letter} is missing.")
        operators.extend(parse_air_operator_csv(texts_by_slice[letter], letter))
    return operators


def air_bundle(operators: list[dict]) -> dict:
    return {
        "sourcePage": AIR_PAGE,
        "downloadUrl": AIR_DOWNLOAD,
        "updatedLabel": "",
        "partFilter": "135",
        "extractedAt": now_iso(),
        "method": "download-csv-by-letter",
        "operatorCount": len(operators),
        "contentHash": sha256_json(operators),
        "operators": operators,
        "notes": (
            "Each letter is the FAA Download sheet filtered to Part 135. "
            "That sheet publishes certificate, designator, address, city, state, officers, and staffing counts. "
            "It does not include a DBA column. The DBA worksheet is not a separate public file."
        ),
    }


def load_air_source(meta: dict, force: bool, csv_dir: str = "") -> tuple[dict, bool]:
    cached = load_json(AIR_PATH, None) if AIR_PATH.exists() else None
    if csv_dir:
        folder = Path(csv_dir)
        texts = {letter: (folder / f"{letter}.csv").read_text() for letter in AIR_LETTERS}
        operators = load_air_slices(texts)
    else:
        operators = download_air_operators()
    saved = air_bundle(operators)
    # A directory replay should not look "changed" solely because extractedAt moved.
    if csv_dir and cached and cached.get("contentHash") == saved["contentHash"]:
        cached["extractedAt"] = saved["extractedAt"]
        return cached, False
    changed = not cached or cached.get("contentHash") != saved["contentHash"] or force
    if not changed:
        cached["extractedAt"] = saved["extractedAt"]
        return cached, False
    return saved, True


def public_operator(record: dict) -> dict:
    """Drop empty sourced fields so the site file stays smaller, but keep conflicts."""
    keep_always = {"id", "presence", "match", "legalName", "designator", "aircraft", "conflicts"}
    published = {}
    for key, value in record.items():
        if key in keep_always:
            published[key] = value
            continue
        if value in (None, {}, []):
            continue
        published[key] = value
    published["aircraftCount"] = len(record.get("aircraft") or [])
    return published


def build_meta(part: dict, air: dict, merged: dict, checked_at: str) -> dict:
    recon = merged["reconciliation"]
    return {
        "lastChecked": checked_at,
        "holdersPage": HOLDERS_PAGE,
        "airOperatorPage": AIR_PAGE,
        "operatorCount": len(merged["operators"]),
        "aircraftCount": part["aircraftCount"],
        "part135": {
            "sourcePage": HOLDERS_PAGE,
            "fileUrl": part["fileUrl"],
            "fileDate": part["fileDate"],
            "lastModified": part.get("lastModified", ""),
            "contentHash": part["contentHash"],
            "byteLength": part.get("byteLength"),
            "fingerprint": part["fingerprint"],
            "operatorCount": part["operatorCount"],
            "aircraftCount": part["aircraftCount"],
            "columns": part.get("columns") or [],
            "unmappedColumns": part.get("unmappedColumns") or [],
        },
        "airOperator": {
            "sourcePage": AIR_PAGE,
            "updatedLabel": air.get("updatedLabel"),
            "extractedAt": air.get("extractedAt"),
            "method": air.get("method"),
            "partFilter": air.get("partFilter"),
            "contentHash": air.get("contentHash"),
            "operatorCount": air.get("operatorCount"),
        },
        "reconciliation": {
            "inBoth": recon["inBoth"],
            "onlyPart135List": recon["onlyPart135List"],
            "onlyAirOperator": recon["onlyAirOperator"],
            "conflictCount": recon["conflictCount"],
            "conflictFields": recon["conflictFields"],
            "ambiguousCount": len(recon["ambiguous"]),
        },
    }


def publish(part: dict, air: dict, merged: dict, previous_meta: dict, checked_at: str, enforce_floors: bool = True) -> dict:
    sanity_check(part, air["operators"], merged, previous_meta, enforce_floors=enforce_floors)
    public = [public_operator(rec) for rec in merged["operators"]]
    new_hash = sha256_json(public)
    meta = build_meta(part, air, merged, checked_at)
    meta["datasetHash"] = new_hash
    data_changed = previous_meta.get("datasetHash") != new_hash
    # Source extracts are what the next no-change run reuses. Rewrite them
    # when their content hash changed, and always refresh meta.
    if not PART_PATH.exists() or load_json(PART_PATH, {}).get("contentHash") != part.get("contentHash"):
        write_json(PART_PATH, part)
    elif part.get("fileDate") != load_json(PART_PATH, {}).get("fileDate"):
        write_json(PART_PATH, part)
    if not AIR_PATH.exists() or load_json(AIR_PATH, {}).get("contentHash") != air.get("contentHash"):
        write_json(AIR_PATH, air)
    if data_changed or not OPERATORS_PATH.exists():
        write_json(OPERATORS_PATH, public)
    write_json(META_PATH, meta)
    summary_conflicts = meta["reconciliation"]
    summary = {
        "dataChanged": data_changed,
        "lastChecked": checked_at,
        "part135FileDate": part["fileDate"],
        "part135FileUrl": part["fileUrl"],
        "part135Operators": part["operatorCount"],
        "part135Aircraft": part["aircraftCount"],
        "airOperatorUpdated": air.get("updatedLabel"),
        "airOperatorCount": air.get("operatorCount"),
        "publishedOperators": len(public),
        "reconciliation": summary_conflicts,
    }
    (ROOT / ".sync-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sync FAA Part 135 sources")
    parser.add_argument("--force", action="store_true", help="Re-download and re-parse even if fingerprints match")
    parser.add_argument("--air-csv-dir", default="", help="Read Air Operator letter CSVs from this directory instead of downloading")
    args = parser.parse_args(argv)
    previous = load_json(META_PATH, {})
    checked_at = now_iso()
    try:
        part, part_changed = load_part_source(previous, args.force)
        print(
            f"Part 135 list {'changed' if part_changed else 'unchanged'} "
            f"({part['operatorCount']} operators, {part['aircraftCount']} aircraft, file date {part['fileDate']}).",
            file=sys.stderr,
        )
        air, air_changed = load_air_source(previous, args.force, args.air_csv_dir)
        print(
            f"Air Operator search {'changed' if air_changed else 'unchanged'} "
            f"({air['operatorCount']} Part 135 operators, label {air.get('updatedLabel')}).",
            file=sys.stderr,
        )
        merged = merge_records(part["operators"], air["operators"])
        publish(part, air, merged, previous, checked_at)
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
