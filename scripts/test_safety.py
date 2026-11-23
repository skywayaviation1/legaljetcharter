#!/usr/bin/env python3
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import safety


def operator(op_id, name, designator, dbas=None, aircraft=None):
    return {
        "id": op_id,
        "legalName": {"value": name, "source": "part135-list"},
        "designator": {"value": designator, "source": "part135-list"},
        "dbas": {"value": dbas or [], "source": "air-operator"},
        "aircraft": aircraft or [],
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

    def test_registration_matches_one_operator_and_stops_when_shared(self):
        record = {
            "evId": "E3", "ntsbNumber": "ERA27LA099", "date": "2026-11-02", "eventType": "Accident",
            "city": "Miami", "state": "FL", "highestInjury": "",
            "url": "https://my.ntsb.gov/report?ev_id=E3",
            "aircraft": [{"registration": "N384JW", "make": "Learjet", "model": "60"}],
            "operatorNames": [], "operatorDbas": [], "designators": [], "farParts": ["091"],
        }
        operators = [
            operator("glra", "Aurora Aviation, Inc.", "GLRA", aircraft=[{"registration": "N384JW", "model": "LR-60-60"}]),
            operator("other", "Someone Else LLC", "OTHR", aircraft=[{"registration": "N1", "model": "C172"}]),
        ]
        stats = safety.apply_safety(operators, [record], [])
        self.assertEqual(stats["ntsb"]["registrationMatches"], 1)
        self.assertEqual(operators[0]["safety"]["ntsb"][0]["match"], "registration match")
        self.assertEqual(operators[0]["safety"]["ntsb"][0]["farPart"], "091")
        named = dict(record, operatorNames=["Flight Alaska"])
        stats = safety.apply_safety(operators, [named], [])
        self.assertEqual(stats["ntsb"]["unmatched"], 1)
        self.assertEqual(operators[0]["safety"]["ntsb"], [])
        operators[1]["aircraft"].append({"registration": "384JW", "model": "LR-60-60"})
        stats = safety.apply_safety(operators, [record], [])
        self.assertEqual(stats["ntsb"]["ambiguous"], 1)
        self.assertEqual(operators[0]["safety"]["ntsb"], [])

    def test_shared_name_uses_the_listed_aircraft_model(self):
        item = {
            "Title": "Crash of Learjet 60",
            "NTSBAccidentID": "ERA27MA026",
            "FileRef": "/investigations/Pages/ERA27MA026.aspx",
            "Accident_x0020_Date": "2026-11-01T04:00:00Z",
            "Accident_x0020_Location_x0020__x002d__x0020_City": "Greenacres",
            "Accident_x0020_Location_x0020__x002d__x0020_State": "FL",
            "PublishingPageContent": "<p>Nov. 1, 2026 crash of a Learjet 60 operated by Aurora Aviation dba Upper Jet\u200b in Greenacres, FL.</p>",
        }
        record = safety.investigation_record(item)
        self.assertEqual(record["ntsbNumber"], "ERA27MA026")
        self.assertEqual(record["date"], "2026-11-01")
        self.assertEqual(record["operatorNames"], ["Aurora Aviation"])
        self.assertEqual(record["operatorDbas"], ["Upper Jet"])
        self.assertTrue(record["preliminary"])
        self.assertNotIn("ev_id=", record["url"])
        operators = [
            operator("glra", "Aurora Aviation, Inc.", "GLRA", aircraft=[{"registration": "N384JW", "model": "LR-60-60"}]),
            operator("llc", "Aurora Aviation, LLC", "810A", aircraft=[{"registration": "N15MH", "model": "PA-60-601"}]),
        ]
        stats = safety.apply_safety(operators, [record], [])
        self.assertEqual(stats["ntsb"]["nameMatches"], 1)
        shown = operators[0]["safety"]["ntsb"][0]
        self.assertEqual(shown["match"], "name match - verify")
        self.assertEqual(shown["ntsbNumber"], "ERA27MA026")
        self.assertEqual(shown["url"], "https://www.ntsb.gov/investigations/Pages/ERA27MA026.aspx")
        self.assertIn("Upper Jet", shown["sourceOperator"])
        self.assertEqual(operators[1]["safety"]["ntsb"], [])
        self.assertEqual(safety.add_investigations(
            [{"ntsbNumber": "ERA27MA026", "evId": "already", "date": "2026-11-01"}],
            [record],
        )[0]["evId"], "already")

    def test_weekly_files_after_the_snapshot_are_kept(self):
        html = """
        <td id="fileName">up01NOV.zip</td><td id="fileDate">11/1/2026 3:01:28 AM</td><td id="fileSize">10</td>
        <td id="fileName">up08NOV.zip</td><td id="fileDate">11/8/2026 3:00:18 AM</td><td id="fileSize">20</td>
        """
        entries = safety.update_file_entries(html)
        self.assertEqual([item["name"] for item in entries], ["up01NOV.zip", "up08NOV.zip"])
        avall = safety.parse_stamp("11/1/2026 6:50:07 AM")
        newer = [item["name"] for item in entries if item["fileStamp"] > avall]
        self.assertEqual(newer, ["up08NOV.zip"])
        merged = safety.merge_ntsb_records(
            [{"evId": "E1", "date": "2020-01-01", "city": "Old"}],
            [{"evId": "E1", "date": "2020-01-01", "city": "New"}, {"evId": "E2", "date": "2026-11-08", "city": "Added"}],
        )
        self.assertEqual(merged[0]["city"], "Added")
        self.assertEqual([item["city"] for item in merged if item["evId"] == "E1"], ["New"])


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
