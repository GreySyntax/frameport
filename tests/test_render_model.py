"""FrameBridge's XR_FB_render_model emulation (native/adapter/render_model.c), compiled for this host with a small C
harness that stands in for the adapter (lookup, xrStringToPath, logging).
Opt-in (FRAMEPORT_NATIVE_TESTS=1, -m native): needs the OpenXR headers in native/.cache (from native/build.py)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INC = next(iter((ROOT / "native/.cache").glob("openxr-*")), None)
CC = shutil.which("cc") or shutil.which("clang")
pytestmark = [pytest.mark.native, pytest.mark.skipif(
    not os.environ.get("FRAMEPORT_NATIVE_TESTS") or not INC or not CC,
    reason="set FRAMEPORT_NATIVE_TESTS=1 (needs the OpenXR headers from native/build.py and a host C compiler)")]

HARNESS = r"""
#define XR_EXTENSION_PROTOTYPES
#include <openxr/openxr.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) (fprintf(stderr, __VA_ARGS__), fputc('\n', stderr))
static XrInstance active_instance = (XrInstance)(uintptr_t)1;
static XRAPI_ATTR XrResult XRAPI_CALL fake_string_to_path(XrInstance i, const char *s, XrPath *out) {
    (void)i; *out = 0; for (const char *c = s; *c; ++c) *out = *out * 131 + (unsigned char)*c; return XR_SUCCESS;
}
static PFN_xrVoidFunction lookup(XrInstance i, const char *name) {
    (void)i; return !strcmp(name, "xrStringToPath") ? (PFN_xrVoidFunction)fake_string_to_path : NULL;
}
#include "render_model.c"
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAILED line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(int argc, char **argv) {
    render_model_scan(argv[1]);
    if (argc > 2) { CHECK(!render_models_available); return 0; }
    CHECK(render_models_available);
    emulate_render_model = 1;
    PFN_xrEnumerateRenderModelPathsFB enumerate =
        (PFN_xrEnumerateRenderModelPathsFB)render_model_emulation("xrEnumerateRenderModelPathsFB");
    PFN_xrGetRenderModelPropertiesFB props_fn =
        (PFN_xrGetRenderModelPropertiesFB)render_model_emulation("xrGetRenderModelPropertiesFB");
    PFN_xrLoadRenderModelFB load = (PFN_xrLoadRenderModelFB)render_model_emulation("xrLoadRenderModelFB");
    CHECK(enumerate && props_fn && load && !render_model_emulation("xrCreatePassthroughFB"));
    uint32_t n = 0;
    CHECK(enumerate(XR_NULL_HANDLE, 0, &n, NULL) == XR_SUCCESS && n == 2);
    XrRenderModelPathInfoFB paths[2] = {{XR_TYPE_RENDER_MODEL_PATH_INFO_FB}, {XR_TYPE_RENDER_MODEL_PATH_INFO_FB}};
    CHECK(enumerate(XR_NULL_HANDLE, 1, &n, paths) == XR_ERROR_SIZE_INSUFFICIENT);
    CHECK(enumerate(XR_NULL_HANDLE, 2, &n, paths) == XR_SUCCESS);
    XrPath right; fake_string_to_path(0, "/model_fb/controller/right", &right);
    CHECK(paths[1].path == right);
    XrRenderModelCapabilitiesRequestFB req = {XR_TYPE_RENDER_MODEL_CAPABILITIES_REQUEST_FB, NULL,
                                              XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_2_BIT_FB};
    XrRenderModelPropertiesFB props = {XR_TYPE_RENDER_MODEL_PROPERTIES_FB, &req};
    CHECK(props_fn(XR_NULL_HANDLE, right, &props) == XR_SUCCESS);
    CHECK(props.vendorId == 0x28DE && strstr(props.modelName, "Right")
          && props.modelKey != XR_NULL_RENDER_MODEL_KEY_FB);
    CHECK(props.flags == XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_2_BIT_FB);
    XrRenderModelPropertiesFB bad = {XR_TYPE_RENDER_MODEL_PROPERTIES_FB};
    CHECK(props_fn(XR_NULL_HANDLE, 12345, &bad) == XR_ERROR_PATH_UNSUPPORTED
          && bad.modelKey == XR_NULL_RENDER_MODEL_KEY_FB);
    XrRenderModelLoadInfoFB info = {XR_TYPE_RENDER_MODEL_LOAD_INFO_FB, NULL, props.modelKey};
    XrRenderModelBufferFB buf = {XR_TYPE_RENDER_MODEL_BUFFER_FB};
    CHECK(load(XR_NULL_HANDLE, &info, &buf) == XR_SUCCESS && buf.bufferCountOutput == 11);
    uint8_t small[4]; buf.bufferCapacityInput = 4; buf.buffer = small;
    CHECK(load(XR_NULL_HANDLE, &info, &buf) == XR_ERROR_SIZE_INSUFFICIENT);
    uint8_t data[64] = {0}; buf.bufferCapacityInput = sizeof(data); buf.buffer = data;
    CHECK(load(XR_NULL_HANDLE, &info, &buf) == XR_SUCCESS && !memcmp(data, "right-model", 11));
    XrRenderModelLoadInfoFB wrong = {XR_TYPE_RENDER_MODEL_LOAD_INFO_FB, NULL, 7};
    CHECK(load(XR_NULL_HANDLE, &wrong, &buf) == XR_ERROR_RENDER_MODEL_KEY_INVALID_FB);
    emulate_render_model = 0;
    CHECK(!render_model_emulation("xrLoadRenderModelFB"));
    return 0;
}
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    d = tmp_path_factory.mktemp("render_model")
    (d / "harness.c").write_text(HARNESS)
    subprocess.run([CC, "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-Wno-unused-variable", "-Wno-missing-field-initializers", "-I", str(INC),
                    "-I", str(ROOT / "native/adapter"),
                    str(d / "harness.c"), "-o", str(d / "harness")], check=True)
    return d / "harness"


def test_serves_controller_models(harness, tmp_path):
    (tmp_path / "controller_left.glb").write_bytes(b"left-model")
    (tmp_path / "controller_right.glb").write_bytes(b"right-model")
    p = subprocess.run([str(harness), str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_needs_both_models(harness, tmp_path):
    (tmp_path / "controller_left.glb").write_bytes(b"left-model")
    p = subprocess.run([str(harness), str(tmp_path), "expect-missing"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
