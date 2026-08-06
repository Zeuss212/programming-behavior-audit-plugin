import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import tornado.web

from myextension.routes import _contents_os_path


class SyntheticContentsManager:
    def __init__(
        self,
        root: Path,
        *,
        model_type: str = "file",
    ) -> None:
        self.root_dir = str(root)
        self.model_type = model_type
        self.awaited = False

    async def get(self, path, content=False):
        assert content is False
        await asyncio.sleep(0)
        self.awaited = True
        candidate = Path(self.root_dir) / path
        if not candidate.exists():
            raise tornado.web.HTTPError(404)
        return {"path": path, "type": self.model_type}

    def _get_os_path(self, path):
        return str(Path(self.root_dir) / path)


def handler_for(contents_manager):
    return SimpleNamespace(
        contents_manager=contents_manager,
        settings={},
    )


@pytest.mark.asyncio
async def test_contents_path_awaits_manager_and_accepts_root_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "student.py"
    source.write_text("print('synthetic')\n", encoding="utf-8")
    manager = SyntheticContentsManager(root)

    resolved = await _contents_os_path(handler_for(manager), "student.py")

    assert manager.awaited is True
    assert resolved == source.resolve()


@pytest.mark.asyncio
async def test_contents_path_rejects_symlink_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(
        "raise AssertionError('outside script must not run')\n",
        encoding="utf-8",
    )
    (root / "linked.py").symlink_to(outside)

    with pytest.raises(ValueError, match="Jupyter 根目录"):
        await _contents_os_path(
            handler_for(SyntheticContentsManager(root)),
            "linked.py",
        )


@pytest.mark.asyncio
async def test_contents_path_rejects_directory_model(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    directory = root / "folder.py"
    directory.mkdir()
    manager = SyntheticContentsManager(root, model_type="directory")

    with pytest.raises(ValueError, match="普通 Python 文件"):
        await _contents_os_path(handler_for(manager), "folder.py")

    assert manager.awaited is True


@pytest.mark.asyncio
async def test_contents_path_maps_missing_file_to_closed_not_found(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    manager = SyntheticContentsManager(root)

    with pytest.raises(OSError, match="not found"):
        await _contents_os_path(handler_for(manager), "missing.py")

    assert manager.awaited is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "manager",
    [
        SimpleNamespace(
            get=SyntheticContentsManager.get,
            _get_os_path=lambda _path: "synthetic.py",
        ),
        SimpleNamespace(
            root_dir="/synthetic-root",
            get=SyntheticContentsManager.get,
        ),
    ],
)
async def test_contents_path_rejects_nonlocal_contents_manager(manager):
    with pytest.raises(ValueError, match="不支持本地运行"):
        await _contents_os_path(handler_for(manager), "student.py")
