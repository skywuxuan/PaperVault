from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Any, Sequence

from backend import __version__
from backend.app import PaperVaultServer
from backend.config import load_local_environment

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


class DesktopCloseGuard:
    """Keep the native window alive until the current note is safely saved."""

    SAVE_SCRIPT = """
        (async () => {
            if (typeof flushNoteSave !== 'function') return true;
            const fields = ['noteTitleInput', 'noteBodyInput'].map(id => document.getElementById(id)).filter(Boolean);
            const disabled = fields.map(field => field.disabled);
            fields.forEach(field => { field.disabled = true; });
            try {
                if (typeof setBusy === 'function') setBusy(true, '正在保存笔记，完成后关闭…');
                do { await flushNoteSave(); } while (state.noteDraft);
                return true;
            } catch (error) {
                fields.forEach((field, index) => { field.disabled = disabled[index]; });
                if (typeof setBusy === 'function') setBusy(false);
                if (typeof handleError === 'function') handleError(error);
                return false;
            }
        })()
    """

    def __init__(self, window: Any):
        self.window = window
        self._approved = False
        self._pending = False
        self._lock = threading.Lock()

    def request_close(self) -> bool:
        with self._lock:
            if self._approved or not self.window.events.loaded.is_set():
                return True
            if not self._pending:
                self._pending = True
                threading.Thread(target=self._save, name="papervault-save-on-close", daemon=True).start()
        # The native UI thread must remain free to evaluate JavaScript and send
        # the save request. The completion callback closes the window later.
        return False

    def _save(self) -> None:
        try:
            self.window.evaluate_js(self.SAVE_SCRIPT, callback=self._saved)
        except Exception:
            # Preserve the window and allow a later close attempt after a
            # temporary WebView error; never discard a note on an eval failure.
            with self._lock:
                self._pending = False

    def _saved(self, success: Any) -> None:
        with self._lock:
            self._pending = False
            if success is not True:
                return
            self._approved = True
        self.window.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperVault desktop application")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def run_desktop(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resource_root()
    load_local_environment(root)
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
        window = webview.create_window(
            f"{WINDOW_TITLE} · {__version__}",
            runtime.url,
            js_api=DesktopBridge(platform),
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            resizable=True,
            background_color="#f6f6fa",
            text_select=True,
        )
        close_guard = DesktopCloseGuard(window)
        window.events.closing += close_guard.request_close
        window.events.closed += runtime.stop
        webview.start(
            gui=platform.renderer,
            debug=args.debug,
            private_mode=False,
            storage_path=str(platform.webview_storage_dir),
        )
    finally:
        runtime.stop()
    return 0


def main() -> None:
    raise SystemExit(run_desktop())


if __name__ == "__main__":
    main()
