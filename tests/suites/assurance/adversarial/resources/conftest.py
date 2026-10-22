"""Each resource case runs in a fresh worker and retains its measurements."""

import pytest

from tests.support.resources.runner import run_resource


_CAPABILITIES = {
    "ndjson_read": ("unstd.serde.jsonx", ("_HAVE_MSGSPEC", "_HAVE_ORJSON")),
    "ndjson_write": ("unstd.serde.jsonx", ("_HAVE_MSGSPEC", "_HAVE_ORJSON")),
    "json_nesting": ("unstd.serde.jsonx", ("_HAVE_MSGSPEC", "_HAVE_ORJSON")),
    "wav_blocks": ("unstd.audio.wavx", ("_HAVE_NUMPY", "_HAVE_SOUNDFILE")),
    "rex_cache": ("unstd.rex", ("_HAVE_IRGX",)),
}


@pytest.fixture(params=["installed", "stdlib"])
def resource_probe(request):
    def probe(module, function, **arguments):
        report = run_resource(
            f"tests.support.resources.{module}",
            function,
            backend=request.param,
            **arguments,
        )
        request.node.user_properties.append((f"resource_{function}", report))
        if function in _CAPABILITIES:
            facade, flags = _CAPABILITIES[function]
            selected = report["capabilities"][facade]
            if request.param == "stdlib":
                assert all(selected[flag] is False for flag in flags), selected
            elif request.config.getoption("--require-extras"):
                assert all(selected[flag] is True for flag in flags), selected
        return report

    return probe
