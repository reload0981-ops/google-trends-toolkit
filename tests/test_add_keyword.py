import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from collector import add_keyword as ak


ROOT = Path(__file__).resolve().parents[1]


class AddKeywordTest(unittest.TestCase):
    """Adding a keyword should need two decisions, not ten typed columns."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "reference").mkdir()
        (self.tmp / "data" / "series").mkdir(parents=True)
        self.keywords = self.tmp / "keywords.csv"
        self.tried = self.tmp / "reference" / "keywords_tried.csv"
        shutil.copy(ROOT / "keywords.csv", self.keywords)
        with self.tried.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["keyword_id", "canonical_keyword", "best_stage"]
            )
            writer.writeheader()
            writer.writerow(
                {"keyword_id": "FP700", "canonical_keyword": "คำที่เคยตาย",
                 "best_stage": "เคยลอง National แล้ว NO_DATA"}
            )

        for name, value in (
            ("KEYWORDS_CSV", self.keywords),
            ("TRIED_CSV", self.tried),
            ("BACKUP_DIR", self.tmp / "backup"),
            ("INCOMING", self.tmp / "incoming"),
            ("ROOT", self.tmp),
        ):
            original = getattr(ak, name)
            setattr(ak, name, value)
            self.addCleanup(setattr, ak, name, original)

    def rows(self):
        with self.keywords.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_the_menu_offers_every_group_and_maps_to_the_same_prefixes(self):
        # The menu is what a novice actually sees, so it must not drift from the
        # prefix table that fills Segment and Factor.
        self.assertEqual(len(ak.GROUP_MENU), len(ak.PREFIXES))
        self.assertEqual(
            [prefix for prefix, _, _ in ak.GROUP_MENU], sorted(ak.PREFIXES, key="FP FU NP NU TP TU".split().index)
        )
        for prefix, label, _ in ak.GROUP_MENU:
            self.assertIn(prefix, ak.PREFIXES)
            self.assertTrue(label.strip())

    def test_prefix_decides_segment_and_factor(self):
        self.assertEqual(ak.PREFIXES["FP"], ("Formal", "Pull"))
        self.assertEqual(ak.PREFIXES["TU"], ("Informal-Traditional", "Push"))
        self.assertEqual(len(ak.PREFIXES), 6)

    def test_adding_fills_every_column_from_the_prefix(self):
        self.assertEqual(ak.add("หางาน ต่างดาว", "FP"), 0)

        added = next(r for r in self.rows() if r["Keyword_TH"] == "หางาน ต่างดาว")
        self.assertTrue(added["Keyword_ID"].startswith("FP"))
        self.assertEqual(added["Segment"], "Formal")
        self.assertEqual(added["Factor"], "Pull")
        self.assertEqual(added["Tier"], "T1")
        self.assertEqual(added["Case_ID"], added["Keyword_ID"])
        self.assertEqual(added["Case_Type"], "keyword")
        self.assertEqual(added["Family_ID"], "")
        self.assertEqual(added["Current_Status"], "active_current_official")

    def test_new_id_skips_numbers_used_anywhere(self):
        taken_before = {r["Keyword_ID"] for r in self.rows()}

        ak.add("คำใหม่หนึ่ง", "FP")
        first = self.rows()[-1]["Keyword_ID"]
        ak.add("คำใหม่สอง", "FP")
        second = self.rows()[-1]["Keyword_ID"]

        self.assertNotIn(first, taken_before)
        self.assertNotIn(second, taken_before)
        self.assertNotEqual(first, second)
        ids = [r["Keyword_ID"] for r in self.rows()]
        self.assertEqual(len(ids), len(set(ids)))
        # keywords_tried.csv already owns FP700, so a fresh FP id must clear it.
        self.assertGreater(int(first[2:]), 700)

    def test_a_keyword_already_in_the_set_is_refused(self):
        existing = self.rows()[0]["Keyword_TH"]
        self.assertEqual(ak.add(existing, "FP"), 1)

    def test_a_keyword_that_already_died_is_refused_unless_forced(self):
        self.assertEqual(ak.add("คำที่เคยตาย", "FP"), 1)
        self.assertEqual(ak.add("คำที่เคยตาย", "FP", force=True), 0)

    def test_unknown_prefix_is_refused(self):
        self.assertEqual(ak.add("อะไรก็ได้", "XX"), 2)

    def test_add_then_remove_restores_the_file_byte_for_byte(self):
        before = self.keywords.read_bytes()

        ak.add("คำชั่วคราว", "TU")
        added = self.rows()[-1]["Keyword_ID"]
        self.assertNotEqual(self.keywords.read_bytes(), before)
        self.assertEqual(ak.remove(added), 0)

        self.assertEqual(self.keywords.read_bytes(), before)

    def test_removal_refuses_once_the_keyword_reached_the_archive(self):
        ak.add("คำที่เข้าคลังแล้ว", "FP")
        added = self.rows()[-1]["Keyword_ID"]
        (self.tmp / "data" / "series" / f"{added}__TH.csv").write_text(
            "Month,Value\n2014-01,1.0\n", encoding="utf-8"
        )

        # Dropping the row alone would leave a series no keyword owns, which is a
        # structural error the release audit fails on.
        self.assertEqual(ak.remove(added), 1)
        self.assertIn(added, {r["Keyword_ID"] for r in self.rows()})

    def test_backups_in_the_same_second_do_not_overwrite_each_other(self):
        ak.add("คำสำรองหนึ่ง", "FP")
        ak.add("คำสำรองสอง", "FP")

        backups = sorted((self.tmp / "backup").glob("keywords-*.csv"))
        self.assertEqual(len(backups), 2)


if __name__ == "__main__":
    unittest.main()
