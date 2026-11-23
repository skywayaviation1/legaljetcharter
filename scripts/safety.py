"""Public safety records for Part 135 operators.

Sources, and only these:

- NTSB aviation accident database, avall.zip, from https://data.ntsb.gov/avdata/
  Aircraft rows whose FAR part is 135.
- Weekly NTSB update files (upDDMMM.zip) published after that avall.zip snapshot.
  Those files are how a crash that happened after the snapshot enters the database.
- NTSB investigation pages for aviation events on or after the avall.zip file date
  that are not in the database yet. The page is the record; nothing is copied
  from news stories.
- FAA quarterly enforcement reports linked from
  https://www.faa.gov/about/office_org/headquarters_offices/agc/practice_areas/enforcement/reports
  Spreadsheet quarters only.

A designator match is a certificate match. An N-number that is on exactly one
operator's FAA aircraft list is a registration match. A legal name or DBA is
attached only when it matches exactly one operator. If several operators share
that name, the aircraft model named by NTSB can select one of them, and the
page still says "name match - verify".
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from urllib.parse import urlencode, urljoin

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
NTSB_INVESTIGATIONS = "https://www.ntsb.gov/investigations/_api/web/lists/getbytitle('Pages')/items"
MIN_PART135_ROWS = 700
MAKE_ALIASES = {
    "LEARJET": "LR",
    "LEAR": "LR",
    "CESSNA": "C",
    "BEECHCRAFT": "BE",
    "BEECH": "BE",
    "PIPER": "PA",
    "ROBINSON": "R",
}

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
    if "T" in text:
        return parse_mdy(text.split("T", 1)[0])
    if " " in text:
        return parse_mdy(text.split(" ", 1)[0])
    return ""


def part135_records(aircraft_rows: list[dict], events_by_id: dict[str, dict], parts=("135",)) -> list[dict]:
    allowed = None if parts is None else {clean_text(part) for part in parts}
    grouped: dict[str, dict] = {}
    for row in aircraft_rows:
        far_part = clean_text(row.get("far_part"))
        if allowed is not None and far_part not in allowed:
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
                "farParts": [],
                "preliminary": False,
            }
            grouped[ev_id] = record
        if far_part and far_part not in record["farParts"]:
            record["farParts"].append(far_part)
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
    if isinstance(dbas, dict) and dbas.get("source") == "site":
        # Reviewed site DBAs are for display and search only. They are not
        # NTSB name keys, so "Upper Jets" does not pull in another operator
        # that used the same trade name.
        faa = dbas.get("faa") or []
        names.extend(clean_text(item) for item in faa if item)
        return names
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


def _operator_aircraft(operator: dict) -> list[dict]:
    raw = operator.get("aircraft") or []
    return [item for item in raw if isinstance(item, dict)]


def registration_forms(value: str) -> set[str]:
    text = re.sub(r"[^A-Z0-9]", "", clean_text(value).upper())
    if len(text) < 2:
        return set()
    forms = {text}
    if text.startswith("N") and text[1].isdigit():
        forms.add(text[1:])
    elif text[0].isdigit():
        forms.add("N" + text)
    return forms


def model_pairs(text: str) -> set[tuple[str, str]]:
    tokens = []
    for token in re.findall(r"[A-Z0-9]+", clean_text(text).upper()):
        tokens.append(MAKE_ALIASES.get(token, token))
        mixed = re.fullmatch(r"([A-Z]{1,6})(\d{2,4})[A-Z]?", tokens[-1])
        if mixed:
            tokens.append(mixed.group(1))
            tokens.append(mixed.group(2))
    families = [token for token in tokens if re.search(r"[A-Z]", token) and len(token) >= 2]
    numbers = [token for token in tokens if token.isdigit() and len(token) >= 2]
    return {(family, number) for family in families for number in numbers}


def record_model_pairs(record: dict) -> set[tuple[str, str]]:
    pairs = set()
    for craft in record.get("aircraft") or []:
        pairs |= model_pairs(f"{craft.get('make') or ''} {craft.get('model') or ''}")
    pairs |= model_pairs(record.get("modelText") or "")
    return pairs


def operator_model_pairs(operator: dict) -> set[tuple[str, str]]:
    pairs = set()
    for craft in _operator_aircraft(operator):
        pairs |= model_pairs(craft.get("model") or "")
    return pairs


def match_records(operators: list[dict], records: list[dict], name_keys, designator_key, normalizer=norm_name) -> dict:
    """Return {operator id: [{record, match}]} and a small stats dict.

    name_keys and designator_key are callables on a record.
    """
    by_designator: dict[str, list] = defaultdict(list)
    by_name: dict[str, list] = defaultdict(list)
    by_registration: dict[str, list] = defaultdict(list)
    for operator in operators:
        designator = _designator(operator)
        if designator:
            by_designator[designator].append(operator)
        for name in _operator_names(operator):
            key = normalizer(name)
            if len(key) >= 6:
                by_name[key].append(operator)
        seen_tails = set()
        for craft in _operator_aircraft(operator):
            for form in registration_forms(craft.get("registration") or ""):
                if form in seen_tails:
                    continue
                seen_tails.add(form)
                by_registration[form].append(operator)

    rules = safety_exclusion_rules()
    attached: dict[str, list] = defaultdict(list)
    certificate = 0
    registration = 0
    name_only = 0
    unmatched = 0
    ambiguous = 0
    excluded = 0
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
            reg_hits = []
            for craft in record.get("aircraft") or []:
                for form in registration_forms(craft.get("registration") or ""):
                    reg_hits.extend(by_registration.get(form, []))
            unique_regs = {id(op): op for op in reg_hits}
            name_hits = []
            for name in name_keys(record):
                key = normalizer(name)
                if len(key) < 6:
                    continue
                name_hits.extend(by_name.get(key, []))
            unique_names = {id(op): op for op in name_hits}
            name_was_ambiguous = len(unique_names) > 1
            if name_was_ambiguous:
                wanted = record_model_pairs(record)
                narrowed = [
                    op for op in unique_names.values()
                    if wanted and operator_model_pairs(op) & wanted
                ]
                unique_names = {id(op): op for op in narrowed}
            named = any(clean_text(name) for name in name_keys(record))
            name_choice = next(iter(unique_names.values())) if len(unique_names) == 1 else None
            reg_choice = next(iter(unique_regs.values())) if len(unique_regs) == 1 else None
            recent = (record.get("date") or "") >= "2024-01-01" or bool(record.get("preliminary"))
            if (name_was_ambiguous and name_choice is None) or (len(unique_regs) > 1 and name_choice is None):
                ambiguous += 1
                continue
            if reg_choice and name_choice and reg_choice is not name_choice:
                chosen = name_choice
                match = "name match - verify"
            elif reg_choice and name_choice is reg_choice:
                chosen = reg_choice
                match = "registration match"
            elif reg_choice and not named and recent:
                chosen = reg_choice
                match = "registration match"
            elif name_choice:
                chosen = name_choice
                match = "name match - verify"
        if chosen is None:
            unmatched += 1
            continue
        if is_safety_excluded(record, chosen, rules):
            excluded += 1
            continue
        if match == "certificate match":
            certificate += 1
        elif match == "registration match":
            registration += 1
        else:
            name_only += 1
        attached[chosen["id"]].append({"match": match, "record": record})
    return {
        "attached": attached,
        "certificateMatches": certificate,
        "registrationMatches": registration,
        "nameMatches": name_only,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "excluded": excluded,
    }


def ntsb_name_keys(record):
    return list(record.get("operatorNames") or []) + list(record.get("operatorDbas") or [])


def ntsb_designators(record):
    return list(record.get("designators") or [])


def safety_exclusion_rules() -> list[dict]:
    from sync_faa import DATA, load_json

    payload = load_json(DATA / "safety-overrides.json", {})
    rules = payload.get("exclude") if isinstance(payload, dict) else None
    return list(rules or [])


def is_safety_excluded(record: dict, operator: dict, rules: list[dict]) -> bool:
    ev_id = clean_text(record.get("evId"))
    number = clean_text(record.get("ntsbNumber")).upper()
    designator = _designator(operator)
    for rule in rules:
        rule_designator = norm_designator(rule.get("designator"))
        if rule_designator and rule_designator != designator:
            continue
        rule_ev = clean_text(rule.get("evId"))
        rule_number = clean_text(rule.get("ntsbNumber")).upper()
        if rule_ev and rule_ev == ev_id:
            return True
        if rule_number and rule_number == number:
            return True
    return False


def parse_stamp(value) -> datetime | None:
    text = clean_text(value)
    formats = (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%Y",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def update_file_entries(html: str) -> list[dict]:
    found = []
    pattern = re.compile(
        r'<td id="fileName">(up\d{2}[A-Z]{3}\.zip)</td>\s*<td id="fileDate">([^<]+)</td>\s*<td id="fileSize">([^<]+)</td>',
        re.I,
    )
    for name, file_date, file_size in pattern.findall(html):
        stamp = parse_stamp(file_date)
        found.append({
            "name": name,
            "fileDate": parse_mdy(file_date),
            "fileStamp": stamp,
            "fileSize": clean_text(file_size),
            "downloadUrl": f"https://data.ntsb.gov/avdata/FileDirectory/DownloadFile?fileID={name}",
        })
    return found


def plain_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def parse_operated_by(text: str) -> tuple[str, str]:
    match = re.search(
        r"operated by\s+(.+?)(?:\s+d/?b/?a\s+(.+?))?(?=\s+in\b|\s+near\b|[.]|$)",
        text,
        flags=re.I,
    )
    if not match:
        return "", ""
    name = re.sub(r"[\s,.;:]+$", "", match.group(1) or "").strip()
    dba = re.sub(r"[\s,.;:]+$", "", match.group(2) or "").strip()
    return name, dba


def aircraft_from_phrase(text: str) -> tuple[str, str, str]:
    match = re.search(r"\b(?:crash|accident|incident)\s+of\s+(?:an?\s+)?(.+?)\s+operated by\b", text, flags=re.I)
    if not match:
        return "", "", ""
    phrase = re.sub(r"[\s,.;:]+$", "", match.group(1)).strip()
    parts = phrase.split()
    if len(parts) >= 2 and re.search(r"\d", parts[-1]):
        return phrase, " ".join(parts[:-1]), parts[-1]
    return phrase, "", phrase


def investigation_record(item: dict) -> dict | None:
    ntsb_no = clean_text(item.get("NTSBAccidentID")).upper()
    if not ntsb_no:
        return None
    summary = plain_text(item.get("PublishingPageContent") or "")
    title = plain_text(item.get("Title") or "")
    name, dba = parse_operated_by(summary or title)
    phrase, make, model = aircraft_from_phrase(summary or title)
    registrations = []
    for tail in re.findall(r"\bN\d{1,5}[A-Z]{0,2}\b", f"{title} {summary}".upper()):
        if tail not in registrations:
            registrations.append(tail)
    if not name and not dba and not registrations:
        return None
    kind = f"{title} {summary}".lower()
    if "incident" in kind and "accident" not in kind and "crash" not in kind:
        event_type = "Incident"
    elif "crash" in kind or "accident" in kind:
        event_type = "Accident"
    else:
        event_type = "Investigation"
    file_ref = clean_text(item.get("FileRef"))
    if file_ref.startswith("/"):
        url = "https://www.ntsb.gov" + file_ref
    else:
        url = f"https://www.ntsb.gov/investigations/Pages/{ntsb_no}.aspx"
    aircraft = []
    if registrations or make or model:
        if registrations:
            for tail in registrations:
                aircraft.append({"registration": tail, "make": make, "model": model})
        else:
            aircraft.append({"registration": "", "make": make, "model": model})
    return {
        "evId": "",
        "ntsbNumber": ntsb_no,
        "date": parse_mdy(item.get("Accident_x0020_Date")),
        "eventType": event_type,
        "eventTypeCode": "",
        "city": clean_text(item.get("Accident_x0020_Location_x0020__x002d__x0020_City")),
        "state": clean_text(item.get("Accident_x0020_Location_x0020__x002d__x0020_State")),
        "highestInjury": "",
        "url": url,
        "aircraft": aircraft,
        "operatorNames": [name] if name else [],
        "operatorDbas": [dba] if dba else [],
        "designators": [],
        "farParts": [],
        "preliminary": True,
        "modelText": phrase,
    }


def merge_ntsb_records(base: list[dict], updates: list[dict]) -> list[dict]:
    by_id = {record["evId"]: record for record in base if record.get("evId")}
    for record in updates:
        ev_id = record.get("evId")
        if ev_id:
            by_id[ev_id] = record
    merged = list(by_id.values())
    merged.sort(key=lambda item: item.get("date") or "", reverse=True)
    return merged


def add_investigations(records: list[dict], investigations: list[dict]) -> list[dict]:
    known = {clean_text(record.get("ntsbNumber")).upper() for record in records if record.get("ntsbNumber")}
    extra = []
    for record in investigations:
        number = clean_text(record.get("ntsbNumber")).upper()
        if not number or number in known:
            continue
        known.add(number)
        extra.append(record)
    combined = records + extra
    combined.sort(key=lambda item: item.get("date") or "", reverse=True)
    return combined


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
    raw_date = match.group(1).strip()
    return {
        "fileDate": parse_mdy(raw_date),
        "fileStamp": parse_stamp(raw_date),
        "fileSize": clean_text(match.group(2)),
        "sourceUrl": NTSB_PAGE,
        "downloadUrl": "https://data.ntsb.gov/avdata/FileDirectory/DownloadFile?fileID=avall.zip",
    }


def read_ntsb_mdb(mdb_path: str, parts=("135",)):
    from access_parser import AccessParser

    database = AccessParser(mdb_path)
    aircraft = database.parse_table("aircraft")
    events = database.parse_table("events")
    count = len(aircraft["ev_id"])
    allowed = None if parts is None else {clean_text(part) for part in parts}
    aircraft_rows = []
    for index in range(count):
        far_part = aircraft["far_part"][index]
        if allowed is not None and clean_text(far_part) not in allowed:
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
    records = part135_records(aircraft_rows, events_by_id, parts=parts)
    part135_rows = sum(1 for row in aircraft_rows if clean_text(row.get("far_part")) == "135")
    return records, part135_rows


def _mdb_from_zip(blob: bytes) -> tuple[list[dict], int]:
    import os
    import tempfile

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = next(item for item in archive.namelist() if item.lower().endswith(".mdb"))
        payload = archive.read(name)
    with tempfile.TemporaryDirectory() as temp:
        path = os.path.join(temp, "source.mdb")
        with open(path, "wb") as handle:
            handle.write(payload)
        return read_ntsb_mdb(path, parts=("135",))


def _mdb_updates_from_zip(blob: bytes):
    import os
    import tempfile

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        name = next(item for item in archive.namelist() if item.lower().endswith(".mdb"))
        payload = archive.read(name)
    with tempfile.TemporaryDirectory() as temp:
        path = os.path.join(temp, "update.mdb")
        with open(path, "wb") as handle:
            handle.write(payload)
        return read_ntsb_mdb(path, parts=None)


def fetch_investigation_pages(since: str) -> list[dict]:
    if not since:
        raise SyncError("NTSB investigation pages need the avall.zip file date. Nothing was published.")
    query = urlencode({
        "$select": ",".join([
            "Title",
            "NTSBAccidentID",
            "FileRef",
            "Accident_x0020_Date",
            "Accident_x0020_Location_x0020__x002d__x0020_City",
            "Accident_x0020_Location_x0020__x002d__x0020_State",
            "Mode",
            "PublishingPageContent",
        ]),
        "$filter": f"Mode eq 'Aviation' and Accident_x0020_Date ge datetime'{since}T00:00:00Z'",
        "$top": "200",
    })
    url = NTSB_INVESTIGATIONS + "?" + query
    blob, _headers = request(url, headers={"Accept": "application/json;odata=verbose"})
    try:
        import json
        payload = json.loads(blob.decode("utf-8", "replace"))
        rows = payload["d"]["results"]
    except (KeyError, ValueError, TypeError) as exc:
        raise SyncError("The NTSB investigation list did not return records. Nothing was published.") from exc
    records = []
    for item in rows:
        record = investigation_record(item)
        if record:
            records.append(record)
    return records


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

    page, _headers = request(NTSB_INDEX)
    html = page.decode("utf-8", "replace")
    finger = ntsb_fingerprint(html)
    listed = update_file_entries(html)
    if len(listed) < 10:
        raise SyncError("The NTSB download page is missing its weekly update files. Nothing was published.")
    blob, _headers = request(finger["downloadUrl"], timeout=300)
    finger["contentHash"] = sha256_bytes(blob)
    records, aircraft_rows = _mdb_from_zip(blob)
    check_ntsb_count(aircraft_rows, previous_rows)
    avall_stamp = finger.get("fileStamp")
    newer = [item for item in listed if avall_stamp and item.get("fileStamp") and item["fileStamp"] > avall_stamp]
    applied = []
    for item in sorted(newer, key=lambda row: row["fileStamp"]):
        update_blob, _headers = request(item["downloadUrl"], timeout=180)
        update_records, part135_rows = _mdb_updates_from_zip(update_blob)
        records = merge_ntsb_records(records, update_records)
        applied.append({
            "name": item["name"],
            "fileDate": item["fileDate"],
            "fileSize": item["fileSize"],
            "downloadUrl": item["downloadUrl"],
            "rows": len(update_records),
            "part135Rows": part135_rows,
            "contentHash": sha256_bytes(update_blob),
        })
    investigations = fetch_investigation_pages(finger["fileDate"])
    records = add_investigations(records, investigations)
    finger["aircraftRows"] = aircraft_rows
    finger["eventCount"] = len(records)
    finger["updates"] = applied
    finger["updatesThrough"] = applied[-1]["fileDate"] if applied else finger["fileDate"]
    finger["investigationPages"] = len(investigations)
    finger.pop("fileStamp", None)
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
    names = ", ".join(record.get("operatorNames") or [])
    dbas = ", ".join(record.get("operatorDbas") or [])
    if names and dbas:
        source = f"{names} DBA {dbas}"
    else:
        source = names or dbas
    public = {
        "match": item["match"],
        "date": record.get("date") or "",
        "eventType": record.get("eventType") or "",
        "city": record.get("city") or "",
        "state": record.get("state") or "",
        "highestInjury": record.get("highestInjury") or "",
        "ntsbNumber": record.get("ntsbNumber") or "",
        "url": record.get("url") or "",
        "aircraft": [
            {"registration": craft.get("registration") or "", "make": craft.get("make") or "", "model": craft.get("model") or ""}
            for craft in record.get("aircraft") or []
        ],
        "sourceOperator": source,
        "sourceDesignators": record.get("designators") or [],
    }
    far_parts = [part for part in record.get("farParts") or [] if part]
    if far_parts:
        public["farPart"] = ", ".join(far_parts)
    if record.get("preliminary"):
        public["preliminary"] = True
    return public


def ntsb_public_meta(info: dict, stats: dict) -> dict:
    ntsb = stats["ntsb"]
    return {
        "sourceUrl": info["sourceUrl"],
        "downloadUrl": info["downloadUrl"],
        "fileDate": info["fileDate"],
        "fileSize": info["fileSize"],
        "contentHash": info["contentHash"],
        "aircraftRows": info["aircraftRows"],
        "eventCount": info["eventCount"],
        "updates": info.get("updates") or [],
        "updatesThrough": info.get("updatesThrough") or "",
        "investigationPages": info.get("investigationPages") or 0,
        "investigationSource": "https://www.ntsb.gov/investigations/",
        "matchedEvents": ntsb["certificateMatches"] + ntsb["registrationMatches"] + ntsb["nameMatches"],
        "certificateMatches": ntsb["certificateMatches"],
        "registrationMatches": ntsb["registrationMatches"],
        "nameMatches": ntsb["nameMatches"],
        "unmatched": ntsb["unmatched"],
        "ambiguous": ntsb["ambiguous"],
    }


def apply_operator_overrides(operators: list[dict]) -> None:
    """Add reviewed DBAs after safety matching so a site alias is searchable
    and visible, but is not an NTSB name key."""
    from sync_faa import DATA, load_json

    payload = load_json(DATA / "operator-overrides.json", {})
    items = payload.get("operators") if isinstance(payload, dict) else None
    if not items:
        return
    by_designator = {_designator(operator): operator for operator in operators if _designator(operator)}
    for item in items:
        operator = by_designator.get(norm_designator(item.get("designator")))
        if operator is None:
            continue
        expected = clean_text(item.get("certificateNumber")).upper()
        if expected:
            current = operator.get("certificateNumber")
            current_text = current.get("value") if isinstance(current, dict) else current
            if clean_text(current_text).upper() not in ("", expected):
                continue
        site_names = []
        for name in item.get("dbas") or []:
            text = clean_text(name.get("name") if isinstance(name, dict) else name)
            if text and text not in site_names:
                site_names.append(text)
        if not site_names:
            continue
        existing = operator.get("dbas")
        faa = []
        if isinstance(existing, dict):
            if "faa" in existing:
                faa = [clean_text(value) for value in existing.get("faa") or [] if clean_text(value)]
            elif existing.get("source") != "site":
                faa = [clean_text(value) for value in existing.get("value") or [] if clean_text(value)]
        merged = list(faa)
        for name in site_names:
            if name not in merged:
                merged.append(name)
        operator["dbas"] = {
            "value": merged,
            "source": "site",
            "faa": faa,
            "site": site_names,
            "note": item.get("note") or "Added on this site. The FAA Part 135 list and Air Operator search do not print this DBA.",
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
    apply_operator_overrides(operators)
    meta["lastChecked"] = now_iso()
    meta["datasetHash"] = sha256_json(operators)
    meta["safety"] = {
        "ntsb": ntsb_public_meta(ntsb_info, stats),
        "enforcement": {key: enforcement_info[key] for key in ("sourceUrl", "contentHash", "coverageStart", "coverageEnd", "rowCount", "files")},
    }
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
        "ntsbRegistrationMatches": stats["ntsb"]["registrationMatches"],
        "ntsbNameMatches": stats["ntsb"]["nameMatches"],
        "updatesThrough": ntsb_info.get("updatesThrough"),
        "investigationPages": ntsb_info.get("investigationPages"),
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

