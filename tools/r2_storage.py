#!/usr/bin/env python3
"""Store explicit protocol artifacts in a private R2 bucket without modifying them.

Objects are kept per run, per source platform (x / threads / both) and per role:
    {prefix}/runs/{run_id}/{platform}/{role}/{sha256}/{filename}

Each platform can live in its own bucket (COMPSNS_R2_BUCKET_X / _THREADS / _BOTH),
so write permission is separated by bucket while one read-only key reads all.
A single bucket (COMPSNS_R2_BUCKET) is also supported, without that separation.

Offline ``plan``, ``locate --local`` and imports use only the standard library.
Network commands require Python 3.10+ and requirements-r2.txt. No bucket or
public access is created. Deletion is manual: ``delete`` only lists what it
would remove unless ``--yes`` is given.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFIX = "compsns/v2"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_NAME_CHARS = 255
IRREGULAR_DIR = "_irregular"

# Which source platforms each role may store under. "both" holds files that mix
# X and Threads (blinded input, coded posts, integrated results, reading records).
ROLE_PLATFORMS = {
    "collector_x": ("x",),
    "collector_threads": ("threads",),
    "aggregator": ("both",),
    "common_coder": ("both",),
    "integrator": ("both",),
    "reading": ("both", "x", "threads"),
}
ROLES = tuple(ROLE_PLATFORMS)
PLATFORMS = ("x", "threads", "both")

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_STORED = re.compile(r"[A-Za-z0-9%](?:%[0-9A-F]{2}|[A-Za-z0-9._-])*\Z")
_KEEP = re.compile(r"[A-Za-z0-9_-]")
_EXTENSION = re.compile(r"[A-Za-z0-9]{1,16}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", re.I)
_POST_ID = re.compile(r"[A-Za-z0-9_-]{5,64}\Z")


def _segment(value: str, label: str) -> str:
    if (not isinstance(value, str) or not _SEGMENT.fullmatch(value)
            or ".." in value or value.endswith(".") or _DEVICE.match(value)):
        raise ValueError("Invalid {}: use safe ASCII letters, digits, '.', '_' or '-'.".format(label))
    return value


def _prefix(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid prefix.")
    for part in value.split("/"):
        _segment(part, "prefix segment")
    return value


def _run_prefix(prefix: str, run_id: str) -> str:
    return "{}/runs/{}/".format(_prefix(prefix), _segment(run_id, "run_id"))


def _percent(text: str) -> str:
    """Percent-escape everything except ASCII letters, digits, '_' and '-'."""
    out = []
    for char in text:
        if _KEEP.fullmatch(char):
            out.append(char)
            continue
        try:
            raw = char.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Filename is not valid text; rename the file.") from None
        out.extend("%{:02X}".format(byte) for byte in raw)
    return "".join(out)


def _stored(name: str) -> str:
    """Validate a filename as stored in a key: safe ASCII or percent-escaped."""
    if (not isinstance(name, str) or len(name) > MAX_NAME_CHARS or not _STORED.fullmatch(name)
            or ".." in name or name.endswith(".") or _DEVICE.match(name)):
        raise ValueError("Invalid stored filename.")
    original_name(name)
    return name


def original_name(stored: str) -> str:
    """Return the human-readable filename for a stored (possibly escaped) name."""
    if "%" not in stored:
        return stored
    try:
        return unquote(stored, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("Invalid stored filename.") from None


def stored_name(name: str) -> str:
    """Keep safe names unchanged; percent-escape any other name instead of rejecting it."""
    try:
        return _segment(name, "filename")
    except ValueError:
        pass
    if not isinstance(name, str) or name in ("", ".", ".."):
        raise ValueError("Invalid filename.")
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or not _EXTENSION.fullmatch(extension):
        stem, extension = name, ""
    escaped = _percent(stem)
    if escaped[:1] in ("_", "-"):
        escaped = "%{:02X}".format(ord(escaped[0])) + escaped[1:]
    if extension:
        escaped += "." + extension
    if _DEVICE.match(escaped):
        escaped = "%{:02X}".format(ord(escaped[0])) + escaped[1:]
    if len(escaped) > MAX_NAME_CHARS:
        raise ValueError("Filename is too long after escaping; use a shorter name.")
    return _stored(escaped)


def resolve_platform(role: str, platform=None) -> str:
    """Return the platform for a role; collectors are fixed, reading defaults to both."""
    if role not in ROLE_PLATFORMS:
        raise ValueError("Unknown role.")
    allowed = ROLE_PLATFORMS[role]
    if platform is None:
        return allowed[0]
    if platform not in allowed:
        raise ValueError("Role {} stores only under: {}.".format(role, ", ".join(allowed)))
    return platform


def _check_name_matches_platform(name: str, platform: str) -> None:
    """Refuse obvious mix-ups such as a Threads bundle sent as X data."""
    lowered = name.lower()
    if platform == "x" and "_threads_" in lowered:
        raise ValueError("Filename looks like Threads data but the platform is x.")
    if platform == "threads" and "_x_" in lowered:
        raise ValueError("Filename looks like X data but the platform is threads.")


def object_key(prefix: str, run_id: str, platform: str, role: str, sha256: str, filename: str) -> str:
    """Return a validated, content-addressed key; filename must already be a stored name."""
    if platform not in PLATFORMS:
        raise ValueError("Unknown platform.")
    resolve_platform(role, platform)
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters.")
    key = "{}{}/{}/{}/{}".format(_run_prefix(prefix, run_id), platform, role, sha256,
                                 _stored(filename))
    if len(key.encode("ascii")) > 1024:
        raise ValueError("Object key exceeds 1024 bytes.")
    return key


def _snapshot(file_path, platform: str) -> tuple[Path, bytes, str, str]:
    path = Path(file_path)
    name = stored_name(path.name)
    _check_name_matches_platform(path.name, platform)
    if not path.is_file():
        raise ValueError("Upload requires one regular file.")
    with path.open("rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Upload requires one regular file.")
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds the 64 MiB limit.")
    return path, data, hashlib.sha256(data).hexdigest(), name


def _prepare(prefix: str, run_id: str, role: str, file_path, platform=None) -> tuple[dict, bytes]:
    platform = resolve_platform(role, platform)
    path, data, digest, name = _snapshot(file_path, platform)
    planned = {"key": object_key(prefix, run_id, platform, role, digest, name),
               "platform": platform, "role": role, "sha256": digest, "size": len(data),
               "filename": name, "original_name": path.name}
    return planned, data


def plan_file(prefix: str, run_id: str, role: str, file_path, platform=None) -> dict:
    """Show where a file would be stored, without connecting to R2."""
    return _prepare(prefix, run_id, role, file_path, platform)[0]


class Store:
    """Buckets per platform plus a read client and an optional write client."""

    def __init__(self, buckets: dict, read_client, write_client=None):
        if set(buckets) != set(PLATFORMS):
            raise ValueError("Give a bucket for each of: x, threads, both.")
        self.buckets = dict(buckets)
        self.read_client = read_client
        self.write_client = write_client

    @property
    def partitioned(self) -> bool:
        return len(set(self.buckets.values())) > 1

    def bucket(self, platform: str) -> str:
        return self.buckets[platform]

    def platforms_by_bucket(self) -> dict:
        result = {}
        for platform in PLATFORMS:
            result.setdefault(self.buckets[platform], []).append(platform)
        return result

    def writer(self):
        if self.write_client is None:
            raise ValueError("This command needs a write key "
                             "(COMPSNS_R2_WRITE_ACCESS_KEY_ID and COMPSNS_R2_WRITE_SECRET_ACCESS_KEY).")
        return self.write_client


def _error_code(exc: Exception):
    response = getattr(exc, "response", {})
    if not isinstance(response, dict):
        return None, None
    return (response.get("Error", {}).get("Code"),
            response.get("ResponseMetadata", {}).get("HTTPStatusCode"))


def _access_denied(exc: Exception) -> bool:
    code, status = _error_code(exc)
    return code in ("AccessDenied", "Forbidden", "403") or status == 403


def _precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    if not isinstance(response, dict):
        return False
    return (response.get("Error", {}).get("Code") in ("PreconditionFailed", "412")
            or response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412)


def _remote_bytes(client, bucket: str, key: str, expected_sha256=None) -> bytes:
    """Download at most 64 MiB; verify the SHA-256 when it is known from the key."""
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            if response.get("ContentLength", 0) > MAX_FILE_BYTES:
                raise ValueError("Remote file exceeds the 64 MiB limit.")
            data = body.read(MAX_FILE_BYTES + 1)
        finally:
            body.close()
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("R2 download failed; check access and connection.") from None
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Remote file exceeds the 64 MiB limit.")
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError("SHA-256 mismatch; remote artifact was not saved.")
    return data


def upload_file(store: Store, prefix: str, run_id: str, role: str, file_path,
                platform=None) -> dict:
    """Upload one byte snapshot once into the platform's bucket; never replace objects."""
    # Upload exactly the bytes that were hashed, even if the file changes meanwhile.
    planned, data = _prepare(prefix, run_id, role, file_path, platform)
    key, digest = planned["key"], planned["sha256"]
    bucket = store.bucket(planned["platform"])
    client = store.writer()
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
                          Metadata={"sha256": digest})
        status = "uploaded"
    except Exception as exc:
        if _access_denied(exc):
            raise RuntimeError("This write key cannot write to the {} area (bucket {})."
                               .format(planned["platform"], bucket)) from None
        if not _precondition_failed(exc):
            raise RuntimeError("R2 upload failed; check access and connection.") from None
        if _remote_bytes(client, bucket, key, digest) != data:
            raise RuntimeError("Existing object differs; upload refused.")
        status = "exists"
    return dict(planned, bucket=bucket, status=status)


