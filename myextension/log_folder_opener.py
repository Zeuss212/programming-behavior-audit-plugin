from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal


class LogFolderOpenUnsupportedError(RuntimeError):
    """The server platform has no supported local desktop opener."""


class LogFolderOpenError(RuntimeError):
    """The fixed sessions directory could not be opened safely."""


class LogFolderOpener:
    def __init__(
        self,
        root: Path,
        *,
        platform: str | None = None,
        command_runner: Callable = subprocess.run,
        windows_startfile: Callable[[str, str], object] | None = None,
    ) -> None:
        self._root = Path(root).expanduser().resolve()
        self._platform = sys.platform if platform is None else platform
        self._command_runner = command_runner
        self._windows_startfile = windows_startfile

    def open_sessions_folder(self) -> Literal["macos", "windows"]:
        target = self._safe_sessions_directory()
        try:
            if self._platform == "darwin":
                self._command_runner(
                    ["open", str(target)],
                    check=True,
                    shell=False,
                    timeout=5,
                )
                return "macos"
            if self._platform.startswith("win"):
                startfile = self._windows_startfile or getattr(os, "startfile", None)
                if startfile is None:
                    raise LogFolderOpenUnsupportedError(
                        "Windows desktop opener is unavailable."
                    )
                startfile(str(target), "open")
                return "windows"
            raise LogFolderOpenUnsupportedError("Platform is unsupported.")
        except LogFolderOpenUnsupportedError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise LogFolderOpenError(
                "Sessions directory could not be opened."
            ) from error

    def _safe_sessions_directory(self) -> Path:
        sessions = self._root / "sessions"
        try:
            if sessions.is_symlink():
                raise LogFolderOpenError("Sessions directory must not be a symlink.")
            sessions.mkdir(mode=0o700, exist_ok=True)
            target = sessions.resolve(strict=True)
            target.relative_to(self._root)
            if not target.is_dir():
                raise LogFolderOpenError("Sessions path is not a directory.")
            return target
        except LogFolderOpenError:
            raise
        except (OSError, ValueError) as error:
            raise LogFolderOpenError(
                "Sessions directory could not be prepared safely."
            ) from error
