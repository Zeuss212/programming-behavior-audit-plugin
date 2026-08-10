import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[2]
PACKAGE_JSON = ROOT / "package.json"
PYPROJECT = ROOT / "pyproject.toml"
RUNTIME_EXAMPLE = (
    ROOT / "deploy" / "bluedot" / "release-0.3.0" / "runtime.env.example"
)


def test_classroom_release_version_is_030() -> None:
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert package["version"] == "0.3.0"
    assert pyproject["project"]["dynamic"] == [
        "version",
        "description",
        "authors",
        "urls",
        "keywords",
    ]
    assert pyproject["tool"]["hatch"]["version"]["source"] == "nodejs"


def test_classroom_runtime_example_uses_five_minute_timeout() -> None:
    values = {}
    for line in RUNTIME_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values[
        "JUPYTERLAB_BEHAVIOR_AUDIT_STALE_SESSION_TIMEOUT_SEC"
    ] == "300"
    assert values["JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR"] == (
        "/workspace/result/behavior-audit"
    )
    assert "ARK_API_KEY" not in values
