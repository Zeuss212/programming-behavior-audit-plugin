from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from myextension.log_folder_opener import (
    LogFolderOpenError,
    LogFolderOpenUnsupportedError,
    LogFolderOpener,
)


def test_macos_opens_only_sessions_without_shell(tmp_path: Path) -> None:
    calls: list[tuple[object, object]] = []

    def run(args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return object()

    platform = LogFolderOpener(
        tmp_path,
        platform="darwin",
        command_runner=run,
    ).open_sessions_folder()

    assert platform == "macos"
    assert calls == [
        (
            ["open", str((tmp_path / "sessions").resolve())],
            {"check": True, "shell": False, "timeout": 5},
        )
    ]
    assert (tmp_path / "sessions").is_dir()


def test_windows_uses_startfile_open_action(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def startfile(path: str, operation: str) -> None:
        calls.append((path, operation))

    platform = LogFolderOpener(
        tmp_path,
        platform="win32",
        windows_startfile=startfile,
    ).open_sessions_folder()

    assert platform == "windows"
    assert calls == [(str((tmp_path / "sessions").resolve()), "open")]


def test_linux_is_unsupported(tmp_path: Path) -> None:
    with pytest.raises(LogFolderOpenUnsupportedError):
        LogFolderOpener(tmp_path, platform="linux").open_sessions_folder()


@pytest.mark.parametrize(
    "runner_error",
    [
        subprocess.TimeoutExpired(["open", "/tmp/sessions"], 5),
        subprocess.CalledProcessError(1, ["open", "/tmp/sessions"]),
    ],
)
def test_macos_runner_errors_are_normalized(
    tmp_path: Path, runner_error: subprocess.SubprocessError
) -> None:
    def run(*args: object, **kwargs: object) -> object:
        raise runner_error

    with pytest.raises(LogFolderOpenError):
        LogFolderOpener(
            tmp_path,
            platform="darwin",
            command_runner=run,
        ).open_sessions_folder()


def test_windows_startfile_os_error_is_normalized(tmp_path: Path) -> None:
    def startfile(path: str, operation: str) -> None:
        raise OSError("Explorer is unavailable")

    with pytest.raises(LogFolderOpenError):
        LogFolderOpener(
            tmp_path,
            platform="win32",
            windows_startfile=startfile,
        ).open_sessions_folder()


def test_sessions_symlink_outside_root_is_rejected_without_changing_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    (tmp_path / "sessions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LogFolderOpenError):
        LogFolderOpener(tmp_path, platform="darwin").open_sessions_folder()

    assert sentinel.read_text(encoding="utf-8") == "must remain"
    assert (tmp_path / "sessions").is_symlink()


def test_new_sessions_directory_is_not_accessible_to_group_or_other(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"

    LogFolderOpener(
        tmp_path,
        platform="darwin",
        command_runner=lambda *args, **kwargs: object(),
    ).open_sessions_folder()

    assert stat.S_IMODE(sessions.stat().st_mode) & 0o077 == 0
