import hashlib

import pytest

from leagueassistant import updater

NEW_EXE = b"new exe bytes"


class FakeResponse:
    def __init__(self, body: bytes, status=200):
        self.body, self.status_code = body, status
        self.text = body.decode(errors="replace")

    def raise_for_status(self):
        if self.status_code != 200:
            raise updater.requests.HTTPError(self.status_code)

    def iter_content(self, size):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def release(monkeypatch):
    files = {}

    def get(url, **kw):
        name = url.rsplit("/", 1)[-1]
        return FakeResponse(files[name]) if name in files else FakeResponse(b"", 404)

    monkeypatch.setattr(updater.requests, "get", get)
    return files


def install(tmp_path, release, commit="new", body=NEW_EXE, digest=None):
    exe = tmp_path / "leagueassistant.exe"
    exe.write_bytes(b"old exe bytes")
    release["build.txt"] = f"{commit} {digest or hashlib.sha256(body).hexdigest()}\n".encode()
    release["leagueassistant.exe"] = body
    restarts = []
    done = updater.update(["--no-browser"], exe=exe, current="old",
                          restart=lambda e, argv: restarts.append((e, argv)))
    return exe, done, restarts


def test_update_swaps_in_the_new_exe_and_restarts(tmp_path, release):
    exe, done, restarts = install(tmp_path, release)
    assert done and restarts == [(exe, ["--no-browser"])]
    assert exe.read_bytes() == NEW_EXE
    assert (tmp_path / "leagueassistant.exe.old").read_bytes() == b"old exe bytes"
    updater.cleanup(exe)
    assert not (tmp_path / "leagueassistant.exe.old").exists()


def test_no_update_when_already_on_the_latest_build(tmp_path, release):
    exe, done, restarts = install(tmp_path, release, commit="old")
    assert not done and not restarts and exe.read_bytes() == b"old exe bytes"


def test_bad_checksum_keeps_the_current_exe(tmp_path, release):
    exe, done, restarts = install(tmp_path, release, digest="0" * 64)
    assert not done and not restarts
    assert exe.read_bytes() == b"old exe bytes"
    assert not (tmp_path / "leagueassistant.exe.new").exists()


def test_missing_release_is_ignored(tmp_path, release):
    exe = tmp_path / "leagueassistant.exe"
    exe.write_bytes(b"old exe bytes")
    assert not updater.update([], exe=exe, current="old", restart=lambda *a: None)


def test_disabled_outside_the_exe():
    assert not updater.enabled()
