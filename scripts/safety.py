"""Public safety records for Part 135 operators.

Sources, and only these:

- NTSB aviation accident database, avall.zip, from https://data.ntsb.gov/avdata/
  Rows whose aircraft far_part is 135 (accidents and incidents).
- FAA quarterly enforcement reports linked from
  https://www.faa.gov/about/office_org/headquarters_offices/agc/practice_areas/enforcement/reports
  Spreadsheet quarters only. Those reports list closed civil penalties and
  certificate suspensions or revocations against regulated aviation entities.

Nothing is inferred. A name is attached only when it matches exactly one
operator after the same legal-name normalization used for the FAA lists.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urljoin

from sync_faa import (
    SyncError,
    clean_text,
    norm_designator,
    norm_name,
    request,
)

NTSB_INDEX = "https://data.ntsb.gov/avdata/"
NTSB_PAGE = "https://data.ntsb.gov/avdata/"
ENFORCEMENT_PAGE = (
    "https://www.faa.gov/about/office_org/headquarters_offices/agc/"
    "practice_areas/enforcement/reports"
)
NTSB_REPORT = "https://my.ntsb.gov/report?ev_id={ev_id}"
MIN_PART135_ROWS = 700

INJURY = {
    "F": "fatal",
    "FATL": "fatal",
    "S": "serious",
    "SERS": "serious",
    "M": "minor",
    "MINR": "minor",
    "N": "none",
    "NONE": "none",
}
EVENT_TYPE = {"ACC": "Accident", "INC": "Incident"}


def parse_mdy(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = clean_text(value)
    formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    if " " in text:
        return parse_mdy(text.split(" ", 1)[0])
    return ""


def part135_records(aircraft_rows: list[dict], events_by_id: dict[str, dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in aircraft_rows:
        if clean_text(row.get("far_part")) != "135":
            continue
        ev_id = clean_text(row.get("ev_id"))
        if not ev_id:
            continue
        event = events_by_id.get(ev_id, {})
        record = grouped.get(ev_id)
        if record is None:
            ev_type = clean_text(event.get("ev_type") or row.get("ev_type")).upper()
            injury = clean_text(event.get("ev_highest_injury")).upper()
            ntsb_no = clean_text(row.get("ntsb_no") or event.get("ntsb_no"))
            record = {
                "evId": ev_id,
                "ntsbNumber": ntsb_no,
                "date": parse_mdy(event.get("ev_date")),
                "eventType": EVENT_TYPE.get(ev_type, ev_type),
                "eventTypeCode": ev_type,
                "city": clean_text(event.get("ev_city")),
                "state": clean_text(event.get("ev_state")),
                "highestInjury": INJURY.get(injury, injury),
                "url": NTSB_REPORT.format(ev_id=ev_id),
                "aircraft": [],
                "operatorNames": [],
                "operatorDbas": [],
                "designators": [],
            }
            grouped[ev_id] = record
        designator = norm_designator(row.get("oper_code"))
        name = clean_text(row.get("oper_name"))
        dba = clean_text(row.get("oper_dba"))
        if designator and designator not in record["designators"]:
            record["designators"].append(designator)
        if name and name not in record["operatorNames"]:
            record["operatorNames"].append(name)
        if dba and dba not in record["operatorDbas"]:
            record["operatorDbas"].append(dba)
        craft = {
            "registration": clean_text(row.get("regis_no")).upper(),
            "make": clean_text(row.get("acft_make")),
            "model": clean_text(row.get("acft_model")),
        }
        if craft not in record["aircraft"]:
            record["aircraft"].append(craft)
    records = list(grouped.values())
    records.sort(key=lambda item: item.get("date") or "", reverse=True)
    return records


def _operator_names(operator: dict) -> list[str]:
    names = []
    for field in ("legalName", "airOperatorName"):
        value = operator.get(field)
        text = value.get("value") if isinstance(value, dict) else value
        if text:
            names.append(clean_text(text))
    dbas = operator.get("dbas")
    raw = dbas.get("value") if isinstance(dbas, dict) else dbas
    if isinstance(raw, list):
        names.extend(clean_text(item) for item in raw if item)
    return names


def _designator(operator: dict) -> str:
    value = operator.get("designator")
    text = value.get("value") if isinstance(value, dict) else value
    return norm_designator(text)


def norm_name_keep_suffix(value: str) -> str:
    text = clean_text(value).upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_records(operators: list[dict], records: list[dict], name_keys, designator_key, normalizer=norm_name) -> dict:
    """Return {operator id: [{record, match}]} and a small stats dict.

    name_keys and designator_key are callables on a record.
    """
    by_designator: dict[str, list] = defaultdict(list)
    by_name: dict[str, list] = defaultdict(list)
    for operator in operators:
        designator = _designator(operator)
        if designator:
            by_designator[designator].append(operator)
        for name in _operator_names(operator):
            key = normalizer(name)
            if len(key) >= 6:
                by_name[key].append(operator)

    attached: dict[str, list] = defaultdict(list)
    certificate = 0
    name_only = 0
    unmatched = 0
    ambiguous = 0
    for record in records:
        chosen = None
        match = ""
        designators = [norm_designator(item) for item in designator_key(record)]
        designators = [item for item in designators if item]
        hits = []
        for designator in designators:
            hits.extend(by_designator.get(designator, []))
        unique = {id(op): op for op in hits}
        if len(unique) == 1:
            chosen = next(iter(unique.values()))
            match = "certificate match"
        elif len(unique) > 1:
            ambiguous += 1
            continue
        else:
            name_hits = []
            for name in name_keys(record):
                key = normalizer(name)
                if len(key) < 6:
                    continue
                name_hits.extend(by_name.get(key, []))
            unique_names = {id(op): op for op in name_hits}
            if len(unique_names) == 1:
                chosen = next(iter(unique_names.values()))
                match = "name match - verify"
            elif len(unique_names) > 1:
                ambiguous += 1
                continue
        if chosen is None:
            unmatched += 1
            continue
        if match == "certificate match":
            certificate += 1
        else:
            name_only += 1
        attached[chosen["id"]].append({"match": match, "record": record})
    return {
        "attached": attached,
        "certificateMatches": certificate,
        "nameMatches": name_only,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


def ntsb_name_keys(record):
    return list(record.get("operatorNames") or []) + list(record.get("operatorDbas") or [])


def ntsb_designators(record):
    return list(record.get("designators") or [])


def enforcement_links(html: str) -> list[dict]:
    found = []
    seen = set()
    for href, text in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
        label = re.sub(r"<[^>]+>", " ", text)
        label = re.sub(r"\s+", " ", label).strip()
        low = href.lower()
        if not (low.endswith((".xlsx", ".xls")) or "/media/" in low):
            continue
        url = urljoin(ENFORCEMENT_PAGE, href)
        if url in seen:
            continue
        seen.add(url)
        found.append({"label": label, "url": url})
    return found


def parse_enforcement_sheet(blob: bytes, source: dict) -> list[dict]:
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:
        raise SyncError(f"FAA enforcement file {source.get('url')} is not a readable spreadsheet: {exc}") from exc
    sheet = book[book.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))
    book.close()
    header_index = None
    for index, row in enumerate(rows[:12]):
        labels = [clean_text(cell).upper() for cell in row]
        if "NAME" in labels and "CASE NUMBER" in labels:
            header_index = index
            break
    if header_index is None:
        raise SyncError(f"FAA enforcement file {source.get('url')} has no NAME and CASE NUMBER columns.")
    headers = [clean_text(cell) for cell in rows[header_index]]
    index_of = {name.upper(): pos for pos, name in enumerate(headers) if name}

    def cell(row, *names):
        for name in names:
            pos = index_of.get(name.upper())
            if pos is None or pos >= len(row):
                continue
            return row[pos]
        return None

    parsed = []
    for row in rows[header_index + 1 :]:
        if row is None:
            continue
        name = clean_text(cell(row, "NAME"))
        case_number = clean_text(cell(row, "CASE NUMBER"))
        if not name and not case_number:
            continue
        entity = clean_text(cell(row, "ENTITY TYPE")).upper()
        if entity and "COMM OPER" not in entity and "AIR CARRIER" not in entity:
            continue
        if not entity:
            continue
        amount = cell(row, "SANCTION AMOUNT")
        amount_text = "" if amount is None else clean_text(amount)
        parsed.append({
            "caseNumber": case_number,
            "name": name,
            "entityType": clean_text(cell(row, "ENTITY TYPE")),
            "action": clean_text(cell(row, "ACTION")),
            "amount": amount_text,
            "sanction": clean_text(cell(row, "SANCTION")),
            "caseType": clean_text(cell(row, "CASE TYPE")),
            "knownDate": parse_mdy(cell(row, "DATE KNOWN")),
            "closedDate": parse_mdy(cell(row, "CLOSED DATE")),
            "quarter": source.get("label") or "",
            "url": source["url"],
        })
    return parsed


def quarter_span(records: list[dict]) -> tuple[str, str]:
    dates = []
    for record in records:
        for key in ("closedDate", "knownDate"):
            if record.get(key):
                dates.append(record[key])
    if not dates:
        return "", ""
    return min(dates), max(dates)


def check_ntsb_count(count: int, previous: int | None):
    if count < MIN_PART135_ROWS:
        raise SyncError(
            f"NTSB Part 135 rows ({count}) are below {MIN_PART135_ROWS}. Nothing was published."
        )
    if previous and count < previous * 0.75:
        raise SyncError(
            f"NTSB Part 135 rows fell from {previous} to {count}. Nothing was published."
        )


def ntsb_fingerprint(html: str) -> dict:
    match = re.search(
        r'<td id="fileName">avall\.zip</td>\s*<td id="fileDate">([^<]+)</td>\s*<td id="fileSize">([^<]+)</td>',
        html,
    )
    if not match:
        raise SyncError("The NTSB download page has no avall.zip entry. Nothing was published.")
    return {
        "fileDate": parse_mdy(match.group(1).strip()),
        "fileSize": clean_text(match.group(2)),
        "sourceUrl": NTSB_PAGE,
        "downloadUrl": "https://data.ntsb.gov/avdata/FileDirectory/DownloadFile?fileID=avall.zip",
    }


def read_ntsb_part135(mdb_path: str) -> list[dict]:
    from access_parser import AccessParser

    database = AccessParser(mdb_path)
    aircraft = database.parse_table("aircraft")
    events = database.parse_table("events")
    count = len(aircraft["ev_id"])
    aircraft_rows = []
    for index in range(count):
        far_part = aircraft["far_part"][index]
        if clean_text(far_part) != "135":
            continue
        aircraft_rows.append({
            "ev_id": aircraft["ev_id"][index],
            "ntsb_no": aircraft["ntsb_no"][index],
            "regis_no": aircraft["regis_no"][index],
            "far_part": far_part,
            "oper_name": aircraft["oper_name"][index],
            "oper_dba": aircraft["oper_dba"][index],
            "oper_code": aircraft["oper_code"][index],
            "acft_make": aircraft["acft_make"][index],
            "acft_model": aircraft["acft_model"][index],
        })
    events_by_id = {}
    for index in range(len(events["ev_id"])):
        ev_id = clean_text(events["ev_id"][index])
        events_by_id[ev_id] = {
            "ev_type": events["ev_type"][index],
            "ev_date": events["ev_date"][index],
            "ev_city": events["ev_city"][index],
            "ev_state": events["ev_state"][index],
            "ev_highest_injury": events["ev_highest_injury"][index],
            "ntsb_no": events["ntsb_no"][index],
        }
    records = part135_records(aircraft_rows, events_by_id)
    return records, len(aircraft_rows)


def read_enforcement_reports(html: str) -> tuple[list[dict], list[dict]]:
    links = enforcement_links(html)
    if len(links) < 4:
        raise SyncError(
            f"The FAA enforcement page listed {len(links)} spreadsheets. Nothing was published."
        )
    records = []
    files = []
    for link in links:
        blob, headers = request(link["url"])
        kind = headers.get("content-type", "")
        if "spreadsheet" not in kind and "excel" not in kind and not blob[:2] == b"PK":
            files.append({**link, "parsed": False, "reason": kind or "not a spreadsheet"})
            continue
        parsed = parse_enforcement_sheet(blob, link)
        files.append({**link, "parsed": True, "rows": len(parsed), "bytes": len(blob)})
        records.extend(parsed)
    parsed_files = [item for item in files if item.get("parsed")]
    if len(parsed_files) < 4 or not records:
        raise SyncError("FAA enforcement spreadsheets did not yield any rows. Nothing was published.")
    return records, files


def fetch_ntsb(previous_rows: int | None):
    from sync_faa import sha256_bytes

    html, _headers = request(NTSB_INDEX)
    finger = ntsb_fingerprint(html.decode("utf-8", "replace"))
    blob, _headers = request(finger["downloadUrl"], timeout=300)
    finger["contentHash"] = sha256_bytes(blob)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = archive.namelist()[0]
        mdb = archive.read(name)
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        path = os.path.join(temp, "avall.mdb")
        with open(path, "wb") as handle:
            handle.write(mdb)
        records, aircraft_rows = read_ntsb_part135(path)
    check_ntsb_count(aircraft_rows, previous_rows)
    finger["aircraftRows"] = aircraft_rows
    finger["eventCount"] = len(records)
    return finger, records


def fetch_enforcement():
    from sync_faa import sha256_bytes

    html, _headers = request(ENFORCEMENT_PAGE)
    records, files = read_enforcement_reports(html.decode("utf-8", "replace"))
    span_dates = []
    for item in files:
        if not item.get("parsed"):
            continue
        found = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", item.get("label") or "")
        span_dates.extend(parse_mdy(piece) for piece in found)
    span_dates = [item for item in span_dates if item]
    if span_dates:
        start, end = min(span_dates), max(span_dates)
    else:
        start, end = quarter_span(records)
    digest = sha256_bytes("\n".join(sorted(item["url"] for item in files if item.get("parsed"))).encode())
    return {
        "sourceUrl": ENFORCEMENT_PAGE,
        "contentHash": digest,
        "files": files,
        "coverageStart": start,
        "coverageEnd": end,
        "rowCount": len(records),
    }, records


def apply_safety(operators: list[dict], ntsb_records: list[dict], enforcement_records: list[dict]) -> dict:
    ntsb_stats = match_records(operators, ntsb_records, ntsb_name_keys, ntsb_designators)
    enforcement_stats = match_records(
        operators,
        enforcement_records,
        lambda record: [record.get("name") or ""],
        lambda record: [],
        normalizer=norm_name_keep_suffix,
    )
    for operator in operators:
        ntsb_items = ntsb_stats["attached"].get(operator["id"], [])
        enforcement_items = enforcement_stats["attached"].get(operator["id"], [])
        operator["safety"] = {
            "ntsb": [_public_ntsb(item) for item in ntsb_items],
            "enforcement": [_public_enforcement(item) for item in enforcement_items],
        }
    return {"ntsb": ntsb_stats, "enforcement": enforcement_stats}


def _public_ntsb(item: dict) -> dict:
    record = item["record"]
    return {
        "match": item["match"],
        "date": record.get("date") or "",
        "eventType": record.get("eventType") or "",
        "city": record.get("city") or "",
        "state": record.get("state") or "",
        "highestInjury": record.get("highestInjury") or "",
        "ntsbNumber": record.get("ntsbNumber") or "",
        "url": record.get("url") or "",
        "aircraft": record.get("aircraft") or [],
        "sourceOperator": ", ".join(record.get("operatorNames") or []) or ", ".join(record.get("operatorDbas") or []),
        "sourceDesignators": record.get("designators") or [],
    }


def update_saved_site() -> None:
    from sync_faa import META_PATH, OPERATORS_PATH, load_json, now_iso, sha256_json, write_json

    operators = load_json(OPERATORS_PATH, [])
    meta = load_json(META_PATH, {})
    if not operators:
        raise SystemExit("No operators file to attach safety records to.")
    previous_rows = ((meta.get("safety") or {}).get("ntsb") or {}).get("aircraftRows")
    ntsb_info, ntsb_records = fetch_ntsb(previous_rows)
    enforcement_info, enforcement_records = fetch_enforcement()
    stats = apply_safety(operators, ntsb_records, enforcement_records)
    meta["lastChecked"] = now_iso()
    meta["datasetHash"] = sha256_json(operators)
    meta["safety"] = {
        "ntsb": {key: ntsb_info[key] for key in ("sourceUrl", "downloadUrl", "fileDate", "fileSize", "contentHash", "aircraftRows", "eventCount")},
        "enforcement": {key: enforcement_info[key] for key in ("sourceUrl", "contentHash", "coverageStart", "coverageEnd", "rowCount", "files")},
    }
    meta["safety"]["ntsb"].update({
        "matchedEvents": stats["ntsb"]["certificateMatches"] + stats["ntsb"]["nameMatches"],
        "certificateMatches": stats["ntsb"]["certificateMatches"],
        "nameMatches": stats["ntsb"]["nameMatches"],
        "unmatched": stats["ntsb"]["unmatched"],
        "ambiguous": stats["ntsb"]["ambiguous"],
    })
    meta["safety"]["enforcement"].update({
        "matchedRows": stats["enforcement"]["nameMatches"],
        "nameMatches": stats["enforcement"]["nameMatches"],
        "unmatched": stats["enforcement"]["unmatched"],
        "ambiguous": stats["enforcement"]["ambiguous"],
    })
    with_ntsb = sum(1 for op in operators if op.get("safety", {}).get("ntsb"))
    with_enf = sum(1 for op in operators if op.get("safety", {}).get("enforcement"))
    write_json(OPERATORS_PATH, operators)
    write_json(META_PATH, meta)
    print({
        "ntsbAircraftRows": ntsb_info["aircraftRows"],
        "ntsbEvents": ntsb_info["eventCount"],
        "ntsbFileDate": ntsb_info["fileDate"],
        "operatorsWithNtsb": with_ntsb,
        "ntsbCertificateMatches": stats["ntsb"]["certificateMatches"],
        "ntsbNameMatches": stats["ntsb"]["nameMatches"],
        "enforcementRows": enforcement_info["rowCount"],
        "enforcementCoverage": [enforcement_info["coverageStart"], enforcement_info["coverageEnd"]],
        "operatorsWithEnforcement": with_enf,
        "enforcementNameMatches": stats["enforcement"]["nameMatches"],
    })


def _public_enforcement(item: dict) -> dict:
    record = item["record"]
    return {
        "match": item["match"],
        "caseNumber": record.get("caseNumber") or "",
        "name": record.get("name") or "",
        "action": record.get("action") or "",
        "amount": record.get("amount") or "",
        "sanction": record.get("sanction") or "",
        "caseType": record.get("caseType") or "",
        "closedDate": record.get("closedDate") or "",
        "knownDate": record.get("knownDate") or "",
        "quarter": record.get("quarter") or "",
        "url": record.get("url") or "",
    }


if __name__ == "__main__":
    update_saved_site()

