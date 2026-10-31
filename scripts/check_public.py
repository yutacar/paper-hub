#!/usr/bin/env python3
"""Offline privacy checks for tracked content and reachable Git history.

Ignored private files are never opened. Findings show locations and categories,
not matched secrets. This is a pattern check, not a guarantee of anonymity.
"""

import argparse
import dataclasses
import getpass
import ipaddress
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ALLOWED_IPS = {"127.0.0.1", "0.0.0.0", "::1", "::"}
PUBLIC_HOSTS = {"github.com", "docs.github.com", "docs.m5stack.com", "docs.espressif.com",
                "static-cdn.m5stack.com", "raw.githubusercontent.com", "api.github.com"}
ALLOWED_BINARY_PATHS = {"docs/images/sample.png"}
PATTERNS = {
    "home_path": re.compile(r"/(?:Users|home)/[^\s/]+|[A-Za-z]:\\Users\\[^\s\\]+"),
    "private_network_name": re.compile(r"\b[\w-]+\.(?:local|lan|internal|home|ts\.net)\b", re.I),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "access_token": re.compile(r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})\b"),
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,4}")
EMAIL = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL = re.compile(r'''https?://[^\s<>"')]+''')


@dataclasses.dataclass(frozen=True)
class Finding:
    source: str
    path: str
    line: int
    category: str


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def private_path(path):
    p = PurePosixPath(path)
    if p.name == "secrets.h" or (p.name.startswith(".env") and p.name != ".env.sample"):
        return True
    return any(part in {".venv", "data", "out", "work", "build", "dist", "__pycache__"} for part in p.parts) or p.suffix.lower() in {
        ".log", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".bin", ".elf", ".hex"}


def public_git_email(email):
    value = email.strip().lower()
    return value.endswith("@users.noreply.github.com") or value == "noreply@github.com"


def scan_text(text, path, source, sensitive_terms=()):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        categories = {key for key, pattern in PATTERNS.items() if pattern.search(line)}
        if any(value not in ALLOWED_IPS for value in IPV4.findall(line)):
            categories.add("ip_address")
        for candidate in IPV6.findall(line):
            try:
                address = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            if address not in ALLOWED_IPS:
                categories.add("ip_address")
        if any(not public_git_email(value) for value in EMAIL.findall(line)):
            categories.add("email_address")
        for value in URL.findall(line):
            try:
                host = urlsplit(value).hostname
            except ValueError:
                categories.add("url_requires_review")
                continue
            if host and "{" not in host and host not in PUBLIC_HOSTS | ALLOWED_IPS:
                categories.add("url_host_requires_review")
        if path.endswith(".sample"):
            if path.endswith("secrets.h.sample") and re.match(r'^\s*#define\s+\w+\s+(?!""\s*$)', line):
                categories.add("populated_sample")
            if PurePosixPath(path).name == ".env.sample" and "=" in line and not line.lstrip().startswith("#"):
                if line.split("=", 1)[1].strip():
                    categories.add("populated_sample")
        if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", line, re.I) for term in sensitive_terms if term):
            categories.add("local_identity_term")
        findings.extend(Finding(source, path, lineno, category) for category in sorted(categories))
    return findings


def inspect_blob(data, path, source, sensitive_terms):
    if private_path(path):
        return [Finding(source, path, 0, "private_file_tracked")]
    if path in ALLOWED_BINARY_PATHS:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding(source, path, 0, "binary_requires_review")]
    return scan_text(text, path, source, sensitive_terms)


def audit(root, history=False, sensitive_terms=()):
    root = Path(root)
    findings, checked, commits = [], 0, 0
    paths = git(root, "ls-files", "-z").decode().split("\0")
    for path in filter(None, paths):
        if private_path(path):
            findings.append(Finding("worktree", path, 0, "private_file_tracked"))
            continue
        file = root / path
        if file.is_symlink():
            findings.append(Finding("worktree", path, 0, "symlink_requires_review"))
        elif file.is_file():
            findings.extend(inspect_blob(file.read_bytes(), path, "worktree", sensitive_terms))
            checked += 1
    # The index may differ from the working tree; inspect what would be committed.
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        path = raw_path.decode()
        if private_path(path):
            findings.append(Finding("index", path, 0, "private_file_tracked"))
            continue
        if mode == "120000" or mode == "160000":
            findings.append(Finding("index", path, 0, "symlink_or_submodule_requires_review"))
            continue
        findings.extend(inspect_blob(git(root, "cat-file", "blob", oid), path, "index", sensitive_terms))
        checked += 1
    if history:
        seen = set()
        for revision in git(root, "rev-list", "--all").decode().splitlines():
            commits += 1
            source = revision[:12]
            # Skip private blobs by pathname, even if a previous commit tracked them.
            for entry in git(root, "ls-tree", "-rz", revision).split(b"\0"):
                if not entry:
                    continue
                metadata, raw_path = entry.split(b"\t", 1)
                mode, kind, oid = metadata.decode().split()
                path = raw_path.decode()
                identity = (path, oid)
                if identity in seen:
                    continue
                seen.add(identity)
                if private_path(path):
                    findings.append(Finding(source, path, 0, "private_file_tracked"))
                    continue
                if mode == "120000" or kind != "blob":
                    findings.append(Finding(source, path, 0, "symlink_or_submodule_requires_review"))
                    continue
                findings.extend(inspect_blob(git(root, "cat-file", "blob", oid), path, source, sensitive_terms))
                checked += 1
            metadata = git(root, "show", "-s", "--format=%ae%x00%ce%x00%B", revision).decode().split("\0", 2)
            if any(not public_git_email(email) for email in metadata[:2]):
                findings.append(Finding(source, "commit metadata", 0, "non_noreply_git_email"))
            findings.extend(scan_text(metadata[2], "commit message", source, sensitive_terms))
    return {"files_and_blobs_checked": checked, "commits_checked": commits,
            "findings": [dataclasses.asdict(finding) for finding in findings]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--sensitive-term", action="append", default=[])
    args = parser.parse_args()
    root = git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip()
    local_name = getpass.getuser()
    terms = args.sensitive_term + ([local_name] if local_name not in {"root", "runner", "user", "ubuntu"} else [])
    result = audit(root, args.history, terms)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Checked {result['files_and_blobs_checked']} files/blobs, {result['commits_checked']} commits")
        for finding in result["findings"]:
            print(f"{finding['source']} {finding['path']}:{finding['line']} [{finding['category']}]")
        print("PASS" if not result["findings"] else "REVIEW REQUIRED (matched values omitted)")
    raise SystemExit(bool(result["findings"]))


if __name__ == "__main__":
    main()
