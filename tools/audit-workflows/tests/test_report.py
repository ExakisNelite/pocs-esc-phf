import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.collect import Collector
from lib.demo import demo_client
from lib.github import local_path
from lib.report import csv_text, generate_reports


HOME = Path(__file__).resolve().parents[1]
CONFIG = {"org": "example", "library": "example/workflows", "apiUrl": "https://api.github.com", "apiVersion": "2026-03-10", "maxDepth": 10, "maxEdges": 1000, "maxFileBytes": 2 * 1024 * 1024}


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def audit(self):
        client, expected = demo_client(self.directory / "api", CONFIG)
        return Collector(client=client, directory=self.directory, config=CONFIG).collect(expected)

    def test_pipeline_retains_versions_routes_and_notes(self):
        audit = self.audit()
        self.assertEqual(audit["organizationCoverage"], "not_verified")
        self.assertEqual(next(repo for repo in audit["coverage"] if repo["id"] == 2)["status"], "partial")
        self.assertIsNone(next(repo for repo in audit["coverage"] if repo["id"] == 3)["referenceCount"])
        self.assertTrue(any(edge.get("resolution", {}).get("kind") == "tag" for edge in audit["edges"]))
        self.assertTrue(any(edge.get("resolution", {}).get("kind") == "sha" for edge in audit["edges"]))
        self.assertTrue(any(edge["depth"] == 2 for edge in audit["edges"]))
        self.assertEqual(generate_reports(self.directory, audit), {"workflowDocuments": 3, "libraryReferences": 5})
        documents = list((self.directory / "docs" / "workflows").glob("*.md"))
        self.assertTrue(any(path.name.startswith("build.yml-") for path in documents))
        self.assertTrue(any(path.name.startswith("build.yaml-") for path in documents))
        self.assertFalse(any("npm ci" in path.read_text() for path in documents))
        self.assertTrue(any("referenced step is absent" in path.read_text() for path in documents))
        notes = self.directory / "review-notes.csv"
        notes.write_text("human decision", encoding="utf-8")
        generate_reports(self.directory, audit)
        self.assertEqual(notes.read_text(), "human decision")

    def test_markdown_and_csv_safeguards(self):
        audit = self.audit()
        audit["nodes"][0]["name"] = "<script>alert(1)</script> | test"
        audit["edges"][0]["raw"] = '=HYPERLINK("bad")'
        generate_reports(self.directory, audit)
        documents = "\n".join(path.read_text() for path in (self.directory / "docs" / "workflows").glob("*.md"))
        self.assertNotIn("<script>", documents)
        self.assertIn("'=HYPERLINK", (self.directory / "reports" / "consumer-references.csv").read_text())
        self.assertIn("'@bad", csv_text([["@bad"]]))

    def test_cli_demo_resume_report_without_credentials(self):
        environment = {**os.environ, "GITHUB_TOKEN": "", "GH_TOKEN": ""}
        for command in ("demo", "resume", "report"):
            result = subprocess.run([sys.executable, str(HOME / "audit.py"), command, "--run-dir", str(self.directory)],
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("3 workflow documents", result.stdout)
        audit = json.loads((self.directory / "data" / "audit.json").read_text())
        self.assertEqual(audit["mode"], "offline_demo")
        self.assertFalse((self.directory / ".audit.lock").exists())

    def test_existing_node_results_remain_readable_and_resumable(self):
        source = HOME / "results" / "demo"
        if not (source / "data" / "run-manifest.json").exists():
            self.skipTest("Legacy Node demo is optional and is not committed")
        shutil.copytree(local_path(source), self.directory, dirs_exist_ok=True)
        before = json.loads((self.directory / "data" / "audit.json").read_text())
        documents_before = {path.name for path in (self.directory / "docs" / "workflows").glob("*.md")}
        for command in ("report", "resume"):
            result = subprocess.run([sys.executable, str(HOME / "audit.py"), command, "--run-dir", str(self.directory)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads((self.directory / "data" / "audit.json").read_text())
        self.assertEqual(before["rootIdentities"], after["rootIdentities"])
        self.assertEqual(before["coverage"], after["coverage"])
        self.assertEqual(documents_before, {path.name for path in (self.directory / "docs" / "workflows").glob("*.md")})

    def test_real_run_exit_codes_and_lock_cleanup_with_simulated_client(self):
        specification = importlib.util.spec_from_file_location("audit_cli", HOME / "audit.py")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        config_path = self.directory / "config.json"
        config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
        run_directory = self.directory / "run"
        client, expected = demo_client(run_directory / "data" / "raw" / "api", CONFIG)
        with patch.object(module, "GitHub", return_value=client):
            self.assertEqual(module.main(["run", "--config", str(config_path), "--run-dir", str(run_directory)]), 2)
        self.assertFalse((run_directory / ".audit.lock").exists())
        manifest = json.loads((run_directory / "data" / "run-manifest.json").read_text())
        self.assertNotIn("tokenCommand", manifest["config"])
        with patch.object(module, "GitHub", return_value=client), patch.object(module.Collector, "collect", side_effect=ValueError("simulated failure")):
            with self.assertRaises(ValueError):
                module.main(["resume", "--run-dir", str(run_directory)])
        self.assertFalse((run_directory / ".audit.lock").exists())


if __name__ == "__main__":
    unittest.main()