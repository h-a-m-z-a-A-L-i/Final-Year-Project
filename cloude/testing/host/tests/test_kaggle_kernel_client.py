import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from testing.host import kaggle_kernel_client as kkc


def test_parse_kaggle_edit_url():
    url = "https://www.kaggle.com/code/codekey/testing-onlll/edit"
    assert kkc.parse_kaggle_edit_url(url) == ("codekey", "testing-onlll")


def test_cache_and_lookup_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(kkc, "KERNEL_METADATA_DIR", tmp_path / "kernels")
    monkeypatch.setattr(kkc, "KERNEL_SLUG_INDEX_PATH", tmp_path / "slug_index.json")

    record = {
        "id_no": 112732919,
        "id": "codekey/testing-onlll",
        "owner": "codekey",
        "slug": "testing-onlll",
        "title": "testing-onlll",
    }
    kkc._cache_metadata(record)

    loaded = kkc.load_cached_metadata("codekey", "testing-onlll")
    assert loaded is not None
    assert loaded["id_no"] == 112732919

    resolved = kkc.resolve_kernel_id_for_url(
        "https://www.kaggle.com/code/codekey/testing-onlll/edit",
        allow_fetch=False,
    )
    assert resolved == 112732919


def test_import_local_kernel_metadata_file(tmp_path, monkeypatch):
    monkeypatch.setattr(kkc, "KERNEL_METADATA_DIR", tmp_path / "kernels")
    monkeypatch.setattr(kkc, "KERNEL_SLUG_INDEX_PATH", tmp_path / "slug_index.json")

    meta_path = tmp_path / "kernel-metadata.json"
    meta_path.write_text(
        '{"id": "codekey/testing-onlll", "id_no": 112732919, "title": "testing-onlll"}',
        encoding="utf-8",
    )
    imported = kkc.import_local_kernel_metadata_file(
        meta_path,
        url="https://www.kaggle.com/code/codekey/testing-onlll/edit",
    )
    assert imported is not None
    assert imported["id_no"] == 112732919
    assert kkc.resolve_kernel_id_for_url(
        "https://www.kaggle.com/code/codekey/testing-onlll/edit",
        allow_fetch=False,
    ) == 112732919
