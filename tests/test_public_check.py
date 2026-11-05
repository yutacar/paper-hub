import subprocess
from pathlib import Path

from scripts.check_public import audit, private_path, scan_text


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "noreply@github.com")
    return tmp_path


def commit(root):
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "Fixture snapshot")


def test_safe_text_and_empty_samples():
    text = "http://127.0.0.1/healthz\nhttps://docs.m5stack.com/\n"
    assert scan_text(text, "README.md", "worktree") == []
    assert scan_text("PORT=\n", ".env.sample", "worktree") == []
    assert scan_text('#define WIFI_PASS ""\n', "secrets.h.sample", "worktree") == []
    assert scan_text("noreply@github.com", "README.md", "worktree") == []


def test_private_patterns_and_location_only():
    # Assemble synthetic findings so repository content contains no fixed secret.
    ip = ".".join(str(part) for part in (10, 20, 30, 40))
    mail = "person" + "@" + "mail.test"
    path = "/" + "Users" + "/" + "private-account" + "/file"
    token = "ghp_" + "x" * 32
    text = "\n".join([ip, mail, path, token, "host" + "." + "local"])
    findings = scan_text(text, "fixture", "test")
    assert {item.category for item in findings} >= {
        "ip_address", "email_address", "home_path", "access_token", "private_network_name"}
    assert all(not hasattr(item, "value") for item in findings)
    assert {item.line for item in findings} == {1, 2, 3, 4, 5}


def test_sample_values_and_custom_identity_terms():
    assert scan_text("PORT=8090", ".env.sample", "test")[0].category == "populated_sample"
    assert scan_text('#define WIFI_PASS "synthetic"', "secrets.h.sample", "test")[0].category == "populated_sample"
    assert scan_text("owner-name", "file", "test", ["owner-name"])[0].category == "local_identity_term"
    assert private_path("firmware/paperdiy/secrets.h")
    assert private_path(".env")
    assert not private_path(".env.sample")
    assert private_path("out/image.png")


def test_history_detects_removed_content(tmp_path):
    root = repository(tmp_path)
    address = "private" + "@" + "mail.test"
    (root / "README.md").write_text(address)
    git(root, "config", "user.email", address)
    commit(root)
    git(root, "config", "user.email", "noreply@github.com")
    (root / "README.md").write_text("Public text")
    commit(root)
    assert audit(root)["findings"] == []
    result = audit(root, history=True)
    assert result["commits_checked"] == 2
    assert {item["category"] for item in result["findings"]} == {"email_address", "non_noreply_git_email"}


def test_private_file_never_opened(tmp_path, monkeypatch):
    root = repository(tmp_path)
    secret = root / "secrets.h"
    secret.write_text("synthetic test data")
    git(root, "add", "secrets.h")
    original = Path.read_bytes
    def protected(path):
        assert path != secret, "Private file must not be opened"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", protected)
    result = audit(root)
    assert result["findings"][0]["category"] == "private_file_tracked"


def test_index_is_checked_even_after_worktree_is_cleaned(tmp_path):
    root = repository(tmp_path)
    address = "staged" + "@" + "mail.test"
    (root / "README.md").write_text(address)
    git(root, "add", "README.md")
    (root / "README.md").write_text("Safe working tree")
    result = audit(root)
    assert result["findings"] == [{"source": "index", "path": "README.md", "line": 1,
                                    "category": "email_address"}]


def test_ipv6_private_key_and_unreviewed_host():
    network = "fd" + "00" + "::1"
    key_header = "-----" + "BEGIN " + "PRIVATE KEY" + "-----"
    url = "http://" + "private-host"
    result = scan_text("\n".join((network, key_header, url)), "fixture", "test")
    assert {item.category for item in result} == {
        "ip_address", "private_key", "url_host_requires_review"}


def test_sample_comments_are_not_configuration_values():
    comments = "# 例: http://<strata-host>:8081/metrics\n# 既定値: なし（必須）\nSTRATA_MONITOR_URL=\n"
    assert scan_text(comments, ".env.sample", "test") == []
    assert scan_text('// 日本向けの例: NTP_SERVER は ntp.nict.jp、TZ_RULE は JST-9。\n#define NTP_SERVER ""',
                     "secrets.h.sample", "test") == []
    # Actual secrets in comments must still be detected.
    address = ".".join(str(part) for part in (10, 20, 30, 40))
    assert scan_text("# private " + address, ".env.sample", "test")[0].category == "ip_address"


def test_only_public_sample_image_is_allowed():
    import io
    from PIL import Image
    from scripts.check_public import inspect_blob
    stream = io.BytesIO()
    Image.new("L", (960, 540), 255).save(stream, format="PNG")
    payload = stream.getvalue()
    for path in ("docs/images/sample.png", "docs/images/sample-strata.png", "docs/images/sample-usage.png"):
        assert inspect_blob(payload, path, "test", ()) == []
    for path in ("docs/images/other.png", "other/sample.png", "docs/images/sample.png.backup"):
        assert inspect_blob(payload, path, "test", ())[0].category == "binary_requires_review"
