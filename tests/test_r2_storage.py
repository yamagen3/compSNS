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

BUCKETS = {"x": "compsns-x", "threads": "compsns-threads", "both": "compsns-both"}
BOTH = BUCKETS["both"]
PREFIX = "compsns/v2"
RUN = "READ-20261117-example"
X_ID = "1989123456789012345"
THREADS_CODE = "C1aBcD2eFgH"


class S3Error(Exception):
    def __init__(self, code, status):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }
        super().__init__(code)


class FakeS3:
    """Small S3 behavioral fake with conditional writes and two-item pages.

    Several fakes can share one storage dict; writable limits which buckets a
    key may write or delete in, like a bucket-scoped R2 token.
    """

    def __init__(self, objects=None, writable=None):
        self.objects = {} if objects is None else objects
        self.writable = writable
        self.put_calls = []
        self.list_calls = []
        self.delete_calls = []
        self.page_count = 0
        self.before_put = None

    def _check_write(self, bucket):
        if self.writable is not None and bucket not in self.writable:
            raise S3Error("AccessDenied", 403)

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs.copy())
        self._check_write(kwargs["Bucket"])
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

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs.copy())
        self._check_write(kwargs["Bucket"])
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {}

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

    def seed(self, key, body, bucket=BOTH):
        self.objects[(bucket, key)] = {"body": body, "metadata": {}}


def files_under(path):
    return sorted(p for p in Path(path).rglob("*") if p.is_file())


