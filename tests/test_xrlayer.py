"""The timefix OpenXR layer (native/xrlayer), compiled for this host and driven through ctypes as a fake loader +
runtime: negotiation, extension stripping, time conversion emulation and calibration at xrWaitFrame.
Opt-in (FRAMEPORT_NATIVE_TESTS=1, -m native): needs the NDK in native/.cache (from native/build.py)."""
import ctypes as C
import os
import platform
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLANG = next(iter((ROOT / "native/.cache").glob("ndk-*/toolchains/llvm/prebuilt/*/bin/clang")), None)
INC = next(iter((ROOT / "native/.cache").glob("openxr-*")), None)
pytestmark = [pytest.mark.native, pytest.mark.skipif(
    not os.environ.get("FRAMEPORT_NATIVE_TESTS") or not CLANG or not INC or platform.machine() != "x86_64",
    reason="set FRAMEPORT_NATIVE_TESTS=1 (needs the NDK from native/build.py on an x86_64 host)")]

XR_SUCCESS, XR_ERROR_FUNCTION_UNSUPPORTED, XR_ERROR_EXTENSION_NOT_PRESENT = 0, -7, -9
LOADER_INFO, API_LAYER_REQUEST, CREATE_INFO, NEXT_INFO = 1, 2, 4, 5  # XrLoaderInterfaceStructs


class Timespec(C.Structure):
    _fields_ = [("tv_sec", C.c_long), ("tv_nsec", C.c_long)]


class InstanceCreateInfo(C.Structure):
    _fields_ = [("type", C.c_int), ("next", C.c_void_p), ("createFlags", C.c_uint64),
                ("applicationInfo", C.c_char * 272), ("enabledApiLayerCount", C.c_uint32),
                ("enabledApiLayerNames", C.c_void_p), ("enabledExtensionCount", C.c_uint32),
                ("enabledExtensionNames", C.POINTER(C.c_char_p))]


PFN = C.c_void_p
GIPA = C.CFUNCTYPE(C.c_int, C.c_uint64, C.c_char_p, C.POINTER(C.c_void_p))
CREATE = C.CFUNCTYPE(C.c_int, C.POINTER(InstanceCreateInfo), C.c_void_p, C.POINTER(C.c_uint64))


class NextInfo(C.Structure):
    pass


NextInfo._fields_ = [("structType", C.c_int), ("structVersion", C.c_uint32), ("structSize", C.c_size_t),
                     ("layerName", C.c_char * 256), ("nextGetInstanceProcAddr", GIPA),
                     ("nextCreateApiLayerInstance", CREATE), ("next", C.POINTER(NextInfo))]


class LayerCreateInfo(C.Structure):
    _fields_ = [("structType", C.c_int), ("structVersion", C.c_uint32), ("structSize", C.c_size_t),
                ("loaderInstance", C.c_void_p), ("settings_file_location", C.c_char * 512),
                ("nextInfo", C.POINTER(NextInfo))]


class LoaderInfo(C.Structure):
    _fields_ = [("structType", C.c_int), ("structVersion", C.c_uint32), ("structSize", C.c_size_t),
                ("minInterfaceVersion", C.c_uint32), ("maxInterfaceVersion", C.c_uint32),
                ("minApiVersion", C.c_uint64), ("maxApiVersion", C.c_uint64)]


class LayerRequest(C.Structure):
    _fields_ = [("structType", C.c_int), ("structVersion", C.c_uint32), ("structSize", C.c_size_t),
                ("layerInterfaceVersion", C.c_uint32), ("layerApiVersion", C.c_uint64),
                ("getInstanceProcAddr", C.c_void_p), ("createApiLayerInstance", C.c_void_p)]


class FrameState(C.Structure):
    _fields_ = [("type", C.c_int), ("next", C.c_void_p), ("predictedDisplayTime", C.c_int64),
                ("predictedDisplayPeriod", C.c_int64), ("shouldRender", C.c_uint32)]


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    out = tmp_path_factory.mktemp("xrlayer") / "timefix.so"
    src = ROOT / "native/xrlayer"
    subprocess.run([str(CLANG), "--target=x86_64-linux-gnu", "-ffreestanding", "-nostdlibinc", "-fno-stack-protector",
                    "-fvisibility=hidden", "-fPIC", "-O2", "-Wall", "-Werror", "-I", str(src / "include"), "-I",
                    str(INC), "-shared", "-nostdlib", "-fuse-ld=lld", str(src / "timefix_layer.c"), "-o", str(out)],
                   check=True)
    return C.CDLL(str(out), mode=C.RTLD_GLOBAL)


def negotiate(lib):
    li = LoaderInfo(LOADER_INFO, 1, C.sizeof(LoaderInfo), 1, 1, 0, 0)
    req = LayerRequest(API_LAYER_REQUEST, 1, C.sizeof(LayerRequest))
    assert lib.xrNegotiateLoaderApiLayerInterface(C.byref(li), b"XR_APILAYER_FRAMEPORT_timefix", C.byref(req)) == 0
    assert lib.xrNegotiateLoaderApiLayerInterface(C.byref(li), b"other", C.byref(req)) != 0
    layer_create = C.CFUNCTYPE(C.c_int, C.POINTER(InstanceCreateInfo), C.POINTER(LayerCreateInfo),
                               C.POINTER(C.c_uint64))
    return GIPA(req.getInstanceProcAddr), layer_create(req.createApiLayerInstance)


