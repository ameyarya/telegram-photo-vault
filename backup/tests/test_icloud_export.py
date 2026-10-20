"""icloud_export.py: new photos move into the folder without ever overwriting an older photo with the same name."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_move_in_never_overwrites_and_keeps_pairs_together(tmp_path):
    ie = importlib.import_module("icloud_export")
    stage, dest = tmp_path / "stage", tmp_path / "iphone"
    stage.mkdir(), dest.mkdir()
    (dest / "IMG_8132.HEIC").write_bytes(b"old photo from 2024")        # iPhone numbers wrapped around
    (dest / "img_8133.mov").write_bytes(b"old clip")                    # case doesn't matter on exFAT
    for n, b in {"IMG_8132.HEIC": b"new", "IMG_8132.MOV": b"new clip", "IMG_8133.HEIC": b"new2",
                 "IMG_9000.HEIC": b"fresh"}.items():
        (stage / n).write_bytes(b)
    os.utime(stage / "IMG_9000.HEIC", (1700000000, 1700000000))
    moved = ie.move_in(stage, dest)
    assert sorted(moved) == ["IMG_8132 (2).HEIC", "IMG_8132 (2).MOV", "IMG_8133 (2).HEIC", "IMG_9000.HEIC"]
    assert (dest / "IMG_8132.HEIC").read_bytes() == b"old photo from 2024"           # untouched
    assert (dest / "IMG_8132 (2).MOV").read_bytes() == b"new clip"                   # pair keeps one number
    assert int((dest / "IMG_9000.HEIC").stat().st_mtime) == 1700000000               # date kept
    assert os.listdir(stage) == []
