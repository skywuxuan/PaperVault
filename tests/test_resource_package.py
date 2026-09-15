from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
import zipfile
import http.client
import json
import threading
from pathlib import Path

from backend.database import Database, utc_now
from backend.app import PaperVaultServer
from backend.resource_package import (
    ResourcePackageError,
    export_resource_package,
    inspect_resource_package,
    restore_resource_package,
)


class ResourcePackageTestCase(unittest.TestCase):
    def make_data_dir(self, root: Path, title: str) -> tuple[Path, str]:
        data_dir = root / f"data-{uuid.uuid4().hex[:8]}"
        data_dir.mkdir(parents=True)
        database = Database(data_dir / "paper-vault.db")
        database.initialize()
        paper_id = str(uuid.uuid4())
        now = utc_now()
        database.insert_paper(
            {
                "id": paper_id,
                "title": title,
                "original_filename": f"{title}.pdf",
                "stored_filename": f"{paper_id}.pdf",
                "created_at": now,
                "updated_at": now,
            },
            [],
        )
        database.update_settings({"api_key": "local-secret", "model": "test-model"})
        (data_dir / "uploads").mkdir()
        (data_dir / "uploads" / f"{paper_id}.pdf").write_bytes(b"%PDF-1.7\nresource package test")
        return data_dir, paper_id

    def test_export_manifest_and_database_are_keyless(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir, paper_id = self.make_data_dir(Path(directory), "Source")
            output_dir = Path(directory) / "exports"
            package_path, manifest = export_resource_package(
                data_dir,
                mode="standard",
                app_version="test",
                schema_version=4,
                output_dir=output_dir,
            )
            inspected = inspect_resource_package(package_path)
            self.assertEqual(inspected["package_id"], manifest["package_id"])
            self.assertFalse(inspected["includes"]["api_key"])
            self.assertTrue(any(item["path"] == f"uploads/{paper_id}.pdf" for item in inspected["files"]))
            with zipfile.ZipFile(package_path) as archive:
                with tempfile.TemporaryDirectory() as extracted:
                    database_path = Path(extracted) / "vault.sqlite"
                    database_path.write_bytes(archive.read("vault.sqlite"))
                    connection = sqlite3.connect(database_path)
                    try:
                        self.assertEqual(connection.execute("SELECT value FROM settings WHERE key = 'api_key'").fetchone()[0], "")
                    finally:
                        connection.close()

    def test_restore_replaces_data_and_preserves_current_machine_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir, _ = self.make_data_dir(root, "Source")
            target_dir, _ = self.make_data_dir(root, "Target")
            package_path, _ = export_resource_package(source_dir, output_dir=root / "exports")
            result = restore_resource_package(
                package_path,
                target_dir,
                preserve_api_key="target-secret",
                app_version="test",
                schema_version=4,
                backup_output_dir=root / "safety-backups",
            )
            restored = Database(target_dir / "paper-vault.db")
            self.assertEqual(restored.list_papers()[0]["title"], "Source")
            self.assertEqual(restored.get_settings(include_secret=True)["api_key"], "target-secret")
            self.assertTrue(result["auto_backup"])
            self.assertTrue((target_dir.parent / result["previous_data_dir"]).is_dir())

    def test_inspect_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_path = Path(directory) / "bad.pvault"
            for member in ("../escape.txt", "uploads/C:/escape.txt", "uploads/file.pdf:stream", ".", "uploads/./paper.pdf"):
                with self.subTest(member=member):
                    with zipfile.ZipFile(package_path, "w") as archive:
                        archive.writestr("manifest.json", "{}")
                        archive.writestr("vault.sqlite", b"not a database")
                        archive.writestr("checksums.sha256", "")
                        archive.writestr(member, b"unsafe")
                    with self.assertRaises(ResourcePackageError):
                        inspect_resource_package(package_path)

    def test_inspect_accepts_normal_zip_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir, _ = self.make_data_dir(root, "Source")
            package_path, _ = export_resource_package(data_dir, output_dir=root / "exports")
            with zipfile.ZipFile(package_path, "a") as archive:
                archive.writestr("uploads/", b"")
            self.assertEqual(inspect_resource_package(package_path)["counts"]["papers"], 1)

    def test_restore_rejects_incompatible_database_before_replacing_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir, _ = self.make_data_dir(root, "Newer source")
            target_dir, target_paper_id = self.make_data_dir(root, "Current library")
            with Database(source_dir / "paper-vault.db").connect() as connection:
                connection.execute("PRAGMA user_version = 99")
            package_path, _ = export_resource_package(
                source_dir, schema_version=4, output_dir=root / "exports"
            )
            with self.assertRaises(ResourcePackageError):
                restore_resource_package(package_path, target_dir, schema_version=4)
            current = Database(target_dir / "paper-vault.db")
            self.assertEqual(current.get_paper(target_paper_id)["title"], "Current library")
            self.assertEqual(current.get_settings(include_secret=True)["api_key"], "local-secret")


class ResourcePackageApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.temp_dir.name) / "data"
        cls.data_dir.mkdir()
        database = Database(cls.data_dir / "paper-vault.db")
        database.initialize()
        paper_id = str(uuid.uuid4())
        now = utc_now()
        database.insert_paper(
            {
                "id": paper_id,
                "title": "HTTP Package Test",
                "original_filename": "http-package-test.pdf",
                "stored_filename": f"{paper_id}.pdf",
                "created_at": now,
                "updated_at": now,
            },
            [],
        )
        (cls.data_dir / "uploads").mkdir()
        (cls.data_dir / "uploads" / f"{paper_id}.pdf").write_bytes(b"%PDF-1.7\nHTTP package")
        project_dir = Path(__file__).resolve().parent.parent
        cls.server = PaperVaultServer(("127.0.0.1", 0), project_dir / "frontend", cls.data_dir)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp_dir.cleanup()

    def request(self, method: str, path: str, body: bytes = b"", content_type: str = "application/json") -> tuple[int, bytes, http.client.HTTPMessage]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {"Content-Type": content_type, "Content-Length": str(len(body))} if body else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, payload, response.headers)
        connection.close()
        return result

    def test_estimate_export_inspect_and_restore_routes(self) -> None:
        status, payload, _ = self.request("GET", "/api/resource-packages/estimate")
        self.assertEqual(status, 200)
        estimate = json.loads(payload.decode("utf-8"))["estimate"]
        self.assertEqual(estimate["counts"]["active_papers"], 1)
        status, package, headers = self.request(
            "POST",
            "/api/resource-packages/export",
            json.dumps({"mode": "standard"}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/vnd.papervault+zip")
        package_path = Path(self.temp_dir.name) / "download.pvault"
        package_path.write_bytes(package)
        status, inspected_body, _ = self.request(
            "POST", "/api/resource-packages/inspect", package, "application/vnd.papervault+zip"
        )
        self.assertEqual(status, 200)
        inspected = json.loads(inspected_body.decode("utf-8"))
        self.assertEqual(inspected["manifest"]["counts"]["active_papers"], 1)
        status, restored_body, _ = self.request(
            "POST",
            "/api/resource-packages/restore",
            json.dumps({"package_id": inspected["package_id"]}).encode("utf-8"),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(restored_body.decode("utf-8"))["restored"])
        status, next_inspected_body, _ = self.request(
            "POST", "/api/resource-packages/inspect", package, "application/vnd.papervault+zip"
        )
        self.assertEqual(status, 200, next_inspected_body.decode("utf-8"))
        self.assertTrue(json.loads(next_inspected_body.decode("utf-8"))["package_id"])
