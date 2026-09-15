from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .database import Database


PACKAGE_FORMAT_VERSION = 1
PACKAGE_EXTENSION = ".pvault"
DATABASE_MEMBER = "vault.sqlite"
MANIFEST_MEMBER = "manifest.json"
CHECKSUMS_MEMBER = "checksums.sha256"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_UNPACKED_BYTES = 6 * 1024 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 200_000


class ResourcePackageError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ResourcePackageError(f"资源文件超出数据目录: {path.name}") from exc
        yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sqlite_snapshot(source_path: Path, target_path: Path, api_key: str = "") -> None:
    if not source_path.is_file():
        raise ResourcePackageError("论文库数据库不存在")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
            target.execute(
                "INSERT INTO settings(key, value) VALUES ('api_key', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (api_key,),
            )
            target.commit()
        finally:
            target.close()
            source.close()
    except sqlite3.Error as exc:
        raise ResourcePackageError(f"创建数据库快照失败: {exc}") from exc


def _database_counts(path: Path) -> dict[str, int]:
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"papers", "settings"}
        missing = sorted(required - tables)
        if missing:
            raise ResourcePackageError(f"数据库缺少必要表: {', '.join(missing)}")
        def count(table: str, where: str = "") -> int:
            if table not in tables:
                return 0
            suffix = f" WHERE {where}" if where else ""
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}{suffix}").fetchone()[0])

        counts = {
            "papers": count("papers"),
            "active_papers": count("papers", "deleted_at IS NULL"),
            "trash_papers": count("papers", "deleted_at IS NOT NULL"),
            "vocabulary": count("vocabulary_entries"),
            "notes": count("paper_notes"),
            "note_quotes": count("note_quotes"),
            "annotations": count("paper_annotations"),
            "summary_variants": count("summary_variants"),
        }
        return counts
    except sqlite3.Error as exc:
        raise ResourcePackageError(f"读取数据库统计失败: {exc}") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass


def _source_members(data_dir: Path, include_assets: bool, include_models: bool) -> list[tuple[str, Path]]:
    members: list[tuple[str, Path]] = []
    for root_name in ("uploads",):
        root = data_dir / root_name
        members.extend((f"{root_name}/{path.relative_to(root).as_posix()}", path) for path in _iter_files(root))
    if include_assets:
        root = data_dir / "assets"
        members.extend((f"assets/{path.relative_to(root).as_posix()}", path) for path in _iter_files(root))
    if include_models:
        root = data_dir / "models"
        members.extend((f"models/{path.relative_to(root).as_posix()}", path) for path in _iter_files(root))
    return members


def _zip_compression(path: str) -> int:
    suffix = Path(path).suffix.casefold()
    if suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bin", ".model", ".safetensors", ".ct2"}:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def estimate_resource_package(data_dir: Path) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    database = data_dir / "paper-vault.db"
    counts = _database_counts(database)
    upload_members = _source_members(data_dir, False, False)
    asset_members = _source_members(data_dir, True, False)
    full_members = _source_members(data_dir, True, True)
    db_size = database.stat().st_size

    def total(members: list[tuple[str, Path]]) -> int:
        return db_size + sum(path.stat().st_size for _, path in members)

    return {
        "counts": counts,
        "standard_bytes": total(upload_members),
        "offline_full_bytes": total(full_members),
        "assets_bytes": sum(path.stat().st_size for _, path in asset_members if _.startswith("assets/")),
        "models_bytes": sum(path.stat().st_size for _, path in full_members if _.startswith("models/")),
        "pdf_files": len(upload_members),
    }


