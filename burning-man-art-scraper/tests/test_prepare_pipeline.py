import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.aggregator_hub.prepare import run_prepare_pipeline, year_has_outputs


class PreparePipelineTests(unittest.TestCase):
    def test_needs_confirm_when_outputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2095
            verify = root / "data" / "verification" / str(year)
            verify.mkdir(parents=True)
            (verify / f"verification_report_{year}.csv").write_text("year\n", encoding="utf-8")
            art = root / "upload.csv"
            art.write_text(
                "Title,Description,Link,UID\n"
                f"A,Desc,http://burningman.org/event/brc/{year}-art-installations/#A,uid-a\n",
                encoding="utf-8",
            )
            self.assertTrue(year_has_outputs(root, year))
            result = run_prepare_pipeline(
                project_root=root,
                art_path=art,
                original_filename=f"PlayaEvents-{year}_ART.csv",
                confirm_overwrite=False,
                run_identity_online=False,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["needs_confirm"])

    def test_prepare_runs_verify_then_ingest_without_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2096
            art = root / "upload.csv"
            art.write_text(
                "Title,Description,Link,UID\n"
                f"A,Desc,http://burningman.org/event/brc/{year}-art-installations/#A,uid-a\n",
                encoding="utf-8",
            )
            fake_summary = {
                "project_count": 1,
                "upload_ready_count": 1,
            }
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(fake_summary), encoding="utf-8")
            view_path = root / "view.json"
            view_path.write_text(json.dumps({"upload_checklist": {"project_count": 1}}), encoding="utf-8")

            with mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_verification",
                return_value=0,
            ) as verify_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_identity_resolution"
            ) as identity_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_ingest",
                return_value={"summary": summary_path, "view": view_path, "upload": root / "u.csv"},
            ) as ingest_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.load_config",
                return_value=mock.Mock(user_agent="test"),
            ):
                result = run_prepare_pipeline(
                    project_root=root,
                    art_path=art,
                    original_filename=f"PlayaEvents-{year}_ART.csv",
                    confirm_overwrite=True,
                    run_identity_online=False,
                )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["year"], year)
            self.assertFalse(result["identity_online_ran"])
            verify_mock.assert_called_once()
            identity_mock.assert_not_called()
            ingest_mock.assert_called_once()
            self.assertEqual(result.get("saved_www_path"), "")
            self.assertTrue(result.get("www_library_untouched"))
            staged = art.parent / f"PlayaEvents-{year}_ART.csv"
            self.assertTrue(staged.exists())
            self.assertEqual(
                Path(ingest_mock.call_args.kwargs["www_file"]).resolve(),
                staged.resolve(),
            )

    def test_prepare_calls_identity_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2094
            art = root / "upload.csv"
            art.write_text(
                "Title,Description,Link,UID\n"
                f"A,Desc,http://burningman.org/event/brc/{year}-art-installations/#A,uid-a\n",
                encoding="utf-8",
            )
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps({"project_count": 1, "upload_ready_count": 0}), encoding="utf-8")
            view_path = root / "view.json"
            view_path.write_text(json.dumps({"upload_checklist": {}}), encoding="utf-8")

            with mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_verification",
                return_value=0,
            ), mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_identity_resolution",
                return_value=0,
            ) as identity_mock, mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.run_ingest",
                return_value={"summary": summary_path, "view": view_path},
            ), mock.patch(
                "burning_man_scraper.aggregator_hub.prepare.load_config",
                return_value=mock.Mock(user_agent="test"),
            ):
                result = run_prepare_pipeline(
                    project_root=root,
                    art_path=art,
                    original_filename=f"PlayaEvents-{year}_ART.csv",
                    confirm_overwrite=True,
                    run_identity_online=True,
                    identity_limit=10,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["identity_online_ran"])
            identity_mock.assert_called_once()
            kwargs = identity_mock.call_args.kwargs
            self.assertTrue(kwargs["enable_search"])
            self.assertEqual(kwargs["limit"], 10)


if __name__ == "__main__":
    unittest.main()