class R2StorageTests(unittest.TestCase):
    def setUp(self):
        scratch = REPO / "data" / "test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        self.store = r2.Store(BUCKETS, self.client, self.client)

    def source(self, name="bundle.json", body=b'{"synthetic": true}\n'):
        path = self.root / name
        path.write_bytes(body)
        return path

    def upload(self, path, run_id=RUN, role="reading", platform=None, store=None):
        return r2.upload_file(store or self.store, PREFIX, run_id, role, path, platform)

    def pull(self, output=None, store=None):
        return r2.download_run(store or self.store, PREFIX, RUN, output or self.root / "pulled")

    def listing(self, store=None):
        return r2.list_run(store or self.store, PREFIX, RUN)

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
        self.assertEqual(self.client.objects[(BOTH, first["key"])]["body"], original)
        self.assertEqual(self.client.objects[(BOTH, second["key"])]["body"], revised)
        self.assertTrue(all(call.get("IfNoneMatch") == "*" for call in self.client.put_calls))

        # An existing key with dishonest metadata is never accepted as a safe retry.
        path.write_bytes(original)
        self.client.objects[(BOTH, first["key"])]["body"] = b"wrong remote bytes"
        with self.assertRaises((ValueError, RuntimeError)):
            self.upload(path)
        self.assertEqual(
            self.client.objects[(BOTH, first["key"])]["body"], b"wrong remote bytes"
        )

    def test_x_and_threads_are_stored_and_pulled_into_separate_folders(self):
        x_file = self.source("collection_bundle_x_%s.json" % RUN, b"x data")
        t_file = self.source("collection_bundle_threads_%s.json" % RUN, b"threads data")
        x_row = self.upload(x_file, role="collector_x")
        t_row = self.upload(t_file, role="collector_threads")
        both_row = self.upload(self.source("reading_posts_%s.csv" % RUN, b"both"))
        self.assertIn("/runs/%s/x/collector_x/" % RUN, x_row["key"])
        self.assertIn("/runs/%s/threads/collector_threads/" % RUN, t_row["key"])
        self.assertIn("/runs/%s/both/reading/" % RUN, both_row["key"])
        self.assertEqual((x_row["bucket"], t_row["bucket"], both_row["bucket"]),
                         (BUCKETS["x"], BUCKETS["threads"], BUCKETS["both"]))
        self.assertIn((BUCKETS["x"], x_row["key"]), self.client.objects)
        self.assertIn((BUCKETS["threads"], t_row["key"]), self.client.objects)

        output = self.root / "pulled"
        tops = {Path(row["path"]).relative_to(output.resolve()).parts[0] for row in self.pull(output)}
        self.assertEqual(tops, {"x", "threads", "both"})

    def test_platform_mixups_are_refused(self):
        with self.assertRaises(ValueError):
            self.upload(self.source("collection_bundle_x_R.json"), role="collector_x",
                        platform="threads")
        with self.assertRaises(ValueError):
            self.upload(self.source("collection_bundle_threads_R.json"), role="collector_x")
        with self.assertRaises(ValueError):
            self.upload(self.source("collection_bundle_x_R.json"), role="collector_threads")
        with self.assertRaises(ValueError):
            self.upload(self.source("coded.jsonl"), role="common_coder", platform="x")
        self.assertEqual(self.client.objects, {})
        self.assertIn("/x/reading/", self.upload(self.source("note.md"), platform="x")["key"])

    def test_listing_consumes_all_pages_and_does_not_leak_neighboring_runs(self):
        expected = set()
        for index in range(5):
            row = self.upload(self.source("part%d.json" % index, ("part%d" % index).encode()))
            expected.add(row["key"])
        self.upload(self.source("neighbor.json"), run_id=RUN + "-neighbor")
        self.upload(self.source("other.json"), run_id="OTHER-RUN")

        rows = self.listing()
        self.assertEqual({row["key"] for row in rows}, expected)
        self.assertEqual(len(rows), 5)
        self.assertEqual(self.client.page_count, 3)
        self.assertEqual({call["Bucket"] for call in self.client.list_calls}, set(BUCKETS.values()))
        self.assertEqual({call["Prefix"] for call in self.client.list_calls},
                         {PREFIX + "/runs/" + RUN + "/"})

    def test_source_changes_during_send_cannot_mismatch_uploaded_digest(self):
        original = b"snapshot that was selected for upload"
        path = self.source(body=original)
        self.client.before_put = lambda: path.write_bytes(b"a concurrent local change")
        uploaded = self.upload(path)
        stored = self.client.objects[(BOTH, uploaded["key"])]["body"]
        self.assertEqual(stored, original)
        self.assertEqual(hashlib.sha256(stored).hexdigest(), uploaded["sha256"])

    def test_pull_preserves_all_revision_bytes_and_refuses_local_conflict(self):
        path = self.source(body=b"first\x00revision\xff")
        self.upload(path)
        path.write_bytes(b"second\x00revision\xfe")
        self.upload(path)
        output = self.root / "pulled"
        rows = self.pull(output)
        files = [Path(row["path"]) for row in rows]
        self.assertEqual(len(set(files)), 2)
        self.assertEqual({row["status"] for row in rows}, {"saved"})
        self.assertEqual({file.read_bytes() for file in files}, {
            b"first\x00revision\xff", b"second\x00revision\xfe"
        })
        for file in files:
            self.assertEqual(file.relative_to(output.resolve()).parts[:2], ("both", "reading"))
        again = self.pull(output)
        self.assertEqual({Path(row["path"]) for row in again}, set(files))
        self.assertEqual({row["status"] for row in again}, {"exists"})

        files[0].write_bytes(b"local edits to retain")
        with self.assertRaises((FileExistsError, ValueError, RuntimeError)):
            self.pull(output)
        self.assertEqual(files[0].read_bytes(), b"local edits to retain")

    def test_corrupt_download_is_rejected_before_saving_bytes(self):
        body = b"expected content"
        sha = hashlib.sha256(body).hexdigest()
        key = r2.object_key(PREFIX, RUN, "both", "reading", sha, "bundle.json")
        self.client.seed(key, b"corrupt remote content")
        output = self.root / "pulled"
        with self.assertRaises((ValueError, RuntimeError)):
            self.pull(output)
        self.assertFalse((output / "both" / "reading" / sha / "bundle.json").exists())

    def test_irregular_filenames_are_escaped_not_rejected(self):
        cases = {
            "収集レポート.md": "%E5%8F%8E%E9%9B%86%E3%83%AC%E3%83%9D%E3%83%BC%E3%83%88.md",
            "report (1).md": "report%20%281%29.md",
            "CON.json": "%43ON.json",
            "_memo.txt": "%5Fmemo.txt",
            ".hidden": "%2Ehidden",
            "a..b.csv": "a%2E%2Eb.csv",
            "100%.md": "100%25.md",
        }
        for original, expected in cases.items():
            with self.subTest(name=original):
                self.assertEqual(r2.stored_name(original), expected)
                self.assertEqual(r2.original_name(expected), original)
        self.assertEqual(r2.stored_name("collection_bundle_x_R.json"), "collection_bundle_x_R.json")
        with self.assertRaises(ValueError):
            r2.stored_name("長" * 90 + ".md")

        row = self.upload(self.source("収集レポート.md", b"japanese name"))
        self.assertTrue(row["key"].endswith("/" + cases["収集レポート.md"]))
        self.assertEqual(row["original_name"], "収集レポート.md")
        listed = self.listing()
        self.assertEqual(listed[0]["original_name"], "収集レポート.md")
        self.assertFalse(listed[0]["irregular"])
        pulled = self.pull()
        self.assertEqual(Path(pulled[0]["path"]).name, cases["収集レポート.md"])
        self.assertEqual(Path(pulled[0]["path"]).read_bytes(), b"japanese name")

    def test_irregular_remote_keys_do_not_block_and_stay_inside_output(self):
        good = self.upload(self.source(body=b"regular"))
        body = b"synthetic data"
        sha = hashlib.sha256(body).hexdigest()
        start = PREFIX + "/runs/" + RUN + "/both/reading/" + sha + "/"
        irregular_keys = [
            start + "../escape.json",
            start + "folder\\escape.json",
            start + "/absolute.json",
            start + "C:\\absolute.json",
            start + "CON",
            start + "収集.json",
            PREFIX + "/runs/" + RUN + "/dashboard upload.json",
            PREFIX + "/runs/" + RUN + "/x/common_coder/" + sha + "/coded.jsonl",
        ]
        for key in irregular_keys:
            self.client.seed(key, body)
        rows = self.listing()
        flagged = {row["key"] for row in rows if row["irregular"]}
        self.assertEqual(flagged, set(irregular_keys))
        self.assertEqual([row["key"] for row in rows if not row["irregular"]], [good["key"]])

        before = set(files_under(self.root))
        output = self.root / "pulled"
        pulled = self.pull(output)
        created = set(files_under(self.root)) - before
        self.assertEqual(len(created), len(irregular_keys) + 1)
        irregular_dir = (output / r2.IRREGULAR_DIR).resolve()
        for row in pulled:
            path = Path(row["path"]).resolve()
            if row["irregular"]:
                self.assertEqual(path.parent, irregular_dir)
                self.assertFalse(row["verified"])
                self.assertEqual(path.read_bytes(), body)
            else:
                self.assertTrue(row["verified"])

    def test_pull_refuses_link_inside_output_and_keeps_external_directory_empty(self):
        self.upload(self.source())
        output = self.root / "pulled"
        outside = self.root / "outside"
        output.mkdir()
        outside.mkdir()
        try:
            (output / "both").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest("Directory symlinks unavailable: " + str(exc))
            try:
                # A junction exercises the same Windows reparse-point boundary
                # without requiring the symbolic-link privilege on this host.
                import _winapi
                _winapi.CreateJunction(str(outside), str(output / "both"))
            except (ImportError, AttributeError, OSError) as junction_error:
                self.skipTest("Directory links unavailable: " + str(junction_error))
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.pull(output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_pull_accepts_a_link_above_the_chosen_output(self):
        self.upload(self.source(body=b"via link"))
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Directory symlinks unavailable: " + str(exc))
        rows = self.pull(link / "data" / "runs" / RUN)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["path"].startswith(str(real.resolve()) + os.sep))
        self.assertEqual(Path(rows[0]["path"]).read_bytes(), b"via link")

    def test_delete_is_a_dry_run_unless_confirmed_and_checks_every_key_first(self):
        keep = self.upload(self.source("keep.json", b"keep"))["key"]
        drop = self.upload(self.source("drop.json", b"drop"))["key"]
        other_run = self.upload(self.source("other.json", b"other"), run_id="OTHER-RUN")["key"]

        planned = r2.delete_objects(self.store, PREFIX, RUN, [drop])
        self.assertEqual(planned, [{"key": drop, "bucket": BOTH, "status": "would_delete"}])
        self.assertEqual(self.client.delete_calls, [])

        for bad in ([drop, other_run], [drop, drop[:-1] + "x"]):
            with self.subTest(keys=bad):
                with self.assertRaises(ValueError):
                    r2.delete_objects(self.store, PREFIX, RUN, bad, execute=True)
                self.assertEqual(self.client.delete_calls, [])

        done = r2.delete_objects(self.store, PREFIX, RUN, [drop], execute=True)
        self.assertEqual(done, [{"key": drop, "bucket": BOTH, "status": "deleted"}])
        remaining = {row["key"] for row in self.listing()}
        self.assertEqual(remaining, {keep})
        self.assertIn((BOTH, other_run), self.client.objects)

    def test_locate_finds_whole_ids_in_r2_and_in_local_copies(self):
        with_x = self.upload(self.source("collection_bundle_x_R.json",
                                         ('{"post_id": "%s"}' % X_ID).encode()),
                             role="collector_x")["key"]
        with_t = self.upload(self.source("collection_bundle_threads_R.json",
                                         ("https://www.threads.com/@a/post/%s" % THREADS_CODE).encode()),
                             role="collector_threads")["key"]
        self.upload(self.source("longer.json", ('{"n": "9%s0"}' % X_ID).encode()))
        found = r2.locate_in_run(self.store, PREFIX, RUN, [X_ID, THREADS_CODE])
        self.assertEqual({row["key"]: row["post_ids"] for row in found},
                         {with_x: [X_ID], with_t: [THREADS_CODE]})

        output = self.root / "pulled"
        self.pull(output)
        local = r2.locate_in_directory(output, [X_ID])
        self.assertEqual(len(local), 1)
        self.assertIn(os.sep + "x" + os.sep + "collector_x" + os.sep, local[0]["path"])
        for bad in ([], ["123"], ["has space"]):
            with self.subTest(ids=bad):
                with self.assertRaises(ValueError):
                    r2.post_id_pattern(bad)

    def test_write_keys_are_limited_to_their_own_bucket_and_reads_cover_all(self):
        storage = self.client.objects
        reader = FakeS3(storage, writable=set())
        x_side = r2.Store(BUCKETS, reader, FakeS3(storage, writable={BUCKETS["x"]}))
        threads_side = r2.Store(BUCKETS, reader, FakeS3(storage, writable={BUCKETS["threads"]}))
        read_only = r2.Store(BUCKETS, reader)

        x_file = self.source("collection_bundle_x_%s.json" % RUN, ('{"id": "%s"}' % X_ID).encode())
        t_file = self.source("collection_bundle_threads_%s.json" % RUN, THREADS_CODE.encode())
        x_key = self.upload(x_file, role="collector_x", store=x_side)["key"]
        t_key = self.upload(t_file, role="collector_threads", store=threads_side)["key"]

        with self.assertRaisesRegex(RuntimeError, "cannot write to the threads area"):
            self.upload(t_file, role="collector_threads", store=x_side)
        with self.assertRaisesRegex(RuntimeError, "cannot write to the x area"):
            self.upload(x_file, role="collector_x", store=threads_side)
        with self.assertRaisesRegex(RuntimeError, "cannot write to the both area"):
            self.upload(self.source("note.md"), store=x_side)
        with self.assertRaisesRegex(ValueError, "needs a write key"):
            self.upload(x_file, role="collector_x", store=read_only)

        # Both sides, and a read-only key, can read and pull everything.
        for store in (x_side, threads_side, read_only):
            with self.subTest(store=store):
                self.assertEqual({row["key"] for row in self.listing(store)}, {x_key, t_key})
        pulled = self.pull(self.root / "pulled", store=read_only)
        self.assertEqual({Path(row["path"]).read_bytes() for row in pulled},
                         {x_file.read_bytes(), t_file.read_bytes()})

        # Deletion follows the same split: each side deletes only in its own bucket.
        with self.assertRaisesRegex(RuntimeError, "cannot delete in bucket compsns-threads"):
            r2.delete_objects(x_side, PREFIX, RUN, [t_key], execute=True)
        with self.assertRaisesRegex(ValueError, "needs a write key"):
            r2.delete_objects(read_only, PREFIX, RUN, [x_key], execute=True)
        self.assertEqual(r2.delete_objects(read_only, PREFIX, RUN, [x_key])[0]["status"],
                         "would_delete")
        r2.delete_objects(threads_side, PREFIX, RUN, [t_key], execute=True)
        self.assertEqual({row["key"] for row in self.listing(read_only)}, {x_key})

    def test_object_in_another_platforms_bucket_is_flagged(self):
        body = b"misplaced"
        sha = hashlib.sha256(body).hexdigest()
        key = r2.object_key(PREFIX, RUN, "threads", "collector_threads", sha, "t.json")
        self.client.seed(key, body, bucket=BUCKETS["x"])
        rows = self.listing()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["irregular"])
        self.assertEqual(rows[0]["bucket"], BUCKETS["x"])
        self.assertIn("another platform", rows[0]["reason"])

    def test_single_bucket_mode_still_works(self):
        one = r2.Store({"x": "compsns-data", "threads": "compsns-data", "both": "compsns-data"},
                       self.client, self.client)
        self.assertFalse(one.partitioned)
        self.assertTrue(self.store.partitioned)
        row = self.upload(self.source("collection_bundle_x_R.json", b"x"), role="collector_x", store=one)
        self.upload(self.source("collection_bundle_threads_R.json", b"t"), role="collector_threads",
                    store=one)
        self.assertEqual(row["bucket"], "compsns-data")
        rows = self.listing(one)
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(row["irregular"] for row in rows))
        self.assertEqual(len(self.client.list_calls), 1)

    def test_environment_selects_buckets_and_separate_keys(self):
        from unittest import mock
        clean = {name: value for name, value in os.environ.items()
                 if not name.upper().startswith(("AWS_", "COMPSNS_"))}
        split = {"COMPSNS_R2_BUCKET_X": "compsns-x", "COMPSNS_R2_BUCKET_THREADS": "compsns-threads",
                 "COMPSNS_R2_BUCKET_BOTH": "compsns-both"}
        with mock.patch.dict(os.environ, dict(clean, **split), clear=True):
            self.assertEqual(r2._buckets_from_env(), BUCKETS)
        with mock.patch.dict(os.environ, dict(clean, COMPSNS_R2_BUCKET="compsns-data"), clear=True):
            self.assertEqual(set(r2._buckets_from_env().values()), {"compsns-data"})
        partial = dict(clean, COMPSNS_R2_BUCKET_X="compsns-x", COMPSNS_R2_BUCKET="compsns-data")
        with mock.patch.dict(os.environ, partial, clear=True):
            with self.assertRaisesRegex(ValueError, "Set all of"):
                r2._buckets_from_env()

        keys = dict(clean, COMPSNS_R2_WRITE_ACCESS_KEY_ID="w", COMPSNS_R2_WRITE_SECRET_ACCESS_KEY="ws",
                    COMPSNS_R2_READ_ACCESS_KEY_ID="r", COMPSNS_R2_READ_SECRET_ACCESS_KEY="rs")
        with mock.patch.dict(os.environ, keys, clear=True):
            self.assertEqual((r2._credentials("WRITE"), r2._credentials("READ")),
                             (("w", "ws"), ("r", "rs")))
        with mock.patch.dict(os.environ, dict(clean, AWS_ACCESS_KEY_ID="a", AWS_SECRET_ACCESS_KEY="as"),
                             clear=True):
            self.assertEqual((r2._credentials("WRITE"), r2._credentials("READ")), (("a", "as"), None))
        with mock.patch.dict(os.environ, dict(clean, COMPSNS_R2_READ_ACCESS_KEY_ID="r"), clear=True):
            with self.assertRaisesRegex(ValueError, "Set both"):
                r2._credentials("READ")

    def test_help_plan_and_local_locate_work_without_credentials_sdk_or_network(self):
        source = self.source(body=("offline planning bytes " + X_ID).encode())
        # A separate interpreter detects accidental eager SDK loading as well as
        # credential discovery or network access during help and local commands.
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
            if not name.upper().startswith(("AWS_", "R2_", "CLOUDFLARE_", "COMPSNS_"))
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        commands = [
            ["--help"],
            ["plan", "--run-id", RUN, "--role", "reading", "--file", str(source)],
            ["locate", "--local", str(self.root), "--id", X_ID],
        ]
        for arguments in commands:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", wrapper, str(TOOL)] + arguments,
                    cwd=REPO, env=environment, capture_output=True, text=True,
                    encoding="utf-8", timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if arguments[0] == "plan":
                    self.assertIn(hashlib.sha256(source.read_bytes()).hexdigest(), result.stdout)
                    self.assertIn(RUN + "/both/reading/", result.stdout)
                    self.assertIn(source.name, result.stdout)
                elif arguments[0] == "locate":
                    self.assertIn(source.name, result.stdout)
                    self.assertIn(X_ID, result.stdout)
                else:
                    self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
