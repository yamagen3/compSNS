"""Offline checks for immutable R2 handoff files; no SDK or account is used."""

import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "r2_storage.py"
SPEC = importlib.util.spec_from_file_location("r2_storage", TOOL)
r2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r2)

BUCKET = "offline-test-bucket"
PREFIX = "compsns/v1"
RUN = "READ-20261117-example"


class S3Error(Exception):
    def __init__(self, code, status):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }
        super().__init__(code)


class FakeS3:
    """Small S3 behavioral fake with conditional writes and two-item pages."""

    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.list_calls = []
        self.page_count = 0
        self.before_put = None

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs.copy())
        identity = (kwargs["Bucket"], kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and identity in self.objects:
            raise S3Error("PreconditionFailed", 412)
        if self.before_put:
            self.before_put()
        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        self.objects[identity] = {
            "body": bytes(body),
            "metadata": kwargs.get("Metadata", {}).copy(),
        }
        return {"ETag": '"offline-etag"'}

    def get_object(self, **kwargs):
        try:
            obj = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError:
            raise S3Error("NoSuchKey", 404)
        return {
            "Body": io.BytesIO(obj["body"]),
            "ContentLength": len(obj["body"]),
            "Metadata": obj["metadata"].copy(),
        }

    def head_object(self, **kwargs):
        response = self.get_object(**kwargs)
        response.pop("Body")
        return response

    def get_paginator(self, name):
        if name != "list_objects_v2":
            raise AssertionError("Unexpected S3 operation: " + name)
        client = self

        class Paginator:
            def paginate(self, **kwargs):
                client.list_calls.append(kwargs.copy())
                rows = [
                    {"Key": key, "Size": len(obj["body"])}
                    for (bucket, key), obj in sorted(client.objects.items())
                    if bucket == kwargs["Bucket"] and key.startswith(kwargs["Prefix"])
                ]
                for offset in range(0, len(rows), 2):
                    client.page_count += 1
                    yield {"Contents": rows[offset:offset + 2]}

        return Paginator()

    def seed(self, key, body):
        self.objects[(BUCKET, key)] = {"body": body, "metadata": {}}


class R2StorageTests(unittest.TestCase):
    def setUp(self):
        scratch = REPO / "data" / "test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = FakeS3()

    def source(self, name="bundle.json", body=b'{"synthetic": true}\n'):
        path = self.root / name
        path.write_bytes(body)
        return path

    def upload(self, path, run_id=RUN, role="reading"):
        return r2.upload_file(self.client, BUCKET, PREFIX, run_id, role, path)

    def pull(self, output=None):
        return r2.download_run(
            self.client, BUCKET, PREFIX, RUN, output or self.root / "pulled"
        )

    def test_retry_preserves_remote_bytes_and_changed_file_creates_revision(self):
        original = b'{"revision": 1}\n'
        revised = b'{"revision": 2}\n'
        path = self.source(body=original)
        first = self.upload(path)
        retry = self.upload(path)
        self.assertEqual((first["status"], retry["status"]), ("uploaded", "exists"))
        self.assertEqual(first["key"], retry["key"])
        path.write_bytes(revised)
        second = self.upload(path)
        self.assertNotEqual(first["key"], second["key"])
        self.assertEqual(len(self.client.objects), 2)
        self.assertEqual(self.client.objects[(BUCKET, first["key"])]["body"], original)
        self.assertEqual(self.client.objects[(BUCKET, second["key"])]["body"], revised)
        self.assertTrue(all(call.get("IfNoneMatch") == "*" for call in self.client.put_calls))

        # An existing key with dishonest metadata is never accepted as a safe retry.
        path.write_bytes(original)
        self.client.objects[(BUCKET, first["key"])]["body"] = b"wrong remote bytes"
        with self.assertRaises((ValueError, RuntimeError)):
            self.upload(path)
        self.assertEqual(
            self.client.objects[(BUCKET, first["key"])]["body"], b"wrong remote bytes"
        )

    def test_listing_consumes_all_pages_and_does_not_leak_neighboring_runs(self):
        expected = set()
        for index in range(5):
            row = self.upload(self.source("part%d.json" % index, ("part%d" % index).encode()))
            expected.add(row["key"])
        self.upload(self.source("neighbor.json"), run_id=RUN + "-neighbor")
        self.upload(self.source("other.json"), run_id="OTHER-RUN")

        rows = r2.list_run(self.client, BUCKET, PREFIX, RUN)
        self.assertEqual({row["key"] for row in rows}, expected)
        self.assertEqual(len(rows), 5)
        self.assertEqual(self.client.page_count, 3)
        self.assertEqual(self.client.list_calls[0]["Prefix"], PREFIX + "/runs/" + RUN + "/")

    def test_source_changes_during_send_cannot_mismatch_uploaded_digest(self):
        original = b"snapshot that was selected for upload"
        path = self.source(body=original)
        self.client.before_put = lambda: path.write_bytes(b"a concurrent local change")
        uploaded = self.upload(path)
        stored = self.client.objects[(BUCKET, uploaded["key"])]["body"]
        self.assertEqual(stored, original)
        self.assertEqual(hashlib.sha256(stored).hexdigest(), uploaded["sha256"])

    def test_pull_preserves_all_revision_bytes_and_refuses_local_conflict(self):
        path = self.source(body=b"first\x00revision\xff")
        self.upload(path)
        path.write_bytes(b"second\x00revision\xfe")
        self.upload(path)
        output = self.root / "pulled"
        files = self.pull(output)
        self.assertEqual(len(set(files)), 2)
        self.assertEqual({file.read_bytes() for file in files}, {
            b"first\x00revision\xff", b"second\x00revision\xfe"
        })
        for file in files:
            self.assertEqual(file.resolve().relative_to(output.resolve()).parts[0], "reading")
        self.assertEqual(set(self.pull(output)), set(files))

        files[0].write_bytes(b"local edits to retain")
        with self.assertRaises((FileExistsError, ValueError, RuntimeError)):
            self.pull(output)
        self.assertEqual(files[0].read_bytes(), b"local edits to retain")

    def test_corrupt_download_is_rejected_before_saving_bytes(self):
        body = b"expected content"
        sha = hashlib.sha256(body).hexdigest()
        key = r2.object_key(PREFIX, RUN, "reading", sha, "bundle.json")
        self.client.seed(key, b"corrupt remote content")
        output = self.root / "pulled"
        with self.assertRaises((ValueError, RuntimeError)):
            self.pull(output)
        self.assertFalse((output / "reading" / sha / "bundle.json").exists())

    def test_untrusted_remote_keys_cannot_escape_or_use_windows_device_names(self):
        body = b"synthetic data"
        sha = hashlib.sha256(body).hexdigest()
        start = PREFIX + "/runs/" + RUN + "/reading/" + sha + "/"
        malicious_keys = [
            start + "../escape.json",
            start + "folder\\escape.json",
            start + "/absolute.json",
            start + "C:\\absolute.json",
            start + "CON",
            start + "nul.json",
            start + "trailing.",
        ]
        for index, key in enumerate(malicious_keys):
            with self.subTest(key=key):
                self.client.objects.clear()
                self.client.seed(key, body)
                output = self.root / ("malicious%d" % index)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    self.pull(output)
                self.assertFalse(output.exists() and any(p.is_file() for p in output.rglob("*")))

    def test_pull_refuses_link_ancestor_and_keeps_external_directory_empty(self):
        self.upload(self.source())
        output = self.root / "pulled"
        outside = self.root / "outside"
        output.mkdir()
        outside.mkdir()
        try:
            (output / "reading").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest("Directory symlinks unavailable: " + str(exc))
            try:
                # A junction exercises the same Windows reparse-point boundary
                # without requiring the symbolic-link privilege on this host.
                import _winapi
                _winapi.CreateJunction(str(outside), str(output / "reading"))
            except (ImportError, AttributeError, OSError) as junction_error:
                self.skipTest("Directory links unavailable: " + str(junction_error))
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.pull(output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_help_and_plan_work_without_credentials_sdk_or_network(self):
        source = self.source(body=b"offline planning bytes")
        # A separate interpreter detects accidental eager SDK loading as well as
        # credential discovery or network access during help and local planning.
        wrapper = """
import importlib.abc, runpy, socket, sys
class NoSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'boto3', 'botocore'}:
            raise AssertionError('SDK import during an offline command: ' + fullname)
sys.meta_path.insert(0, NoSDK())
def no_network(*args, **kwargs):
    raise AssertionError('Network access during an offline command')
socket.socket = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        environment = {
            name: value for name, value in os.environ.items()
            if not name.upper().startswith(("AWS_", "R2_", "CLOUDFLARE_"))
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        commands = [
            ["--help"],
            ["plan", "--run-id", RUN, "--role", "reading", "--file", str(source)],
        ]
        for arguments in commands:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", wrapper, str(TOOL)] + arguments,
                    cwd=REPO, env=environment, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if arguments[0] == "plan":
                    self.assertIn(hashlib.sha256(source.read_bytes()).hexdigest(), result.stdout)
                    self.assertIn(RUN, result.stdout)
                    self.assertIn(source.name, result.stdout)
                else:
                    self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
