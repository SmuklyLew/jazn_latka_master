from __future__ import annotations

from io import BytesIO
from pathlib import Path

from latka_jazn.tools import release_staging


class _Status:
    def to_dict(self) -> dict[str, object]:
        return {"status": "verified_export_without_git_history"}


class _FakeStdin:
    def __init__(self, process: "_FakeBatchProcess") -> None:
        self.process = process
        self.closed = False

    def write(self, data: bytes) -> int:
        assert self.process.pending_sha is None, (
            "a second cat-file request was written before the previous response was read"
        )
        sha = data.rstrip(b"\n").decode("ascii")
        assert sha in self.process.blobs
        self.process.pending_sha = sha
        self.process.requests.append(sha)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    def __init__(self, process: "_FakeBatchProcess") -> None:
        self.process = process

    def readline(self) -> bytes:
        sha = self.process.pending_sha
        assert sha is not None, "cat-file response requested before a SHA was written"
        blob = self.process.blobs[sha]
        self.process.pending_sha = None
        self.process.current_blob = blob
        self.process.separator_pending = False
        return f"{sha} blob {len(blob)}\n".encode("ascii")

    def read(self, size: int) -> bytes:
        if self.process.current_blob is not None:
            blob = self.process.current_blob
            assert size == len(blob)
            self.process.current_blob = None
            self.process.separator_pending = True
            return blob
        assert self.process.separator_pending and size == 1
        self.process.separator_pending = False
        return b"\n"


class _FakeBatchProcess:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.pending_sha: str | None = None
        self.current_blob: bytes | None = None
        self.separator_pending = False
        self.requests: list[str] = []
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)
        self.stderr = BytesIO()

    def wait(self) -> int:
        assert self.stdin.closed
        assert self.pending_sha is None
        assert self.current_blob is None
        assert not self.separator_pending
        return 0


def test_release_staging_interleaves_cat_file_requests_and_responses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    destination = tmp_path / "release"
    root.mkdir()

    head = "a" * 40
    first_sha = "1" * 40
    second_sha = "2" * 40
    blobs = {
        first_sha: b"first payload\n",
        second_sha: b"second payload\n",
    }
    listing = (
        f"100644 blob {first_sha}\tfirst.txt\0"
        f"100644 blob {second_sha}\tnested/second.txt\0"
    ).encode("utf-8")

    def fake_git(_root: Path, *args: str, binary: bool = False):
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("ls-tree", "-rz", head):
            assert binary is True
            return listing
        raise AssertionError(f"unexpected git invocation: {args!r}")

    process = _FakeBatchProcess(blobs)
    monkeypatch.setattr(release_staging, "_git", fake_git)
    monkeypatch.setattr(
        release_staging,
        "build_source_provenance_document",
        lambda *_args, **_kwargs: {
            "base_merge_commit": head,
            "git_tree_sha": "tree-sha",
        },
    )
    monkeypatch.setattr(
        release_staging.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        release_staging,
        "write_package_integrity_manifest",
        lambda *_args, **_kwargs: {"file_count": 2},
    )
    monkeypatch.setattr(
        release_staging,
        "verify_package_integrity_manifest",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        release_staging,
        "read_source_provenance",
        lambda *_args, **_kwargs: _Status(),
    )

    report = release_staging.create_release_staging(root, destination)

    assert report["ok"] is True
    assert report["tracked_file_count"] == 2
    assert process.requests == [first_sha, second_sha]
    assert (destination / "first.txt").read_bytes() == blobs[first_sha]
    assert (destination / "nested" / "second.txt").read_bytes() == blobs[second_sha]
