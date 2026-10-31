"""Host tests of the exact C++ policy compiled into the firmware."""

import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def policy_binary(tmp_path_factory):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler required for firmware host tests")
    output = tmp_path_factory.mktemp("firmware") / "policy_check"
    subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "firmware/paperdiy"),
                    str(ROOT / "tests/firmware/policy_check.cpp"), "-o", str(output)], check=True)
    return output


@pytest.mark.parametrize("valid,hour,expected", [(1, 0, 1800), (1, 6, 1800),
    (1, 7, 600), (1, 23, 600), (0, 12, 1800)])
def test_sleep_window(policy_binary, valid, hour, expected):
    result = subprocess.check_output([str(policy_binary), "sleep", str(valid), str(hour)], text=True)
    assert int(result) == expected


def test_receive_buffer_bounds(policy_binary):
    subprocess.run([str(policy_binary), "append"], check=True)


@pytest.mark.parametrize("damage", [None, "truncated", "crc", "missing_iend", "huge_chunk",
    "wrong_dimensions", "wrong_color", "deflate_crc"])
def test_png_validation(policy_binary, tmp_path, damage):
    path = tmp_path / "payload.png"
    Image.new("L", (960, 540), 255).save(path)
    data = bytearray(path.read_bytes())
    if damage == "truncated":
        data = data[:20]
    elif damage == "crc":
        data[20] ^= 1
    elif damage == "missing_iend":
        data = data[:-12]
    elif damage == "huge_chunk":
        data[8:12] = b"\xff" * 4
    elif damage in ("wrong_dimensions", "wrong_color"):
        if damage == "wrong_dimensions":
            data[16:20] = struct.pack(">I", 100)
        else:
            data[25] = 6
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    elif damage == "deflate_crc":
        data[45] ^= 1  # IDAT corruption must be caught before any display write.
    path.write_bytes(data)
    result = subprocess.check_output([str(policy_binary), "png", str(path)], text=True)
    assert result == ("1" if damage is None else "0")


def test_secrets_template_and_git_exclusion():
    template = (ROOT / "firmware/paperdiy/secrets.h.sample").read_text()
    fields = dict(re.findall(r'^#define (\w+) (".*")$', template, re.M))
    assert set(fields) == {"WIFI_SSID", "WIFI_PASS", "HUB_URL", "NTP_SERVER", "TZ_RULE"}
    assert all(value == '""' for value in fields.values())
    result = subprocess.run(["git", "check-ignore", "firmware/paperdiy/secrets.h"], cwd=ROOT, capture_output=True)
    assert result.returncode == 0
    result = subprocess.run(["git", "check-ignore", "firmware/paperdiy/secrets.h.sample"], cwd=ROOT, capture_output=True)
    assert result.returncode == 1


@pytest.mark.parametrize("server,timezone,expected", [
    ("", "", "pool.ntp.org|UTC0"),
    ("ntp.nict.jp", "JST-9", "ntp.nict.jp|JST-9"),
    ("", "JST-9", "pool.ntp.org|JST-9"),
    ("ntp.nict.jp", "", "ntp.nict.jp|UTC0"),
])
def test_optional_clock_configuration(policy_binary, server, timezone, expected):
    assert subprocess.check_output([str(policy_binary), "config", server, timezone], text=True) == expected