def _parse_key(run_prefix: str, key: str, size) -> dict:
    """Classify one listed key. Keys outside the layout are reported, not fatal."""
    entry = {"key": key, "size": size}
    rest = key[len(run_prefix):]
    parts = rest.split("/")
    try:
        if not isinstance(size, int) or size < 0:
            raise ValueError("invalid size")
        if len(parts) != 4:
            raise ValueError("not in {platform}/{role}/{sha256}/{filename} layout")
        platform, role, digest, name = parts
        if platform not in PLATFORMS or role not in ROLE_PLATFORMS:
            raise ValueError("unknown platform or role")
        resolve_platform(role, platform)
        if not _SHA256.fullmatch(digest):
            raise ValueError("invalid sha256")
        _stored(name)
        if size > MAX_FILE_BYTES:
            raise ValueError("exceeds the 64 MiB limit")
    except ValueError as exc:
        entry.update(irregular=True, reason=str(exc))
        return entry
    entry.update(irregular=False, platform=platform, role=role, sha256=digest,
                 filename=name, original_name=original_name(name))
    return entry


def list_run(store: Store, prefix: str, run_id: str) -> list[dict]:
    """List every revision of one run across the platform buckets.

    Irregular keys, and objects kept in another platform's bucket, are flagged
    instead of failing the whole listing.
    """
    run_prefix = _run_prefix(prefix, run_id)
    result = []
    for bucket, platforms in store.platforms_by_bucket().items():
        try:
            pages = store.read_client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=run_prefix)
            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if not isinstance(key, str) or not key.startswith(run_prefix):
                        raise ValueError("R2 returned an object outside the requested run.")
                    entry = _parse_key(run_prefix, key, item.get("Size", 0))
                    if not entry["irregular"] and entry["platform"] not in platforms:
                        entry = {"key": key, "size": entry["size"], "irregular": True,
                                 "reason": "stored in the bucket for another platform"}
                    entry["bucket"] = bucket
                    result.append(entry)
        except ValueError:
            raise
        except Exception as exc:
            if _access_denied(exc):
                raise RuntimeError("This read key cannot list bucket {}.".format(bucket)) from None
            raise RuntimeError("R2 listing failed; check access and connection.") from None
    return sorted(result, key=lambda entry: (entry["key"], entry["bucket"]))


