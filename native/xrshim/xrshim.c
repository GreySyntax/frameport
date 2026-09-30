// SPDX-License-Identifier: GPL-3.0-only
// FrameBridge extension shim: lets engine plugins reach OpenXR functions that FrameBridge emulates but overport's
// dispatcher (libopenxr_loader.so) doesn't know. overport answers xrGetInstanceProcAddr from a fixed table and returns
// "Unknown proc addr" for anything else (e.g. the XR_FB_render_model functions), so the adapter underneath never sees
// the request.
//
// Meta's OVRPlugin finds xrGetInstanceProcAddr with dlopen("libopenxr_loader.so") + dlsym, so FramePort points that
// string in libOVRPlugin.so at this library instead (patches/frame/adapter.py). Names the adapter emulates are served
// by the adapter (framebridge_extension_proc); everything else goes to overport's xrGetInstanceProcAddr unchanged.
#include <openxr/openxr.h>
#include <android/log.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stddef.h>

#define TAG "FrameBridge"
#define EXPORT __attribute__((visibility("default")))

typedef PFN_xrVoidFunction (*PFN_extension_proc)(const char *name);

static PFN_xrGetInstanceProcAddr overport_gipa;
static pthread_once_t once = PTHREAD_ONCE_INIT;

static void resolve_overport(void) {
    void *h = dlopen("libopenxr_loader.so", RTLD_NOW | RTLD_NOLOAD);
    if (!h) h = dlopen("libopenxr_loader.so", RTLD_NOW);
    if (h) overport_gipa = (PFN_xrGetInstanceProcAddr)dlsym(h, "xrGetInstanceProcAddr");
    __android_log_print(ANDROID_LOG_INFO, TAG, "extension shim: overport xrGetInstanceProcAddr %s",
                        overport_gipa ? "OK" : "MISSING");
}

static PFN_xrVoidFunction adapter_proc(const char *name) {
    static PFN_extension_proc fn;
    if (!fn) {
        // Loaded by overport's dispatcher once an instance exists; NOLOAD so the shim never loads it by itself.
        void *h = dlopen("libopenxr_loader_generic.so", RTLD_NOW | RTLD_NOLOAD);
        if (h) fn = (PFN_extension_proc)dlsym(h, "framebridge_extension_proc");
        if (!fn) return NULL;
    }
    return fn(name);
}

EXPORT XRAPI_ATTR XrResult XRAPI_CALL xrGetInstanceProcAddr(XrInstance instance, const char *name,
                                                            PFN_xrVoidFunction *function) {
    pthread_once(&once, resolve_overport);
    if (name && function && instance != XR_NULL_HANDLE) {
        PFN_xrVoidFunction emulated = adapter_proc(name);
        if (emulated) {
            __android_log_print(ANDROID_LOG_INFO, TAG, "extension shim: %s -> FrameBridge", name);
            *function = emulated;
            return XR_SUCCESS;
        }
    }
    if (!overport_gipa) return XR_ERROR_INITIALIZATION_FAILED;
    return overport_gipa(instance, name, function);
}
