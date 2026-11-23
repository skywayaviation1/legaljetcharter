#!/usr/bin/env python3
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import safety


def operator(op_id, name, designator, dbas=None):
    return {
        "id": op_id,
        "legalName": {"value": name, "source": "part135-list"},
        "designator": {"value": designator, "source": "part135-list"},
        "dbas": {"value": dbas or [], "source": "air-operator"},
    }


class NtsbTests(unittest.TestCase):
    def test_keeps_part_135_and_matches_the_designator(self):
        aircraft = [
            {"ev_id": "E1", "far_part": "135", "ntsb_no": "ERA24LA001", "regis_no": "N100", "oper_name": "Other Name LLC", "oper_dba": "", "oper_code": "EXAA", "acft_make": "Cessna", "acft_model": "208"},
            {"ev_id": "E2", "far_part": "091", "ntsb_no": "ERA24LA002", "regis_no": "N200", "oper_name": "Example Air LLC", "oper_dba": "", "oper_code": "EXAA", "acft_make": "Cessna", "acft_model": "172"},
        ]
        events = {"E1": {"ev_type": "ACC", "ev_date": "01/02/24 00:00:00", "ev_city": "Boise", "ev_state": "ID", "ev_highest_injury": "N", "ntsb_no": "ERA24LA001"}}
        records = safety.part135_records(aircraft, events)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["date"], "2024-01-02")
        self.assertEqual(records[0]["eventType"], "Accident")
        operators = [
            operator("dsgn:EXAA", "Example Air LLC", "EXAA"),
            operator("dsgn:OTHR", "Other Name LLC", "OTHR"),
        ]
        stats = safety.apply_safety(operators, records, [])
        self.assertEqual(stats["ntsb"]["certificateMatches"], 1)
        self.assertEqual(operators[0]["safety"]["ntsb"][0]["match"], "certificate match")
        self.assertEqual(operators[1]["safety"]["ntsb"], [])

    def test_name_match_is_labeled_and_ambiguous_names_are_not_attached(self):
        records = [{
            "evId": "E9", "ntsbNumber": "X", "date": "2020-01-01", "eventType": "Incident",
            "eventTypeCode": "INC", "city": "", "state": "", "highestInjury": "",
            "url": "https://my.ntsb.gov/report?ev_id=E9", "aircraft": [],
            "operatorNames": ["Shared Aviation LLC"], "operatorDbas": [], "designators": [],
        }]
        operators = [
            operator("a", "Shared Aviation LLC", "AAAA"),
            operator("b", "Shared Aviation Inc", "BBBB"),
        ]
        stats = safety.apply_safety(operators, records, [])
        self.assertEqual(stats["ntsb"]["ambiguous"], 1)
        self.assertEqual(operators[0]["safety"]["ntsb"], [])
        self.assertEqual(operators[1]["safety"]["ntsb"], [])

    def test_short_extract_fails(self):
        with self.assertRaises(safety.SyncError):
            safety.check_ntsb_count(10, None)


class EnforcementTests(unittest.TestCase):
    def test_reads_carrier_rows_and_skips_other_entities(self):
        import openpyxl

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["CASE NUMBER", "NAME", "ENTITY TYPE", "DATE KNOWN", "ACTION", "SANCTION AMOUNT", "SANCTION", "CASE TYPE", "CLOSED DATE"])
        sheet.append(["C1", "Example Air LLC", "A/C or COMM OPER", None, "ORD ASSESS CIVIL PENALTY", 2500, "DOLLARS", "FLT OPNS", None])
        sheet.append(["C2", "Jane Pilot", "PILOT", None, "CERT SUSPENDED", None, None, "FLT OPNS", None])
        handle = io.BytesIO()
        book.save(handle)
        rows = safety.parse_enforcement_sheet(handle.getvalue(), {"url": "https://www.faa.gov/report.xlsx", "label": "1/1/2026-3/31/2026"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Example Air LLC")
        operators = [operator("dsgn:EXAA", "Example Air LLC", "EXAA")]
        stats = safety.apply_safety(operators, [], rows)
        self.assertEqual(stats["enforcement"]["nameMatches"], 1)
        item = operators[0]["safety"]["enforcement"][0]
        self.assertEqual(item["match"], "name match - verify")
        self.assertEqual(item["url"], "https://www.faa.gov/report.xlsx")
        self.assertIn("2500", item["amount"])


if __name__ == "__main__":
    unittest.main()
