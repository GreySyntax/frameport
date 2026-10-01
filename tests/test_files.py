import pytest

from frameport.install import files


def test_items_keep_folder_structure(tmp_path):
    (tmp_path / "Show/S1").mkdir(parents=True)
    (tmp_path / "Show/S1/e1.mp4").write_bytes(b"12345")
    (tmp_path / "movie.mkv").write_bytes(b"12")
    items = files._items([tmp_path / "Show", tmp_path / "movie.mkv"], "Films")
    assert [(i[1], i[2]) for i in items] == [("Films/Show/S1/e1.mp4", 5), ("Films/movie.mkv", 2)]
    with pytest.raises(FileNotFoundError):
        files._items([tmp_path / "missing"], "")


def test_subdir_stays_inside():
    assert files._safe_subdir("a\\b/./c/") == "a/b/c"
    with pytest.raises(ValueError):
        files._safe_subdir("../etc")
