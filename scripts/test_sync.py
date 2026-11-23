#!/usr/bin/env python3
"""Unit tests for FAA parsing, matching, and the no-publish-on-bad-data rule."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_faa as sync


def part_op(designator, name, aircraft=None, cfrs=None, chdos=None):
    return {
        "designator": designator,
        "name": name,
        "nameVariants": [],
        "cfrs": cfrs or ["135"],
        "chdos": chdos or ["WP01"],
        "aircraft": aircraft or [{
            "registration": "N100" + designator[:2],
            "model": "CE-208",
            "serial": "1",
            "cfr": "135",
        }],
    }


def air_op(**kwargs):
    base = {
        "name": "",
        "designator": "",
        "certificateNumber": "",
        "issueDate": "",
        "address": "",
        "city": "",
        "state": "",
        "postalCode": "",
        "fsdo": "",
        "status": "",
        "cfrs": ["135"],
        "dbas": [],
        "aircraft": [],
        "extra": {},
    }
    base.update(kwargs)
    return base


class HeaderAndWorkbookTests(unittest.TestCase):
    def test_finds_workbook_link(self):
        html = '''
        <a href="/sites/faa.gov/files/old.xls">old</a>
        <a href="/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders.xlsx">list</a>
        '''
        link = sync.find_workbook_link(html, sync.HOLDERS_PAGE)
        self.assertTrue(link.endswith("part-135-holders.xlsx"))
        self.assertTrue(link.startswith("https://www.faa.gov/"))

    def test_missing_link_fails(self):
        with self.assertRaises(sync.SyncError):
            sync.find_workbook_link("<html><a href='/about'>no file</a></html>", sync.HOLDERS_PAGE)

    def test_extra_column_is_kept_and_required_column_is_enforced(self):
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["CFR", "CHDO", "DSGN", "Name", "Aircraft M/M/S", "Registration No.", "Serial No.", "Notes"])
        sheet.append(["135", "WP39", "coea", "Empire Airlines, Inc.", "CE-208-B", "n700fx", "C208B0419", "kept"])
        sheet.append(["121/135", "WP39", "COEA", "Empire Airlines, Inc.", "CE-208-B", "N705FX", "C208B0425", ""])
        sheet.append(["135", "GL61", "Y2PA", "USA Jet Airlines, Inc.", None, None, None, None])
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as handle:
            book.save(handle.name)
            parsed = sync.parse_part135_workbook(Path(handle.name).read_bytes())
        self.assertEqual(parsed["operatorCount"], 2)
        self.assertEqual(parsed["aircraftCount"], 2)
        self.assertEqual(parsed["operators"][1]["aircraft"], [])
        self.assertEqual(parsed["operators"][0]["designator"], "COEA")
        self.assertEqual(parsed["operators"][0]["aircraft"][0]["extra"]["Notes"], "kept")
        self.assertIn("Notes", parsed["unmappedColumns"])

        sheet.append(["135", "WP39", "XXXX", "", "CE-208", "N1", "S"])
        book2 = openpyxl.Workbook()
        only = book2.active
        only.append(["Name", "Registration No."])
        only.append(["Someone", "N1"])
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as handle:
            book2.save(handle.name)
            with self.assertRaises(sync.SyncError):
                sync.parse_part135_workbook(Path(handle.name).read_bytes())


class AirInterpretTests(unittest.TestCase):
    def test_filters_to_part_135_and_joins_dba(self):
        payload = {
            "tables": {
                "Air Operators": {
                    "columns": ["Air Operator Name", "Designator Code", "Certificate Number", "City", "State", "FAR Part", "Issue Date"],
                    "rows": [
                        ["Example Air LLC", "EXAA", "EXAA123A", "Boise", "ID", "135", "2020-01-02"],
                        ["Airline Only", "AIRL", "AIRL1", "Dallas", "TX", "121", "2010-01-01"],
                    ],
                },
                "DBA": {
                    "columns": ["Designator", "Authorized DBA Name"],
                    "rows": [["EXAA", "Example Charters"]],
                },
                "Aircraft": {
                    "columns": ["Designator Code", "Aircraft MMS"],
                    "rows": [["EXAA", "C-208"]],
                },
            }
        }
        ops = sync.interpret_air_payload(payload)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["designator"], "EXAA")
        self.assertEqual(ops[0]["dbas"], ["Example Charters"])
        self.assertEqual(ops[0]["city"], "Boise")
        self.assertEqual(ops[0]["aircraft"][0]["model"], "C-208")


class AirCsvTests(unittest.TestCase):
    def test_pivots_staffing_rows_and_rejects_a_short_file(self):
        header = "14 CFR Part,Addr1,Addr2,Addr3,Air_Operator_Name,CEO Name,Cert_Issue_Date,Certificate_Number,Chief Pilot,City,Country,Director of Maintenance,Director of Operations,DSGN_CODE,Extension,Foreign_Phone,Measure Names,Phone,St,Zip/Postal Code,Measure Values"
        measures = ["PIC Captains", "Inspectors", "Designated Inspectors", "NonCertificated Mechanics", "Certificated Mechanics", "Repairmen", "Total Number of Employees"]
        lines = [header]
        for measure in measures:
            lines.append(f"135,1 MAIN,,,Acme Air LLC,CEO,8/18/2014,EXAA123A,,BOISE,US,,,EXAA,,,{measure},,ID,83702,3")
        ops = sync.parse_air_operator_csv("\n".join(lines) + "\n", "A")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["designator"], "EXAA")
        self.assertEqual(ops[0]["issueDate"], "2014-08-18")
        self.assertEqual(ops[0]["state"], "ID")
        self.assertEqual(ops[0]["staff"]["totalEmployees"], "3")
        with self.assertRaises(sync.SyncError):
            sync.parse_air_operator_csv("\n".join(lines[:3]) + "\n", "A")


class MergeTests(unittest.TestCase):
    def test_designator_match_name_conflict_and_single_source_rows(self):
        part = [
            part_op("EXAA", "Example Air LLC"),
            part_op("ONLY", "List Only Inc"),
            part_op("PLCA", "Choice Aviation, LLC"),
            part_op("VPEA", "Choice Aviation, LLC"),
        ]
        air = [
            air_op(name="Example Air, L.L.C.", designator="EXAA", certificateNumber="EXAA123A", city="BOISE", state="ID", dbas=["Example Charters"]),
            air_op(name="Search Only LLC", designator="SRCH", certificateNumber="SRCH1", city="MIAMI", state="FL"),
            air_op(name="Example Air Different", designator="OTHR", certificateNumber="OTHR1"),
        ]
        # Force a real name conflict on EXAA.
        air[0]["name"] = "Totally Different Name LLC"
        result = sync.merge_records(part, air)
        by_id = {rec["id"]: rec for rec in result["operators"]}
        self.assertEqual(by_id["dsgn:EXAA"]["presence"], "both")
        self.assertEqual(by_id["dsgn:EXAA"]["match"], "designator")
        self.assertEqual(by_id["dsgn:EXAA"]["city"]["value"], "BOISE")
        self.assertEqual(by_id["dsgn:EXAA"]["city"]["source"], "air-operator")
        self.assertTrue(any(item["field"] == "legalName" for item in by_id["dsgn:EXAA"]["conflicts"]))
        self.assertEqual(by_id["dsgn:ONLY"]["presence"], "part135-list")
        self.assertEqual(by_id["dsgn:SRCH"]["presence"], "air-operator")
        # Same legal name on two certificates is not collapsed, and the third
        # name does not get glued onto either of them.
        self.assertEqual(by_id["dsgn:PLCA"]["presence"], "part135-list")
        self.assertEqual(by_id["dsgn:VPEA"]["presence"], "part135-list")
        self.assertEqual(by_id["dsgn:OTHR"]["presence"], "air-operator")
        self.assertEqual(result["reconciliation"]["inBoth"], 1)
        self.assertEqual(result["reconciliation"]["onlyPart135List"], 3)
        self.assertEqual(result["reconciliation"]["onlyAirOperator"], 2)
        self.assertGreaterEqual(result["reconciliation"]["conflictFields"].get("legalName", 0), 1)

    def test_unique_name_fallback_when_search_has_no_designator(self):
        part = [part_op("EXAA", "Example Air LLC")]
        air = [air_op(name="Example Air, LLC", designator="", certificateNumber="EXAA123A", city="BOISE", state="ID")]
        result = sync.merge_records(part, air)
        rec = result["operators"][0]
        self.assertEqual(rec["presence"], "both")
        self.assertEqual(rec["match"], "unique-legal-name")
        self.assertEqual(rec["certificateNumber"]["value"], "EXAA123A")
        self.assertEqual(len(result["operators"]), 1)

    def test_different_designators_are_not_merged_on_name(self):
        part = [part_op("EXAA", "Example Air LLC")]
        air = [air_op(name="Example Air LLC", designator="ZZZZ", certificateNumber="ZZ1")]
        result = sync.merge_records(part, air)
        self.assertEqual(len(result["operators"]), 2)
        presences = sorted(rec["presence"] for rec in result["operators"])
        self.assertEqual(presences, ["air-operator", "part135-list"])

    def test_repeat_merge_is_stable(self):
        part = [part_op("EXAA", "Example Air LLC"), part_op("ONLY", "List Only Inc")]
        air = [air_op(name="Example Air LLC", designator="EXAA", city="BOISE", state="ID")]
        first = sync.merge_records(part, air)
        second = sync.merge_records(part, air)
        self.assertEqual(sync.sha256_json(first), sync.sha256_json(second))


class PublishNoChangeTests(unittest.TestCase):
    def test_second_publish_keeps_operator_bytes_and_moves_last_checked(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        saved = (sync.ROOT, sync.DATA, sync.SOURCES, sync.OPERATORS_PATH, sync.META_PATH, sync.PART_PATH, sync.AIR_PATH)
        sync.ROOT = root
        sync.DATA = root / "data"
        sync.SOURCES = sync.DATA / "sources"
        sync.OPERATORS_PATH = sync.DATA / "operators.json"
        sync.META_PATH = sync.DATA / "meta.json"
        sync.PART_PATH = sync.SOURCES / "part135.json"
        sync.AIR_PATH = sync.SOURCES / "air-operator.json"
        try:
            part = {
                "operatorCount": 1,
                "aircraftCount": 1,
                "operators": [part_op("EXAA", "Example Air LLC")],
                "fileUrl": "https://www.faa.gov/example.xlsx",
                "fileDate": "2026-11-16",
                "lastModified": "Mon, 16 Nov 2026 19:03:18 GMT",
                "fingerprint": {"url": "https://www.faa.gov/example.xlsx", "lastModified": "x", "contentLength": "1", "etag": "y"},
                "contentHash": "abc",
                "byteLength": 10,
                "columns": ["Name"],
                "unmappedColumns": [],
            }
            air = {
                "operators": [air_op(name="Example Air LLC", designator="EXAA", city="Boise", state="ID")],
                "operatorCount": 1,
                "updatedLabel": "Monday, November 16, 2026",
                "contentHash": "def",
                "extractedAt": "2026-11-22T00:00:00Z",
                "partFilter": "135",
                "method": "test",
            }
            merged = sync.merge_records(part["operators"], air["operators"])
            sync.publish(part, air, merged, {}, "2026-11-22T00:00:00Z", enforce_floors=False)
            first = sync.OPERATORS_PATH.read_bytes()
            previous = json.loads(sync.META_PATH.read_text())
            sync.publish(part, air, merged, previous, "2026-11-22T01:00:00Z", enforce_floors=False)
            self.assertEqual(first, sync.OPERATORS_PATH.read_bytes())
            self.assertEqual(json.loads(sync.META_PATH.read_text())["lastChecked"], "2026-11-22T01:00:00Z")
            summary = json.loads((root / ".sync-summary.json").read_text())
            self.assertFalse(summary["dataChanged"])
        finally:
            (sync.ROOT, sync.DATA, sync.SOURCES, sync.OPERATORS_PATH, sync.META_PATH, sync.PART_PATH, sync.AIR_PATH) = saved
            tmp.cleanup()


class SanityTests(unittest.TestCase):
    def test_drop_versus_previous_fails_and_floors_can_be_relaxed_for_fixtures(self):
        part = {"operatorCount": 10, "aircraftCount": 10}
        air = [air_op(name="A", designator="AAAA")]
        merged = {"operators": [{}] * 10}
        with self.assertRaises(sync.SyncError):
            sync.sanity_check(part, air, merged, {}, enforce_floors=True)
        previous = {
            "part135": {"operatorCount": 100, "aircraftCount": 100},
            "airOperator": {"operatorCount": 100},
            "operatorCount": 100,
        }
        with self.assertRaises(sync.SyncError):
            sync.sanity_check(part, air * 20, merged, previous, enforce_floors=False)

    def test_no_change_hash_skips_operator_rewrite(self):
        part = [part_op("EXAA", "Example Air LLC")]
        air = [air_op(name="Example Air LLC", designator="EXAA")]
        merged = sync.merge_records(part, air)
        original = sync.sha256_json([sync.public_operator(rec) for rec in merged["operators"]])
        again = sync.merge_records(part, air)
        repeat = sync.sha256_json([sync.public_operator(rec) for rec in again["operators"]])
        self.assertEqual(original, repeat)
        changed = original != repeat
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