def _windows_directory_lock(path: Path, stack: ExitStack, allow_reparse: bool = False) -> None:
    """Hold each directory against rename/reparse changes during local writes."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # READ_ATTRIBUTES, SHARE_READ (deny writes/deletes to the directory itself),
    # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT (or without it for the base).
    flags = 0x02000000 if allow_reparse else 0x02200000
    handle = create(str(path), 0x80, 0x1, None, 3, flags, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Cannot safely lock output directory.")
    stack.callback(close, handle)
    if allow_reparse:
        return
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("Output path contains a link or reparse point.")


def output_root(output_dir) -> Path:
    """Resolve the user-chosen output folder once; links above it are the user's choice."""
    root = Path(os.path.realpath(os.path.abspath(output_dir)))
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("Output path is not a directory.")
    return root


@contextmanager
def _safe_directory(root: Path, parts):
    """Create directories below root without following links planted inside it."""
    with ExitStack() as stack:
        current = root
        descriptor = None
        if os.name == "nt":
            _windows_directory_lock(current, stack, allow_reparse=True)
        else:
            descriptor = os.open(current, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, descriptor)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        for part in parts:
            current = current / part
            if os.name == "nt":
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                _windows_directory_lock(current, stack)
            else:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                descriptor = os.open(part, flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
        yield current, descriptor


def _local_file(parent: Path, descriptor, filename: str, data: bytes, digest: str) -> tuple[Path, bool]:
    """Create filename exclusively; an existing identical file is accepted, others refused."""
    target = parent / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if os.name != "nt":
        flags |= os.O_NOFOLLOW
    try:
        if descriptor is None:
            fd = os.open(target, flags | os.O_BINARY, 0o600)
        else:
            fd = os.open(filename, flags, 0o600, dir_fd=descriptor)
    except FileExistsError:
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("Existing destination is not a regular file.")
        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        existing_fd = (os.open(target, read_flags) if descriptor is None else
                       os.open(filename, read_flags, dir_fd=descriptor))
        with os.fdopen(existing_fd, "rb") as existing:
            if hashlib.sha256(existing.read(MAX_FILE_BYTES + 1)).hexdigest() != digest:
                raise FileExistsError("Existing destination differs; overwrite refused.")
        return target, False
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
    except BaseException:
        # Remove only the exclusively created file; never replace an existing file.
        if descriptor is None:
            target.unlink()
        else:
            os.unlink(filename, dir_fd=descriptor)
        raise
    return target, True


def irregular_local_name(run_prefix: str, bucket: str, key: str) -> str:
    """A flat, escaped local filename for a key outside the layout (never a path)."""
    rest = key[len(run_prefix):]
    tag = hashlib.sha256((bucket + "/" + key).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    try:
        escaped = _percent(rest)
    except ValueError:
        escaped = "unreadable"
    return (tag + "_" + escaped)[:200]


def download_run(store: Store, prefix: str, run_id: str, output_dir) -> list[dict]:
    """Pull every object of a run, from all platform buckets, below output_dir.

    Regular objects go to {platform}/{role}/{sha256}/{filename} and are verified
    against the SHA-256 in their key. Irregular objects are saved, unverified,
    under _irregular/ with a flat escaped name, so they cannot leave output_dir.
    """
    run_prefix = _run_prefix(prefix, run_id)
    objects = list_run(store, prefix, run_id)
    root = output_root(output_dir)
    client = store.read_client
    results = []
    for item in objects:
        record = {"key": item["key"], "bucket": item["bucket"], "irregular": item["irregular"]}
        if item["irregular"]:
            if not isinstance(item["size"], int) or item["size"] > MAX_FILE_BYTES:
                record.update(status="skipped", reason=item["reason"])
                results.append(record)
                continue
            data = _remote_bytes(client, item["bucket"], item["key"])
            digest = hashlib.sha256(data).hexdigest()
            parts = (IRREGULAR_DIR,)
            name = irregular_local_name(run_prefix, item["bucket"], item["key"])
            record.update(verified=False, sha256=digest, reason=item["reason"])
        else:
            data = _remote_bytes(client, item["bucket"], item["key"], item["sha256"])
            digest = item["sha256"]
            parts = (item["platform"], item["role"], digest)
            name = item["filename"]
            record.update(verified=True, sha256=digest, original_name=item["original_name"])
        with _safe_directory(root, parts) as (parent, descriptor):
            path, created = _local_file(parent, descriptor, name, data, digest)
        record.update(path=str(path), status="saved" if created else "exists")
        results.append(record)
    return results


def post_id_pattern(post_ids) -> re.Pattern:
    """Match whole post IDs or URL codes, not substrings of longer identifiers."""
    ids = sorted({str(value).strip() for value in post_ids if str(value).strip()})
    if not ids:
        raise ValueError("Give at least one post ID.")
    for value in ids:
        if not _POST_ID.fullmatch(value):
            raise ValueError("Post IDs must be 5-64 characters of letters, digits, '_' or '-'.")
    body = b"|".join(re.escape(value.encode("ascii")) for value in ids)
    return re.compile(rb"(?<![A-Za-z0-9_-])(" + body + rb")(?![A-Za-z0-9_-])")


def _found(pattern: re.Pattern, data: bytes) -> list[str]:
    return sorted({match.decode("ascii") for match in pattern.findall(data)})


def locate_in_run(store: Store, prefix: str, run_id: str, post_ids) -> list[dict]:
    """Find which objects of a run mention any of the post IDs (for manual deletion)."""
    pattern = post_id_pattern(post_ids)
    result = []
    for item in list_run(store, prefix, run_id):
        record = {"key": item["key"], "bucket": item["bucket"]}
        if not isinstance(item["size"], int) or item["size"] > MAX_FILE_BYTES:
            result.append(dict(record, status="skipped", reason="too large"))
            continue
        data = _remote_bytes(store.read_client, item["bucket"], item["key"],
                             None if item["irregular"] else item["sha256"])
        found = _found(pattern, data)
        if found:
            result.append(dict(record, status="found", post_ids=found))
    return result


def locate_in_directory(directory, post_ids) -> list[dict]:
    """Find local copies (this PC or the aggregator's environment) that mention the IDs."""
    pattern = post_id_pattern(post_ids)
    root = Path(directory)
    if not root.is_dir():
        raise ValueError("Local path is not a directory.")
    result = []
    for current, folders, files in os.walk(root, followlinks=False):
        folders.sort()
        for name in sorted(files):
            path = Path(current) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_size > MAX_FILE_BYTES:
                result.append({"path": str(path), "status": "skipped", "reason": "too large"})
                continue
            found = _found(pattern, path.read_bytes())
            if found:
                result.append({"path": str(path), "status": "found", "post_ids": found})
    return result


def delete_objects(store: Store, prefix: str, run_id: str, keys, execute: bool = False,
                   bucket=None) -> list[dict]:
    """Delete listed keys of one run. Without execute, only report what would be deleted.

    Every key is checked against the current listing before anything is deleted.
    A key found in more than one bucket must be narrowed with bucket.
    """
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise ValueError("Give at least one --key.")
    run_prefix = _run_prefix(prefix, run_id)
    where = {}
    for item in list_run(store, prefix, run_id):
        if bucket is None or item["bucket"] == bucket:
            where.setdefault(item["key"], []).append(item["bucket"])
    targets = []
    for key in keys:
        if not isinstance(key, str) or not key.startswith(run_prefix):
            raise ValueError("Key is outside this run; nothing was deleted: {}".format(key))
        if key not in where:
            raise ValueError("Key is not in R2; nothing was deleted: {}".format(key))
        if len(where[key]) > 1:
            raise ValueError("Key is in several buckets; add --bucket. Nothing was deleted: {}"
                             .format(key))
        targets.append((where[key][0], key))
    client = store.writer() if execute else None
    results = []
    for target_bucket, key in targets:
        record = {"key": key, "bucket": target_bucket}
        if not execute:
            results.append(dict(record, status="would_delete"))
            continue
        try:
            client.delete_object(Bucket=target_bucket, Key=key)
        except Exception as exc:
            done = ", ".join(row["key"] for row in results) or "none"
            if _access_denied(exc):
                raise RuntimeError("This write key cannot delete in bucket {}. Already deleted: {}"
                                   .format(target_bucket, done)) from None
            raise RuntimeError("R2 delete failed at {}. Already deleted: {}"
                               .format(key, done)) from None
        results.append(dict(record, status="deleted"))
    return results


_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


def _buckets_from_env() -> dict:
    split = {platform: os.environ.get("COMPSNS_R2_BUCKET_" + platform.upper(), "")
             for platform in PLATFORMS}
    single = os.environ.get("COMPSNS_R2_BUCKET", "")
    if any(split.values()):
        if not all(split.values()):
            raise ValueError("Set all of COMPSNS_R2_BUCKET_X, COMPSNS_R2_BUCKET_THREADS and "
                             "COMPSNS_R2_BUCKET_BOTH, or only COMPSNS_R2_BUCKET.")
        buckets = split
    elif single:
        buckets = {platform: single for platform in PLATFORMS}
    else:
        raise ValueError("Set COMPSNS_R2_BUCKET_X, COMPSNS_R2_BUCKET_THREADS and "
                         "COMPSNS_R2_BUCKET_BOTH (or COMPSNS_R2_BUCKET for one bucket).")
    for name in set(buckets.values()):
        if not _BUCKET_NAME.fullmatch(name):
            raise ValueError("A bucket name is not valid: {}".format(name))
    return buckets


def _credentials(kind: str):
    """Return (key id, secret) for 'WRITE' or 'READ', or None when not set."""
    names = [("COMPSNS_R2_{}_ACCESS_KEY_ID".format(kind), "COMPSNS_R2_{}_SECRET_ACCESS_KEY".format(kind))]
    if kind == "WRITE":
        names.append(("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))
    for key_name, secret_name in names:
        key_id, secret = os.environ.get(key_name, ""), os.environ.get(secret_name, "")
        if key_id and secret:
            return key_id, secret
        if key_id or secret:
            raise ValueError("Set both {} and {}.".format(key_name, secret_name))
    return None


def store_from_env(need_write: bool) -> Store:
    if sys.version_info < (3, 10):
        raise RuntimeError("R2 network commands require Python 3.10 or newer.")
    account = os.environ.get("COMPSNS_R2_ACCOUNT_ID", "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ValueError("Set COMPSNS_R2_ACCOUNT_ID to the 32-character hexadecimal account ID.")
    buckets = _buckets_from_env()
    write = _credentials("WRITE")
    read = _credentials("READ") or write
    if need_write and write is None:
        raise ValueError("This command needs a write key: set COMPSNS_R2_WRITE_ACCESS_KEY_ID and "
                         "COMPSNS_R2_WRITE_SECRET_ACCESS_KEY.")
    if read is None:
        raise ValueError("Set a read key (COMPSNS_R2_READ_ACCESS_KEY_ID and "
                         "COMPSNS_R2_READ_SECRET_ACCESS_KEY) or a write key.")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("Install the optional R2 SDK: python -m pip install -r requirements-r2.txt") from None

    def client(pair):
        return boto3.client("s3", endpoint_url="https://{}.r2.cloudflarestorage.com".format(account),
                            region_name="auto", aws_access_key_id=pair[0], aws_secret_access_key=pair[1],
                            config=Config(signature_version="s3v4",
                                          request_checksum_calculation="when_required",
                                          response_checksum_validation="when_required"))

    write_client = client(write) if write is not None else None
    read_client = write_client if read == write else client(read)
    return Store(buckets, read_client, write_client)


def _emit(result) -> None:
    """Print JSON with readable names; fall back to ASCII escapes on narrow consoles."""
    try:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(result, ensure_ascii=True, indent=2))


def _read_ids(args) -> list[str]:
    ids = list(args.id or [])
    if args.ids_file:
        text = Path(args.ids_file).read_text(encoding="utf-8")
        ids.extend(line.strip() for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#"))
    return ids


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "upload", "list", "pull", "locate", "delete"):
        command = commands.add_parser(name)
        command.add_argument("--run-id", required=name != "locate")
        if name in ("plan", "upload"):
            command.add_argument("--role", required=True, choices=ROLES)
            command.add_argument("--platform", choices=PLATFORMS,
                                 help="Fixed for collectors; reading defaults to both.")
            command.add_argument("--file", required=True, type=Path)
        if name == "pull":
            command.add_argument("--output", type=Path, help="Default: data/runs/{run_id}")
        if name == "locate":
            command.add_argument("--local", type=Path,
                                 help="Search a local folder instead of R2 (no credentials needed).")
            command.add_argument("--id", action="append", help="Post ID or URL code (repeatable).")
            command.add_argument("--ids-file", type=Path, help="One post ID per line.")
        if name == "delete":
            command.add_argument("--key", action="append", required=True,
                                 help="Exact key from list or locate (repeatable).")
            command.add_argument("--yes", action="store_true",
                                 help="Actually delete. Without it, only show what would be deleted.")
            command.add_argument("--bucket", help="Only when the same key is in several buckets.")
    args = parser.parse_args(argv)
    try:
        prefix = _prefix(os.environ.get("COMPSNS_R2_PREFIX", DEFAULT_PREFIX))
        if args.run_id is not None:
            _run_prefix(prefix, args.run_id)
        if args.command == "plan":
            result = plan_file(prefix, args.run_id, args.role, args.file, args.platform)
        elif args.command == "locate" and args.local is not None:
            result = locate_in_directory(args.local, _read_ids(args))
        else:
            if args.command == "locate" and args.run_id is None:
                raise ValueError("locate needs --run-id (R2) or --local (a folder).")
            if args.command == "locate":
                post_id_pattern(_read_ids(args))
            store = store_from_env(need_write=args.command == "upload"
                                   or (args.command == "delete" and args.yes))
            if args.command == "upload":
                result = upload_file(store, prefix, args.run_id, args.role, args.file, args.platform)
            elif args.command == "list":
                result = list_run(store, prefix, args.run_id)
            elif args.command == "pull":
                output = args.output or ROOT / "data" / "runs" / args.run_id
                result = download_run(store, prefix, args.run_id, output)
            elif args.command == "locate":
                result = locate_in_run(store, prefix, args.run_id, _read_ids(args))
            else:
                result = delete_objects(store, prefix, args.run_id, args.key, args.yes, args.bucket)
        _emit(result)
        return 0
    except (ValueError, RuntimeError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
    except OSError:
        print("Error: local file access failed; check paths and permissions.", file=sys.stderr)
    except Exception:
        # SDK exceptions may contain endpoint URLs, request details or credentials.
        print("Error: R2 operation failed; check configuration and access.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
