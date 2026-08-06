"""Cross-language canonical JSON string-domain regression tests."""

import json

import pytest

from myextension.canonical_json import canonical_json_bytes


@pytest.mark.parametrize(
    "raw_json",
    [
        '{"value":"\\ud800"}',
        '{"value":"\\udc00"}',
        '{"\\ud800":"synthetic"}',
        '{"\\udc00":"synthetic"}',
        '["\\ud800"]',
        '["\\udc00"]',
    ],
)
def test_canonical_json_rejects_unpaired_surrogates(raw_json):
    with pytest.raises(ValueError, match="surrogate"):
        canonical_json_bytes(json.loads(raw_json))


def test_canonical_json_accepts_a_valid_surrogate_pair_as_unicode_scalar():
    assert canonical_json_bytes(json.loads('{"value":"\\ud83d\\ude00"}')) == (
        '{"value":"😀"}'.encode("utf-8")
    )
