import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.aggregator_hub.prepare import run_prepare_pipeline
from burning_man_scraper.bm_ingest.sources import default_www_dir
from burning_man_scraper.verification.www_loader import assert_playaevents_art_csv


class WwwTemplateGuardTests(unittest.TestCase):
    def test_reject_artelier_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artelier_bm_upload_2016.csv"
            path.write_text(
                "project_title,bm_uid,project_year\n"
                "Art,uid-1,2016\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                assert_playaevents_art_csv(path)
            self.assertIn("Artelier", str(ctx.exception))

    def test_accept_playaevents_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PlayaEvents-2099_ART.csv"
            path.write_text(
                "Title,Description,Link,UID\n"
                "A,Desc,http://burningman.org/event/brc/2099-art-installations/#A,uid-a\n",
                encoding="utf-8",
            )
            assert_playaevents_art_csv(path)

    def test_prepare_does_not_overwrite_www_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scraper"
            root.mkdir()
            year = 2097
            www_dir = default_www_dir(root)
            www_dir.mkdir(parents=True)
            library = www_dir / f"PlayaEvents-{year}_ART.csv"
            original = (
                "Title,Description,Link,UID\n"
                f"LibraryPiece,KeepMe,http://burningman.org/event/brc/{year}-art-installations/#L,uid-lib\n"
            )
            library.write_text(original, encoding="utf-8")
            before = library.read_bytes()

            job = root / "job"
            job.mkdir()
            upload = job / "upload.csv"
            upload.write_text(
                "Title,Description,Link,UID\n"
                f"UploadPiece,Desc,http://burningman.org/event/brc/{year}-art-installations/#U,uid-up\n",
                encoding="utf-8",
            )

            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({"project_count": 1, "upload_ready_count": 1}),
                encoding="utf-8",
            )
            view_path = root / "view.json"
            view_path.write_text(json.dumps({"upload_checklist": {}}), encoding="utf-8")

            with mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_verification",
                return_value=0,
            ) as verify_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_identity_resolution"
            ), mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_ingest",
                return_value={"summary": summary_path, "view": view_path},
            ) as ingest_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.load_config",
                return_value=mock.Mock(user_agent="test"),
            ):
                result = run_prepare_pipeline(
                    project_root=root,
                    art_path=upload,
                    original_filename=f"PlayaEvents-{year}_ART.csv",
                    confirm_overwrite=True,
                    run_identity_online=False,
                )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result.get("www_library_untouched"))
            self.assertEqual(result.get("saved_www_path"), "")
            self.assertEqual(library.read_bytes(), before)
            # Verify/ingest must use job-local staging, not the WWW library path.
            verify_www = verify_mock.call_args.kwargs["www_dir"]
            self.assertEqual(Path(verify_www).resolve(), job.resolve())
            ingest_www = ingest_mock.call_args.kwargs["www_file"]
            self.assertEqual(Path(ingest_www).resolve(), (job / f"PlayaEvents-{year}_ART.csv").resolve())
            self.assertNotEqual(Path(ingest_www).resolve(), library.resolve())


if __name__ == "__main__":
    unittest.main()
