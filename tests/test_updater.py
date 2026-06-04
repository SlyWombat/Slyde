"""Device-side self-update helpers (verify / safe-extract / apply)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from memento_emulator.updater import UpdateError, apply_update, stage_bundle, verify_md5


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buf.getvalue()


def test_verify_md5_ok_and_mismatch() -> None:
    data = b"hello world"
    verify_md5(data, hashlib.md5(data).hexdigest())
    with pytest.raises(UpdateError, match="md5 mismatch"):
        verify_md5(data, "deadbeef")


def test_stage_bundle_extracts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    names = stage_bundle(_zip({"app/x.py": "print(1)", "VERSION": "1.2.3"}), tmp_path / "out")
    assert (tmp_path / "out" / "VERSION").read_text() == "1.2.3"
    assert "app/x.py" in names


def test_stage_bundle_rejects_traversal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UpdateError, match="unsafe"):
        stage_bundle(_zip({"../evil.py": "x"}), tmp_path / "out")


def test_apply_update_downloads_verifies_stages_restarts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    data = _zip({"VERSION": "9.9.9"})
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(data)
    digest = hashlib.md5(data).hexdigest()
    restarts: list[int] = []
    members = apply_update(
        bundle.as_uri(), digest, target_dir=tmp_path / "app", restart=lambda: restarts.append(1)
    )
    assert (tmp_path / "app" / "VERSION").read_text() == "9.9.9"
    assert "VERSION" in members
    assert restarts == [1]


def test_apply_update_rejects_bad_md5(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(_zip({"VERSION": "1"}))
    with pytest.raises(UpdateError, match="md5 mismatch"):
        apply_update(bundle.as_uri(), "deadbeef", target_dir=tmp_path / "app")
