"""Graph-aware smoke checks for repository and wheel JavaScript artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


REPOSITORY_LABEXTENSION = Path(__file__).resolve().parents[1] / "labextension"
PACKAGE_JSON = Path(__file__).resolve().parents[2] / "package.json"
PACKAGE_VERSION = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
DELIVERY_WHEEL = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "bluedot"
    / f"release-{PACKAGE_VERSION}"
    / "artifacts"
    / f"myextension-{PACKAGE_VERSION}-py3-none-any.whl"
)

REQUIRED_TASK_11_MARKERS = (
    "myextension:active-session",
    "sessions/start",
    "queue_overflow",
    "receipt_mismatch",
    "upload_network",
)

REQUIRED_TASK_12_MARKERS = (
    "编程行为分析",
    "本次会话结果",
    "分析详情",
    "教师复核",
    "jp-BehaviorAudit-sidebarTab",
    "ai_provider_timeout",
    "ai_response_invalid",
    "最长约 180 秒",
)

FORBIDDEN_STALE_OR_PRIVATE_MARKERS = (
    "[jupyterlab-behavior-audit]",
    "[PASTE-DEBUG",
    "myextension:monitor-enabled",
    "behavior-events",
    "Behavior segment upload queue exceeded 500 segments; dropped the oldest segment.",
)

EXTENSION_EXPOSE_PATTERN = re.compile(
    r'"\./extension":\(\)=>__webpack_require__\.e\((\d+)\)'
    r"\.then\(\(\)=>\(\)=>__webpack_require__\((\d+)\)\)"
)
CHUNK_HASH_MAP_PATTERN = re.compile(
    r'__webpack_require__\.u=e=>""\+e\+"\."\+\(\{([^{}]+)\}\)'
    r'\[e\]\+"\.js'
)
CHUNK_HASH_PATTERN = re.compile(r'(\d+):"([0-9a-f]+)"')


@dataclass(frozen=True)
class Artifact:
    label: str
    files: dict[PurePosixPath, bytes]

    def text(self, relative: PurePosixPath) -> str:
        return self.files[relative].decode("utf-8")

    @property
    def javascript_paths(self) -> tuple[PurePosixPath, ...]:
        return tuple(
            sorted(
                path
                for path in self.files
                if path.parent == PurePosixPath("static") and path.suffix == ".js"
            )
        )


@dataclass(frozen=True)
class ExtensionGraph:
    load_path: PurePosixPath
    extension_chunk_path: PurePosixPath
    chunk_id: int
    module_id: int


def _repository_artifact() -> Artifact:
    files = {
        PurePosixPath(path.relative_to(REPOSITORY_LABEXTENSION).as_posix()):
        path.read_bytes()
        for path in REPOSITORY_LABEXTENSION.rglob("*")
        if path.is_file()
    }
    return Artifact("repository labextension", files)


def _wheel_artifact() -> Artifact:
    assert DELIVERY_WHEEL.is_file(), (
        f"Build the current {PACKAGE_VERSION} wheel before running "
        "the delivery artifact gate."
    )
    with ZipFile(DELIVERY_WHEEL) as wheel:
        manifest_names = [
            PurePosixPath(name)
            for name in wheel.namelist()
            if name.endswith(
                "share/jupyter/labextensions/myextension/package.json"
            )
        ]
        assert len(manifest_names) == 1
        prefix = manifest_names[0].parent
        files: dict[PurePosixPath, bytes] = {}
        for name in wheel.namelist():
            path = PurePosixPath(name)
            if path == prefix or prefix not in path.parents or name.endswith("/"):
                continue
            relative = path.relative_to(prefix)
            assert relative.parts and ".." not in relative.parts
            files[relative] = wheel.read(name)
    return Artifact("delivery wheel", files)


def _assert_wheel_delivery_contents() -> None:
    assert DELIVERY_WHEEL.is_file(), (
        f"Build the current {PACKAGE_VERSION} wheel before running "
        "the delivery artifact gate."
    )
    with ZipFile(DELIVERY_WHEEL) as wheel:
        names = set(wheel.namelist())
    assert not any(name.startswith("myextension/labextension/") for name in names)
    required_prefixes = (
        "myextension/resources/dimension_templates/",
        "myextension/resources/signal_dictionary/",
        "myextension/api_schemas/",
        "myextension/tests/",
    )
    for prefix in required_prefixes:
        assert any(name.startswith(prefix) for name in names), prefix
    data_prefix = f"myextension-{PACKAGE_VERSION}.data/data/"
    assert (
        data_prefix
        + "etc/jupyter/jupyter_server_config.d/myextension.json"
        in names
    )
    assert any(
        name.startswith(
            data_prefix + "share/jupyter/labextensions/myextension/static/"
        )
        for name in names
    )


def _assert_task_11_artifact(artifact: Artifact) -> ExtensionGraph:
    manifest_path = PurePosixPath("package.json")
    assert manifest_path in artifact.files
    manifest = json.loads(artifact.text(manifest_path))
    build = manifest["jupyterlab"]["_build"]
    assert build["extension"] == "./extension"

    load_path = PurePosixPath(build["load"].replace("\\", "/"))
    assert not load_path.is_absolute()
    assert load_path.parts[0] == "static"
    assert ".." not in load_path.parts
    assert load_path in artifact.files
    assert load_path.suffix == ".js"

    remote_source = artifact.text(load_path)
    exposed = EXTENSION_EXPOSE_PATTERN.findall(remote_source)
    assert len(exposed) == 1
    chunk_id, module_id = (int(value) for value in exposed[0])

    chunk_maps = CHUNK_HASH_MAP_PATTERN.findall(remote_source)
    assert len(chunk_maps) == 1
    chunk_hashes = {
        int(mapped_chunk): digest
        for mapped_chunk, digest in CHUNK_HASH_PATTERN.findall(chunk_maps[0])
    }
    assert chunk_id in chunk_hashes
    extension_chunk_path = PurePosixPath(
        f"static/{chunk_id}.{chunk_hashes[chunk_id]}.js"
    )
    assert extension_chunk_path in artifact.files

    extension_source = artifact.text(extension_chunk_path)
    assert re.search(
        rf"push\(\[\[{chunk_id}\],\{{.*?(?<!\d){module_id}\(",
        extension_source,
    )
    for marker in REQUIRED_TASK_11_MARKERS:
        assert marker in extension_source
    for marker in REQUIRED_TASK_12_MARKERS:
        assert marker in extension_source

    all_javascript = "\n".join(
        artifact.text(path) for path in artifact.javascript_paths
    )
    for marker in FORBIDDEN_STALE_OR_PRIVATE_MARKERS:
        assert marker not in all_javascript

    return ExtensionGraph(
        load_path=load_path,
        extension_chunk_path=extension_chunk_path,
        chunk_id=chunk_id,
        module_id=module_id,
    )


def test_repository_and_delivery_wheel_load_the_same_task_11_extension() -> None:
    _assert_wheel_delivery_contents()
    repository = _repository_artifact()
    delivery = _wheel_artifact()

    repository_graph = _assert_task_11_artifact(repository)
    delivery_graph = _assert_task_11_artifact(delivery)

    assert delivery_graph == repository_graph
    assert delivery.javascript_paths == repository.javascript_paths
    for path in repository.javascript_paths:
        assert delivery.files[path] == repository.files[path]