def test_layer_end_to_end(layer):
    gipa, create = negotiate(layer)
    seen = []

    @CREATE
    def runtime_create(info, layer_info, instance):
        names = [info.contents.enabledExtensionNames[i] for i in range(info.contents.enabledExtensionCount)]
        seen.append(names)
        if b"XR_KHR_convert_timespec_time" in names:
            return XR_ERROR_EXTENSION_NOT_PRESENT  # like the Frame runtime
        instance[0] = 42
        return XR_SUCCESS

    CONV = C.CFUNCTYPE(C.c_int, C.c_uint64, C.POINTER(Timespec), C.POINTER(C.c_int64))
    WAIT = C.CFUNCTYPE(C.c_int, C.c_uint64, C.c_void_p, C.POINTER(FrameState))

    @CONV
    def runtime_convert(inst, ts, t):
        return XR_ERROR_FUNCTION_UNSUPPORTED  # like the Frame runtime

    @WAIT
    def runtime_wait(session, info, state):
        state[0].predictedDisplayTime = 10**15  # the runtime's clock: far from CLOCK_MONOTONIC
        state[0].predictedDisplayPeriod = 13_888_888
        return XR_SUCCESS

    @GIPA
    def runtime_gipa(inst, name, fn):
        table = {b"xrConvertTimespecTimeToTimeKHR": runtime_convert, b"xrWaitFrame": runtime_wait}
        if name in table:
            fn[0] = C.cast(table[name], C.c_void_p).value
            return XR_SUCCESS
        return XR_ERROR_FUNCTION_UNSUPPORTED

    nxt = NextInfo(NEXT_INFO, 1, C.sizeof(NextInfo), b"runtime", runtime_gipa, runtime_create, None)
    li = LayerCreateInfo(CREATE_INFO, 1, C.sizeof(LayerCreateInfo), None, b"", C.pointer(nxt))
    exts = (C.c_char_p * 2)(b"XR_KHR_vulkan_enable", b"XR_KHR_convert_timespec_time")
    info = InstanceCreateInfo(3, None, 0, b"", 0, None, 2, exts)
    inst = C.c_uint64()
    assert create(C.byref(info), C.byref(li), C.byref(inst)) == XR_SUCCESS and inst.value == 42
    assert seen == [[b"XR_KHR_vulkan_enable", b"XR_KHR_convert_timespec_time"], [b"XR_KHR_vulkan_enable"]]

    fn = C.c_void_p()
    assert gipa(42, b"xrConvertTimespecTimeToTimeKHR", C.byref(fn)) == XR_SUCCESS
    to_time = CONV(fn.value)
    assert gipa(42, b"xrConvertTimeToTimespecTimeKHR", C.byref(fn)) == XR_SUCCESS
    to_ts = C.CFUNCTYPE(C.c_int, C.c_uint64, C.c_int64, C.POINTER(Timespec))(fn.value)
    assert gipa(42, b"xrWaitFrame", C.byref(fn)) == XR_SUCCESS
    wait = WAIT(fn.value)

    t = C.c_int64()
    assert to_time(42, C.byref(Timespec(5, 7)), C.byref(t)) == XR_SUCCESS and t.value == 5_000_000_007  # uncalibrated
    st = FrameState()
    assert wait(42, None, C.byref(st)) == XR_SUCCESS
    now = Timespec()
    C.CDLL(None).clock_gettime(1, C.byref(now))
    assert to_time(42, C.byref(now), C.byref(t)) == XR_SUCCESS
    expected = 10**15 - 13_888_888  # "now" in runtime time right after xrWaitFrame
    assert abs(t.value - expected) < 50_000_000, t.value - expected
    back = Timespec()
    assert to_ts(42, t.value, C.byref(back)) == XR_SUCCESS
    assert (back.tv_sec, back.tv_nsec) == (now.tv_sec, now.tv_nsec)
    # everything else passes through to the next layer/runtime
    assert gipa(42, b"xrEndFrame", C.byref(fn)) == XR_ERROR_FUNCTION_UNSUPPORTED


def test_layer_api_version_fallback(layer):
    """The Frame's SteamVR runtime rejects apiVersion 1.1 (XR_ERROR_API_VERSION_UNSUPPORTED): retried as 1.0."""
    import struct

    _, create = negotiate(layer)
    versions = []

    @CREATE
    def runtime_create(info, layer_info, instance):
        app_addr = C.addressof(info.contents) + InstanceCreateInfo.applicationInfo.offset
        v = C.c_uint64.from_address(app_addr + 264).value  # XrApplicationInfo.apiVersion
        versions.append((v >> 48, (v >> 32) & 0xFFFF))
        if versions[-1] != (1, 0):
            return -4  # XR_ERROR_API_VERSION_UNSUPPORTED
        instance[0] = 7
        return XR_SUCCESS

    @GIPA
    def runtime_gipa(inst, name, fn):
        return XR_ERROR_FUNCTION_UNSUPPORTED

    nxt = NextInfo(NEXT_INFO, 1, C.sizeof(NextInfo), b"runtime", runtime_gipa, runtime_create, None)
    li = LayerCreateInfo(CREATE_INFO, 1, C.sizeof(LayerCreateInfo), None, b"", C.pointer(nxt))
    for requested, expect in (((1, 1), [(1, 1), (1, 0)]), ((1, 0), [(1, 0)])):
        versions.clear()
        app = bytearray(272)
        struct.pack_into("<Q", app, 264, (requested[0] << 48) | (requested[1] << 32))
        info = InstanceCreateInfo(3, None, 0, b"", 0, None, 0, None)
        C.memmove(C.addressof(info) + InstanceCreateInfo.applicationInfo.offset, bytes(app), len(app))
        inst = C.c_uint64()
        assert create(C.byref(info), C.byref(li), C.byref(inst)) == XR_SUCCESS and inst.value == 7
        assert versions == expect
