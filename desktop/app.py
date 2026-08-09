from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Any, Sequence

from backend import __version__
from backend.app import PaperVaultServer

from .platforms import DesktopPlatform, get_desktop_platform, resource_root


WINDOW_TITLE = "PaperVault"
WINDOW_SIZE = (1440, 900)
WINDOW_MIN_SIZE = (1024, 700)


class BackendRuntime:
    """Own the localhost API server used by the native window."""

    def __init__(self, frontend_dir: Path, data_dir: Path, port: int = 0):
        self.server = PaperVaultServer(
            ("127.0.0.1", port), frontend_dir.resolve(), data_dir.resolve()
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="papervault-http",
            daemon=True,
        )
        self._stopped = False
        self._stop_lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self, *_: Any) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=5)
        self.server.server_close()


class DesktopBridge:
    """Small stable bridge for UI capabilities that need native context."""

    def __init__(self, platform: DesktopPlatform):
        self.platform = platform

    def app_info(self) -> dict[str, str]:
        return {
            "name": WINDOW_TITLE,
            "version": __version__,
            "platform": self.platform.name,
            "data_dir": str(self.platform.data_dir),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperVault desktop application")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def run_desktop(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform = get_desktop_platform()
    if args.data_dir is not None:
        data_dir = args.data_dir.expanduser().resolve()
        platform = DesktopPlatform(
            name=platform.name,
            renderer=platform.renderer,
            data_dir=data_dir,
            webview_storage_dir=data_dir / "webview",
            icon_name=platform.icon_name,
        )

    root = resource_root()
    frontend_dir = root / "frontend"
    if not (frontend_dir / "index.html").is_file():
        raise RuntimeError(f"PaperVault frontend resources are missing: {frontend_dir}")

    try:
        import webview
    except ImportError as error:
        raise RuntimeError(
            "Desktop dependencies are missing. Install requirements-desktop.txt first."
        ) from error

    platform.data_dir.mkdir(parents=True, exist_ok=True)
    platform.webview_storage_dir.mkdir(parents=True, exist_ok=True)
    runtime = BackendRuntime(frontend_dir, platform.data_dir, args.port)
    runtime.start()
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        icon_path = root / "desktop" / "assets" / platform.icon_name
        window = webview.create_window(
            WINDOW_TITLE,
            runtime.url,
            js_api=DesktopBridge(platform),
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            resizable=True,
            background_color="#f4f6f5",
            text_select=True,
        )
        window.events.closed += runtime.stop
        webview.start(
            gui=platform.renderer,
            debug=args.debug,
            private_mode=False,
            storage_path=str(platform.webview_storage_dir),
            icon=str(icon_path) if icon_path.is_file() else None,
        )
    finally:
        runtime.stop()
    return 0


def main() -> None:
    raise SystemExit(run_desktop())


if __name__ == "__main__":
    main()
