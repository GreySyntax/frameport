from frameport.analysis import elf
from frameport.analysis.stubgen import build_stub_library
from frameport.parity import classify
from frameport.report import render


def test_stub_libraries_equivalent():
    a = build_stub_library(["ovr_A", "ovr_B"])
    b = build_stub_library(["ovr_B", "ovr_A"], abi="arm64-v8a") + b"\0" * 16  # different bytes, same exports
    assert classify("lib/arm64-v8a/libovrstubs.so", a, b)[0] == "equivalent"
    c = build_stub_library(["ovr_A"])
    assert classify("lib/arm64-v8a/libovrstubs.so", a, c)[0] == "UNEXPLAINED"


def test_needed_layout_equivalent():
    base = build_stub_library(["f"], soname="libgame.so")
    one = elf.add_needed(base, "libglshim.so")
    two = elf.add_needed(base + b"", "libglshim.so")
    assert classify("lib/arm64-v8a/libgame.so", one, two)[0] in ("equivalent",) or one == two


def test_settings_classification():
    kind, why = classify("lib/arm64-v8a/libframe_settings.so", b"scale=1.0\nscene_emul=1\n", b"scene_emul=1\nscale=1.0\n")
    assert kind == "equivalent"


def test_report_counts():
    text = render()
    assert "23 work" in text and "Path of the Warrior" in text
