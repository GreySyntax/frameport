// SPDX-License-Identifier: GPL-3.0-only
// FramePort OVRPlugin frame-loop shim for Unity 2017-2018 built-in Oculus support.
//
// That Unity drives OVRPlugin with the legacy frame loop: ovrp_Update2(render step, frame index, ...) on the main
// thread, then ovrp_BeginFrame / ovrp_EndFrame on the render thread. It never calls ovrp_WaitToBeginFrame, which
// newer OVRPlugin builds (OVRPort's OpenXR OVRPlugin) need to call xrWaitFrame: no frame ever begins, OVRPlugin logs
// "CompositorOpenXR::Update called for frame N outside of frame bounds" thousands of times a second and the Frame's
// dashboard freezes (Accounting+, Unity 2017.4).
//
// FramePort renames the "ovrp_Update2" lookup in libunity.so to "fpov_Update2" (same length) and adds this library to
// libOVRPlugin.so's DT_NEEDED, so Unity's dlsym on the plugin finds this function: it waits for the frame first (once
// per frame index, render step only), then calls the real ovrp_Update2.
#include <android/log.h>
#include <dlfcn.h>
#include <pthread.h>

#define TAG "FrameBridge"
#define EXPORT __attribute__((visibility("default")))
#define LOG(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

typedef int (*PFN_Update2)(int step, int frame_index, double prediction_seconds);
typedef int (*PFN_WaitToBeginFrame)(int frame_index);

static PFN_Update2 real_update2;
static PFN_WaitToBeginFrame real_wait;
static pthread_once_t once = PTHREAD_ONCE_INIT;

static void init(void) {
    void *ovrp = dlopen("libOVRPlugin.so", RTLD_NOW | RTLD_NOLOAD);
    if (!ovrp) ovrp = dlopen("libOVRPlugin.so", RTLD_NOW);
    if (ovrp) {
        real_update2 = (PFN_Update2)dlsym(ovrp, "ovrp_Update2");
        real_wait = (PFN_WaitToBeginFrame)dlsym(ovrp, "ovrp_WaitToBeginFrame");
    }
    LOG("ovrp frame loop shim: ovrp_Update2 %s, ovrp_WaitToBeginFrame %s", real_update2 ? "OK" : "MISSING",
        real_wait ? "OK" : "MISSING");
}

#define STEP_RENDER (-1)  // ovrpStep_Render

EXPORT int fpov_Update2(int step, int frame_index, double prediction_seconds) {
    pthread_once(&once, init);
    static int last_waited = -1, logged;
    if (step == STEP_RENDER && real_wait && frame_index != last_waited) {
        last_waited = frame_index;
        int r = real_wait(frame_index);
        if (logged++ < 3) LOG("ovrp frame loop shim: waited for frame %d: %d", frame_index, r);
    }
    return real_update2 ? real_update2(step, frame_index, prediction_seconds) : -1000;  // ovrpFailure
}
