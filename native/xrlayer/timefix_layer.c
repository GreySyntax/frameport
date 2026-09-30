/* FramePort timefix: an OpenXR API layer for Windows PC VR games running under Proton on the Steam Frame.
 *
 * Proton's wineopenxr implements XR_KHR_win32_convert_performance_counter_time (required by Revive's OpenXR backend)
 * on top of the host's XR_KHR_convert_timespec_time, but the Frame runtime returns XR_ERROR_FUNCTION_UNSUPPORTED for
 * xrConvertTimespecTimeToTimeKHR / xrConvertTimeToTimespecTimeKHR. This layer supplies them with the same emulation
 * the FrameBridge adapter uses for Quest games: XrTime = CLOCK_MONOTONIC + offset, where the offset is measured at
 * every xrWaitFrame (predictedDisplayTime - predictedDisplayPeriod - now). The runtime's own functions are preferred
 * whenever they work. The layer also advertises XR_KHR_convert_timespec_time (manifest) and removes it from
 * xrCreateInstance if the runtime rejects it.
 *
 * OpenXR version fallback: the Frame's SteamVR runtime rejects apiVersion 1.1 with XR_ERROR_API_VERSION_UNSUPPORTED
 * (checked 2026-09-29; 1.0 works), which is what Proton 11's VR helper requests, so VR never started for Proton games
 * (flat window / "Unable to load LibOVRRT DLL" under Revive). The layer retries such a create as a 1.0 app.
 *
 * Enabled per game by FramePort's Proton launcher: XR_API_LAYER_PATH=<game>/xrlayer,
 * XR_ENABLE_API_LAYERS=XR_APILAYER_FRAMEPORT_timefix. Built freestanding (see ../build.py).
 */
#define XR_USE_TIMESPEC 1
#include <time.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <openxr/openxr_loader_negotiation.h>

#define EXPORT __attribute__((visibility("default")))
#define LAYER_NAME "XR_APILAYER_FRAMEPORT_timefix"
#define TIMESPEC_EXT "XR_KHR_convert_timespec_time"
#define MAX_EXTS 256

static PFN_xrGetInstanceProcAddr next_gipa;
static PFN_xrConvertTimespecTimeToTimeKHR runtime_timespec_to_time;
static PFN_xrConvertTimeToTimespecTimeKHR runtime_time_to_timespec;
static PFN_xrWaitFrame runtime_wait_frame;
static volatile long long time_offset;
static volatile int calibrated;

