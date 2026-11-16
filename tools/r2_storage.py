#!/usr/bin/env python3
"""Store explicit protocol artifacts in a private R2 bucket without modifying them.

Offline ``plan`` and imports use only the standard library. Network commands
require Python 3.10+ and requirements-r2.txt. No bucket or public access is created.
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

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFIX = "compsns/v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
ROLES = ("collector_x", "collector_threads", "common_coder", "integrator", "reading")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", re.I)


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


def object_key(prefix: str, run_id: str, role: str, sha256: str, filename: str) -> str:
    """Return a validated, content-addressed key; filenames must be basenames."""
    if role not in ROLES:
        raise ValueError("Unknown role.")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters.")
    key = "{}{}/{}/{}".format(_run_prefix(prefix, run_id), role, sha256,
                              _segment(filename, "filename"))
    if len(key.encode("ascii")) > 1024:
        raise ValueError("Object key exceeds 1024 bytes.")
    return key


def _snapshot(file_path) -> tuple[Path, bytes, str]:
    path = Path(file_path)
    _segment(path.name, "filename")
    if not path.is_file():
        raise ValueError("Upload requires one regular file.")
    with path.open("rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Upload requires one regular file.")
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds the 64 MiB limit.")
    return path, data, hashlib.sha256(data).hexdigest()


def _precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    if not isinstance(response, dict):
        return False
    return (response.get("Error", {}).get("Code") in ("PreconditionFailed", "412")
            or response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412)


def _remote_bytes(client, bucket: str, key: str, expected_sha256: str) -> bytes:
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
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError("SHA-256 mismatch; remote artifact was not saved.")
    return data


def upload_file(client, bucket: str, prefix: str, run_id: str, role: str, file_path) -> dict:
    """Upload one byte snapshot once. Existing objects are verified, never replaced."""
    path, data, digest = _snapshot(file_path)
    key = object_key(prefix, run_id, role, digest, path.name)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
                          Metadata={"sha256": digest})
        status = "uploaded"
    except Exception as exc:
        if not _precondition_failed(exc):
            raise RuntimeError("R2 upload failed; check access and connection.") from None
        if _remote_bytes(client, bucket, key, digest) != data:
            raise RuntimeError("Existing object differs; upload refused.")
        status = "exists"
    return {"key": key, "sha256": digest, "size": len(data), "status": status}


def list_run(client, bucket: str, prefix: str, run_id: str) -> list[dict]:
    """List every revision in one run, rejecting keys outside the canonical layout."""
    run_prefix = _run_prefix(prefix, run_id)
    result = []
    try:
        pages = client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=run_prefix)
        for page in pages:
            for item in page.get("Contents", []):
                key = item["Key"]
                if not isinstance(key, str) or not key.startswith(run_prefix):
                    raise ValueError("R2 returned an object outside the requested run.")
                parts = key[len(run_prefix):].split("/")
                if len(parts) != 3 or object_key(prefix, run_id, *parts) != key:
                    raise ValueError("R2 returned an invalid artifact key.")
                role, digest, filename = parts
                size = item.get("Size", 0)
                if not isinstance(size, int) or size < 0 or size > MAX_FILE_BYTES:
                    raise ValueError("R2 returned an invalid artifact size.")
                result.append({"key": key, "role": role, "sha256": digest,
                               "filename": filename, "size": size})
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("R2 listing failed; check access and connection.") from None
    return sorted(result, key=lambda entry: entry["key"])


def _windows_directory_lock(path: Path, stack: ExitStack) -> None:
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
    # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80, 0x1, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Cannot safely lock output directory.")
    stack.callback(close, handle)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("Output path contains a link or reparse point.")


@contextmanager
def _safe_directory(path: Path):
    """Create directories without following symlinks, including in root ancestors."""
    absolute = Path(os.path.abspath(path))
    with ExitStack() as stack:
        current = Path(absolute.anchor)
        descriptor = None
        if os.name == "nt":
            _windows_directory_lock(current, stack)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(current, flags)
            stack.callback(os.close, descriptor)
        for part in absolute.parts[1:]:
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
        yield absolute, descriptor


def _local_file(parent: Path, descriptor, filename: str, data: bytes, digest: str) -> Path:
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
        return target
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
    return target


def download_run(client, bucket: str, prefix: str, run_id: str, output_dir) -> list[Path]:
    """Pull all run revisions below output_dir/role/sha256/basename, verifying bytes."""
    objects = list_run(client, bucket, prefix, run_id)
    saved = []
    for item in objects:
        data = _remote_bytes(client, bucket, item["key"], item["sha256"])
        directory = Path(output_dir) / item["role"] / item["sha256"]
        with _safe_directory(directory) as (parent, descriptor):
            saved.append(_local_file(parent, descriptor, item["filename"], data, item["sha256"]))
    return saved


def _client_from_env():
    if sys.version_info < (3, 10):
        raise RuntimeError("R2 network commands require Python 3.10 or newer.")
    names = ("COMPSNS_R2_ACCOUNT_ID", "COMPSNS_R2_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    if any(not os.environ.get(name) for name in names):
        raise ValueError("Set COMPSNS_R2_ACCOUNT_ID, COMPSNS_R2_BUCKET, AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
    account = os.environ["COMPSNS_R2_ACCOUNT_ID"]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ValueError("COMPSNS_R2_ACCOUNT_ID must be a 32-character hexadecimal account ID.")
    bucket = os.environ["COMPSNS_R2_BUCKET"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("COMPSNS_R2_BUCKET is not a valid bucket name.")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("Install the optional R2 SDK: python -m pip install -r requirements-r2.txt") from None
    client = boto3.client("s3", endpoint_url="https://{}.r2.cloudflarestorage.com".format(account),
                          region_name="auto", aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                          config=Config(signature_version="s3v4",
                                        request_checksum_calculation="when_required",
                                        response_checksum_validation="when_required"))
    return client, bucket


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "upload", "list", "pull"):
        command = commands.add_parser(name)
        command.add_argument("--run-id", required=True)
        if name in ("plan", "upload"):
            command.add_argument("--role", required=True, choices=ROLES)
            command.add_argument("--file", required=True, type=Path)
        if name == "pull":
            command.add_argument("--output", type=Path, help="Default: data/runs/{run_id}")
    args = parser.parse_args(argv)
    try:
        prefix = _prefix(os.environ.get("COMPSNS_R2_PREFIX", DEFAULT_PREFIX))
        _run_prefix(prefix, args.run_id)
        if args.command == "plan":
            path, data, digest = _snapshot(args.file)
            result = {"key": object_key(prefix, args.run_id, args.role, digest, path.name),
                      "sha256": digest, "size": len(data)}
        else:
            client, bucket = _client_from_env()
            if args.command == "upload":
                result = upload_file(client, bucket, prefix, args.run_id, args.role, args.file)
            elif args.command == "list":
                result = list_run(client, bucket, prefix, args.run_id)
            else:
                output = args.output or ROOT / "data" / "runs" / args.run_id
                result = [str(path) for path in download_run(client, bucket, prefix, args.run_id, output)]
        print(json.dumps(result, ensure_ascii=True, indent=2))
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