def export_resource_package(
    data_dir: Path,
    *,
    mode: str = "standard",
    app_version: str = "",
    schema_version: int = 0,
    output_dir: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    if mode not in {"standard", "offline_full"}:
        raise ResourcePackageError("资源包模式无效")
    data_dir = data_dir.resolve()
    output_dir = (output_dir or data_dir / "backups").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package_id = str(uuid.uuid4())
    created_at = utc_now()
    package_name = f"PaperVault-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{package_id[:8]}{PACKAGE_EXTENSION}"
    package_path = output_dir / package_name
    include_assets = mode == "offline_full"
    include_models = mode == "offline_full"
    source_database = data_dir / "paper-vault.db"

    with tempfile.TemporaryDirectory(prefix=".pvault-export-", dir=output_dir) as temporary:
        snapshot = Path(temporary) / DATABASE_MEMBER
        _copy_sqlite_snapshot(source_database, snapshot, api_key="")
        counts = _database_counts(snapshot)
        members = [(DATABASE_MEMBER, snapshot)] + _source_members(data_dir, include_assets, include_models)
        manifest_files: list[dict[str, Any]] = []
        for archive_name, source_path in members:
            manifest_files.append(
                {
                    "path": archive_name,
                    "size": source_path.stat().st_size,
                    "sha256": _sha256(source_path),
                }
            )
        manifest = {
            "format_version": PACKAGE_FORMAT_VERSION,
            "package_id": package_id,
            "created_at": created_at,
            "app_version": app_version,
            "schema_version": schema_version,
            "mode": mode,
            "includes": {"database": True, "pdfs": True, "assets": include_assets, "models": include_models, "api_key": False},
            "counts": counts,
            "files": manifest_files,
        }
        checksums = "\n".join(f"{item['sha256']}  {item['path']}" for item in manifest_files) + "\n"
        try:
            with zipfile.ZipFile(package_path, "w", allowZip64=True) as archive:
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                for archive_name, source_path in members:
                    archive.write(source_path, archive_name, compress_type=_zip_compression(archive_name))
                archive.writestr(CHECKSUMS_MEMBER, checksums)
        except (OSError, zipfile.BadZipFile) as exc:
            package_path.unlink(missing_ok=True)
            raise ResourcePackageError(f"生成资源包失败: {exc}") from exc
    return package_path, manifest


def _safe_member_name(name: str) -> str:
    normalized = str(name)
    path = PurePosixPath(normalized)
    if (
        not path.parts
        or "\\" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or path.parts[0] == "~"
        or path.as_posix() != normalized.rstrip("/")
        or any(":" in part or part.endswith((" ", ".")) for part in path.parts)
    ):
        raise ResourcePackageError("资源包包含不安全的文件路径")
    return path.as_posix() + ("/" if normalized.endswith("/") else "")


def _validate_database_member(archive: zipfile.ZipFile) -> None:
    with tempfile.TemporaryDirectory(prefix=".pvault-inspect-") as temporary:
        database = Path(temporary) / DATABASE_MEMBER
        with archive.open(DATABASE_MEMBER) as source, database.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        try:
            connection = sqlite3.connect(database)
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise ResourcePackageError("资源包数据库完整性校验失败")
            _database_counts(database)
        except sqlite3.Error as exc:
            raise ResourcePackageError(f"资源包数据库无法打开: {exc}") from exc
        finally:
            try:
                connection.close()
            except UnboundLocalError:
                pass
        try:
            # Check the actual database schema, not only the version claimed by
            # the manifest, before replacing the current library.
            Database(database).initialize()
        except (RuntimeError, sqlite3.Error) as exc:
            raise ResourcePackageError(f"资源包数据库与当前应用不兼容: {exc}") from exc


def inspect_resource_package(package_path: Path, *, max_schema_version: int | None = None) -> dict[str, Any]:
    package_path = package_path.resolve()
    if not package_path.is_file():
        raise ResourcePackageError("资源包文件不存在")
    if package_path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ResourcePackageError("资源包超过 2 GB，已停止恢复")
    try:
        archive = zipfile.ZipFile(package_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ResourcePackageError("资源包不是有效的 PaperVault 文件") from exc
    try:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_MEMBERS:
            raise ResourcePackageError("资源包文件数量过多")
        names: set[str] = set()
        unpacked_total = 0
        for info in infos:
            name = _safe_member_name(info.filename)
            if name in names:
                raise ResourcePackageError("资源包包含重复文件")
            names.add(name)
            if info.is_dir():
                continue
            if name not in {MANIFEST_MEMBER, DATABASE_MEMBER, CHECKSUMS_MEMBER} and not name.startswith(("uploads/", "assets/", "models/")):
                raise ResourcePackageError("资源包包含不支持的文件路径")
            unpacked_total += int(info.file_size)
            if unpacked_total > MAX_UNPACKED_BYTES:
                raise ResourcePackageError("资源包解压体积超过 6 GB")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ResourcePackageError("资源包不支持符号链接")
        if MANIFEST_MEMBER not in names or DATABASE_MEMBER not in names or CHECKSUMS_MEMBER not in names:
            raise ResourcePackageError("资源包缺少必要文件")
        try:
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResourcePackageError("资源包清单格式无效") from exc
        if not isinstance(manifest, dict) or manifest.get("format_version") != PACKAGE_FORMAT_VERSION:
            raise ResourcePackageError("资源包版本不受当前应用支持")
        if max_schema_version is not None:
            try:
                package_schema_version = int(manifest.get("schema_version", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ResourcePackageError("资源包数据库版本无效") from exc
            if package_schema_version > max_schema_version:
                raise ResourcePackageError("资源包由更新版本的 PaperVault 生成，请先升级应用")
        file_records = manifest.get("files")
        if not isinstance(file_records, list) or not file_records:
            raise ResourcePackageError("资源包清单没有文件记录")
        listed: set[str] = set()
        for record in file_records:
            if not isinstance(record, dict):
                raise ResourcePackageError("资源包文件记录无效")
            name = _safe_member_name(str(record.get("path", "")))
            if name in listed or name not in names or name in {MANIFEST_MEMBER, CHECKSUMS_MEMBER}:
                raise ResourcePackageError("资源包文件清单不一致")
            try:
                expected_size = int(record["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ResourcePackageError("资源包文件大小无效") from exc
            if expected_size != archive.getinfo(name).file_size or not re_full_sha256(str(record.get("sha256", ""))):
                raise ResourcePackageError("资源包校验信息无效")
            listed.add(name)
            with archive.open(name) as source:
                digest = hashlib.sha256()
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != str(record["sha256"]):
                    raise ResourcePackageError(f"资源包文件校验失败: {name}")
        if listed != {name for name in names if name not in {MANIFEST_MEMBER, CHECKSUMS_MEMBER} and not name.endswith("/") }:
            raise ResourcePackageError("资源包包含未登记文件")
        _validate_database_member(archive)
        manifest["package_size"] = package_path.stat().st_size
        return manifest
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ResourcePackageError(f"资源包文件无法读取: {exc}") from exc
    finally:
        archive.close()


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def restore_resource_package(
    package_path: Path,
    data_dir: Path,
    *,
    preserve_api_key: str = "",
    app_version: str = "",
    schema_version: int = 0,
    backup_output_dir: Path | None = None,
) -> dict[str, Any]:
    manifest = inspect_resource_package(package_path, max_schema_version=schema_version or None)
    data_dir = data_dir.resolve()
    parent = data_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{data_dir.name}.restore-", dir=parent))
    old_data = parent / f".{data_dir.name}.before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    auto_backup: Path | None = None
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            for info in archive.infolist():
                name = _safe_member_name(info.filename)
                if name in {MANIFEST_MEMBER, CHECKSUMS_MEMBER} or info.is_dir():
                    continue
                target = stage.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        for directory in ("uploads", "assets", "models", "backups/incoming"):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        staged_database = stage / "paper-vault.db"
        extracted_database = stage / DATABASE_MEMBER
        extracted_database.rename(staged_database)
        connection = sqlite3.connect(staged_database)
        try:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES ('api_key', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(preserve_api_key or ""),),
            )
            connection.commit()
        finally:
            connection.close()
        try:
            Database(staged_database).initialize()
        except (RuntimeError, sqlite3.Error) as exc:
            raise ResourcePackageError(f"资源包数据库与当前应用不兼容: {exc}") from exc
        _database_counts(staged_database)

        if data_dir.is_dir() and (data_dir / "paper-vault.db").is_file():
            try:
                auto_backup, _ = export_resource_package(
                    data_dir,
                    mode="standard",
                    app_version=app_version,
                    schema_version=schema_version,
                    output_dir=backup_output_dir or parent / "PaperVault-backups",
                )
            except ResourcePackageError:
                auto_backup = None
        if data_dir.exists():
            data_dir.rename(old_data)
        stage.rename(data_dir)
    except Exception:
        if not data_dir.exists() and old_data.exists():
            old_data.rename(data_dir)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "manifest": manifest,
        "auto_backup": auto_backup.name if auto_backup else "",
        "previous_data_dir": old_data.name if old_data.exists() else "",
    }