static int str_eq(const char *a, const char *b) {
    if (!a || !b) return 0;
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static long long mono_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (long long)now.tv_sec * 1000000000ll + now.tv_nsec;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_timespec_to_time(XrInstance instance, const struct timespec *ts, XrTime *time) {
    if (runtime_timespec_to_time) {
        XrResult r = runtime_timespec_to_time(instance, ts, time);
        if (r != XR_ERROR_FUNCTION_UNSUPPORTED) return r;
    }
    if (!ts || !time) return XR_ERROR_VALIDATION_FAILURE;
    *time = (XrTime)((long long)ts->tv_sec * 1000000000ll + ts->tv_nsec + (calibrated ? time_offset : 0));
    return XR_SUCCESS;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_time_to_timespec(XrInstance instance, XrTime time, struct timespec *ts) {
    if (runtime_time_to_timespec) {
        XrResult r = runtime_time_to_timespec(instance, time, ts);
        if (r != XR_ERROR_FUNCTION_UNSUPPORTED) return r;
    }
    if (!ts) return XR_ERROR_VALIDATION_FAILURE;
    long long mono = (long long)time - (calibrated ? time_offset : 0);
    ts->tv_sec = (time_t)(mono / 1000000000ll);
    ts->tv_nsec = (long)(mono % 1000000000ll);
    return XR_SUCCESS;
}

static XRAPI_ATTR XrResult XRAPI_CALL layer_wait_frame(XrSession session, const XrFrameWaitInfo *info, XrFrameState *state) {
    if (!runtime_wait_frame) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult r = runtime_wait_frame(session, info, state);
    if (XR_SUCCEEDED(r) && state && state->predictedDisplayTime) {
        // predictedDisplayTime is about one display period ahead of "now"
        time_offset = (long long)(state->predictedDisplayTime - state->predictedDisplayPeriod) - mono_ns();
        calibrated = 1;
    }
    return r;
}

static XRAPI_ATTR XrResult XRAPI_CALL layer_get_instance_proc_addr(XrInstance instance, const char *name,
                                                                   PFN_xrVoidFunction *function) {
    if (!function || !name) return XR_ERROR_VALIDATION_FAILURE;
    if (!next_gipa) return XR_ERROR_HANDLE_INVALID;
    if (str_eq(name, "xrConvertTimespecTimeToTimeKHR") || str_eq(name, "xrConvertTimeToTimespecTimeKHR")) {
        PFN_xrVoidFunction real = 0;
        if (instance == XR_NULL_HANDLE) return XR_ERROR_HANDLE_INVALID;
        if (XR_FAILED(next_gipa(instance, name, &real))) real = 0;
        if (str_eq(name, "xrConvertTimespecTimeToTimeKHR")) {
            runtime_timespec_to_time = (PFN_xrConvertTimespecTimeToTimeKHR)real;
            *function = (PFN_xrVoidFunction)emu_timespec_to_time;
        } else {
            runtime_time_to_timespec = (PFN_xrConvertTimeToTimespecTimeKHR)real;
            *function = (PFN_xrVoidFunction)emu_time_to_timespec;
        }
        return XR_SUCCESS;
    }
    if (str_eq(name, "xrWaitFrame") && instance != XR_NULL_HANDLE) {
        XrResult r = next_gipa(instance, name, (PFN_xrVoidFunction *)&runtime_wait_frame);
        if (XR_FAILED(r)) return r;
        *function = (PFN_xrVoidFunction)layer_wait_frame;
        return XR_SUCCESS;
    }
    return next_gipa(instance, name, function);
}

static XrResult create_stripped(XrApiLayerNextInfo *next, const XrInstanceCreateInfo *info,
                                const XrApiLayerCreateInfo *chained, XrInstance *instance) {
    XrResult r = next->nextCreateApiLayerInstance(info, chained, instance);
    if (r != XR_ERROR_EXTENSION_NOT_PRESENT) return r;
    // The runtime doesn't know XR_KHR_convert_timespec_time: we provide it, so don't pass it down.
    const char *exts[MAX_EXTS];
    uint32_t n = 0, dropped = 0;
    for (uint32_t i = 0; i < info->enabledExtensionCount && n < MAX_EXTS; ++i) {
        if (str_eq(info->enabledExtensionNames[i], TIMESPEC_EXT)) { ++dropped; continue; }
        exts[n++] = info->enabledExtensionNames[i];
    }
    if (!dropped) return r;
    XrInstanceCreateInfo stripped = *info;
    stripped.enabledExtensionCount = n;
    stripped.enabledExtensionNames = exts;
    return next->nextCreateApiLayerInstance(&stripped, chained, instance);
}

static XRAPI_ATTR XrResult XRAPI_CALL layer_create_instance(const XrInstanceCreateInfo *info,
                                                            const XrApiLayerCreateInfo *layer_info, XrInstance *instance) {
    if (!layer_info || !layer_info->nextInfo || !info) return XR_ERROR_INITIALIZATION_FAILED;
    XrApiLayerNextInfo *next = layer_info->nextInfo;
    next_gipa = next->nextGetInstanceProcAddr;
    XrApiLayerCreateInfo chained = *layer_info;
    chained.nextInfo = next->next;
    XrResult r = create_stripped(next, info, &chained, instance);
    if (r != XR_ERROR_API_VERSION_UNSUPPORTED || XR_VERSION_MAJOR(info->applicationInfo.apiVersion) != 1 ||
        XR_VERSION_MINOR(info->applicationInfo.apiVersion) == 0)
        return r;
    // The Frame's SteamVR runtime only accepts OpenXR 1.0, but Proton 11's VR helper (and newer apps) ask for 1.1:
    // retry as a 1.0 app (1.1 core functions the runtime lacks are simply not returned by xrGetInstanceProcAddr).
    XrInstanceCreateInfo v10 = *info;
    v10.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    return create_stripped(next, &v10, &chained, instance);
}

EXPORT XRAPI_ATTR XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(const XrNegotiateLoaderInfo *loader_info,
                                                                         const char *layer_name,
                                                                         XrNegotiateApiLayerRequest *request) {
    if (!loader_info || !request || loader_info->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        request->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST ||
        loader_info->minInterfaceVersion > XR_CURRENT_LOADER_API_LAYER_VERSION ||
        loader_info->maxInterfaceVersion < XR_CURRENT_LOADER_API_LAYER_VERSION)
        return XR_ERROR_INITIALIZATION_FAILED;
    if (layer_name && !str_eq(layer_name, LAYER_NAME)) return XR_ERROR_INITIALIZATION_FAILED;
    request->layerInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
    request->layerApiVersion = XR_CURRENT_API_VERSION;
    request->getInstanceProcAddr = layer_get_instance_proc_addr;
    request->createApiLayerInstance = layer_create_instance;
    return XR_SUCCESS;
}
