import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTTP_CONFIG = ROOT / "deploy/classroom/nginx/bams-classroom-api-http.conf.template"
LOCATION_CONFIG = ROOT / "deploy/classroom/nginx/bams-classroom-api-location.conf.template"


def test_bams_ingress_renders_a_syntax_valid_proxy_without_sensitive_logging(
    tmp_path: Path,
):
    http_config = HTTP_CONFIG.read_text(encoding="utf-8")
    config = LOCATION_CONFIG.read_text(encoding="utf-8")

    assert "location = /classroom-api" in config
    assert "location ~ ^/classroom-api/v1/classroom/classrooms/[^/]+/events$" in config
    assert "location = /classroom-api/v1/classroom/plan-suggestions" in config
    assert "proxy_read_timeout 75s;" in config
    assert "location /classroom-api/" in config
    assert "proxy_pass ${CLASSROOM_SYNC_UPSTREAM};" in config
    assert "proxy_set_header Authorization $http_authorization;" in config
    assert "client_max_body_size 2m;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_cache off;" in config
    assert "log_format classroom_safe" in http_config
    assert "$uri" in http_config
    assert "$request_uri" not in http_config
    assert "$http_authorization" not in http_config
    assert "40002" not in config
    assert "40037" not in config
    assert "127.0.0.1" not in config

    rendered_location = config.replace(
        "${CLASSROOM_SYNC_UPSTREAM}", "http://127.0.0.1:8080"
    )
    rendered = tmp_path / "nginx.conf"
    rendered.write_text(
        f"events {{}}\nhttp {{\n{http_config}\nserver {{\nlisten 8080;\n"
        f"{rendered_location}\n}}\n}}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{rendered}:/etc/nginx/nginx.conf:ro",
            "nginx:1.27-alpine",
            "nginx",
            "-t",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
