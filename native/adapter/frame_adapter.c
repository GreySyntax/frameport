// SPDX-License-Identifier: GPL-3.0-only
// Steam Frame OpenXR adapter for overport-patched Quest games.
//
// Installed as lib/arm64-v8a/libopenxr_loader_generic.so; overport's real generic
// loader is renamed to libopenxr_loader_original.so and loaded from the same dir.
// Hook technique follows Quest2Frame's frame_bridge.c (GPL-3.0-only,
// github.com/MichaelScottsman/Quest2Frame).
//
// Fixes applied:
//  * adds XR_KHR_android_create_instance when the app chains
//    XrInstanceCreateInfoAndroidKHR but forgets to enable the extension;
//  * hides foveation extensions and strips XrSwapchainCreateInfoFoveationFB
//    (foveation_fix);
//  * reports Valve / generic controller profiles as Oculus Touch and suppresses
//    synthetic hand-tracking data (controller_fix);
//  * scales the recommended eye-buffer size (scale);
//  * drops requested instance extensions the runtime lacks (e.g. XR_FB_passthrough);
//  * retries rejected swapchains with 1 sample / a supported format (swapchain_fix, overport #71);
//  * drops composition layers that reference swapchains that failed to create (layer_fix);
//  * optionally requests mutable-format swapchain images (mutable_fix, off by default);
//  * optionally swaps left/right images of stereo projection layers (swap_eyes, off by default);
//  * emulates XR_FB_passthrough with XR_ENVIRONMENT_BLEND_MODE_ALPHA_BLEND when the runtime lacks it
//    (passthrough_emul);
//  * optionally tells the app its reference spaces changed once the session is focused, so apps that
//    created them before tracking started recreate them (respace_kick);
//  * optionally flips quad layers vertically for apps whose UI panels show upside down (flip_quads);
//  * optionally serves Steam Frame controller models through XR_FB_render_model (controller_models,
//    render_model.c).
//
// Settings (key=value lines), later sources override earlier ones:
//   <libdir>/libframe_settings.so
//   /sdcard/Android/data/<package>/files/framebridge.conf
//   $FRAMEBRIDGE_CONFIG
#define _GNU_SOURCE
#define XR_EXTENSION_PROTOTYPES
#include <openxr/openxr.h>
#include <android/log.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TAG "FrameBridge"
#define LOG(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

static void *loader;
static PFN_xrGetInstanceProcAddr next_gipa;
static pthread_once_t init_once = PTHREAD_ONCE_INIT;
static XrInstance active_instance = XR_NULL_HANDLE;

static float scale = 1.0f;
static int foveation_fix = 1;
static int controller_fix = 1;
static int swapchain_fix = 1;
static int layer_fix = 1;
static int mutable_fix = 0;
static int swap_eyes = 0;
static int passthrough_emul = 1;
static int respace_kick = 0;
static int strip_depth = 0;
static int flip_quads = 0;
static int flip_emul_setting = 1;
static int scene_emul;  // scene emulation setting (per game)
static float scene_height = 2.5f;  // scene emulation ceiling height (m)
static float scene_width, scene_depth;  // optional room size override (m)
static int64_t xr_time_offset;  // XrTime minus CLOCK_MONOTONIC ns, measured at xrWaitFrame
static int xr_time_calibrated;
static int runtime_has_image_layout;  // runtime supports XR_FB_composition_layer_image_layout natively
// Layer types whose extension the runtime lacks (dropped at xrCreateInstance) are removed from frames.
static int no_equirect, no_equirect2, no_cylinder, no_cube;
static int runtime_has_fb_passthrough, emulate_passthrough, alpha_blend_failed;
static int controller_models;  // serve Frame controller models via XR_FB_render_model (render_model.c)
static void render_model_init(const char *package);

static void read_settings(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[256];
    float value;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "scale=%f", &value) == 1 && value >= 0.5f && value <= 2.0f) scale = value;
        if (sscanf(line, "foveation_fix=%f", &value) == 1) foveation_fix = value != 0;
        if (sscanf(line, "controller_fix=%f", &value) == 1) controller_fix = value != 0;
        if (sscanf(line, "swapchain_fix=%f", &value) == 1) swapchain_fix = value != 0;
        if (sscanf(line, "layer_fix=%f", &value) == 1) layer_fix = value != 0;
        if (sscanf(line, "mutable_fix=%f", &value) == 1) mutable_fix = value != 0;
        if (sscanf(line, "swap_eyes=%f", &value) == 1) swap_eyes = value != 0;
        if (sscanf(line, "passthrough_emul=%f", &value) == 1) passthrough_emul = value != 0;
        if (sscanf(line, "respace_kick=%f", &value) == 1) respace_kick = value != 0;
        if (sscanf(line, "strip_depth=%f", &value) == 1) strip_depth = value != 0;
        if (sscanf(line, "flip_quads=%f", &value) == 1) flip_quads = value != 0;
        if (sscanf(line, "flip_emul=%f", &value) == 1) flip_emul_setting = value != 0;
        if (sscanf(line, "scene_emul=%f", &value) == 1) scene_emul = value != 0;
        if (sscanf(line, "scene_height=%f", &value) == 1 && value > 1.5f && value < 5.0f) scene_height = value;
        if (sscanf(line, "scene_width=%f", &value) == 1 && value > 0.5f && value < 20.0f) scene_width = value;
        if (sscanf(line, "scene_depth=%f", &value) == 1 && value > 0.5f && value < 20.0f) scene_depth = value;
        if (sscanf(line, "controller_models=%f", &value) == 1) controller_models = value != 0;
    }
    fclose(f);
    LOG("settings read from %s", path);
}

static void initialize(void) {
    Dl_info self;
    char path[4096];
    if (!dladdr((void *)&initialize, &self) || !self.dli_fname) return;
    snprintf(path, sizeof(path), "%s", self.dli_fname);
    char *slash = strrchr(path, '/');
    if (!slash) return;
    size_t room = sizeof(path) - (size_t)(slash + 1 - path);

    snprintf(slash + 1, room, "libframe_settings.so");
    read_settings(path);

    snprintf(slash + 1, room, "libopenxr_loader_original.so");
    loader = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (loader) next_gipa = (PFN_xrGetInstanceProcAddr)dlsym(loader, "xrGetInstanceProcAddr");

    char process[256] = {0};
    FILE *f = fopen("/proc/self/cmdline", "r");
    if (f) {
        size_t n = fread(process, 1, sizeof(process) - 1, f);
        process[n] = 0;
        fclose(f);
    }
    char *colon = strchr(process, ':');
    if (colon) *colon = 0;
    if (*process && !strchr(process, '/')) {
        snprintf(path, sizeof(path), "/sdcard/Android/data/%s/files/framebridge.conf", process);
        read_settings(path);
    }
    const char *env = getenv("FRAMEBRIDGE_CONFIG");
    if (env && *env) read_settings(env);
    if (controller_models && *process && !strchr(process, '/')) render_model_init(process);

    LOG("scale=%.2f foveation_fix=%d controller_fix=%d swapchain_fix=%d layer_fix=%d mutable_fix=%d swap_eyes=%d passthrough_emul=%d respace_kick=%d flip_quads=%d controller_models=%d loader=%s",
        scale, foveation_fix, controller_fix, swapchain_fix, layer_fix, mutable_fix, swap_eyes, passthrough_emul,
        respace_kick, flip_quads, controller_models, next_gipa ? "OK" : (dlerror() ?: "FAILED"));
}

static PFN_xrVoidFunction lookup(XrInstance instance, const char *name) {
    pthread_once(&init_once, initialize);
    PFN_xrVoidFunction fn = NULL;
    if (next_gipa) next_gipa(instance, name, &fn);
    if (!fn && loader) fn = (PFN_xrVoidFunction)dlsym(loader, name);
    return fn;
}

#include "scene_emu.c"
#include "render_model.c"

XRAPI_ATTR XrResult XRAPI_CALL xrCreateInstance(const XrInstanceCreateInfo *info, XrInstance *instance) {
    PFN_xrCreateInstance fn = (PFN_xrCreateInstance)lookup(XR_NULL_HANDLE, "xrCreateInstance");
    if (!fn) return XR_ERROR_INITIALIZATION_FAILED;
    if (!info) return XR_ERROR_VALIDATION_FAILURE;

    int chained = 0, enabled = 0;
    for (const XrBaseInStructure *p = (const XrBaseInStructure *)info->next; p; p = p->next)
        if (p->type == XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR) chained = 1;
    for (uint32_t i = 0; i < info->enabledExtensionCount; ++i)
        if (!strcmp(info->enabledExtensionNames[i], "XR_KHR_android_create_instance")) enabled = 1;

    // Runtime's real extension list (bypassing our own filtered hook), used to drop requested
    // extensions the Frame runtime lacks (e.g. XR_FB_passthrough) instead of failing outright.
    PFN_xrEnumerateInstanceExtensionProperties enum_ext = (PFN_xrEnumerateInstanceExtensionProperties)lookup(
        XR_NULL_HANDLE, "xrEnumerateInstanceExtensionProperties");
    uint32_t available_count = 0;
    XrExtensionProperties *available = NULL;
    if (enum_ext && XR_SUCCEEDED(enum_ext(NULL, 0, &available_count, NULL)) && available_count) {
        available = calloc(available_count, sizeof(*available));
        if (available) {
            for (uint32_t i = 0; i < available_count; ++i) available[i].type = XR_TYPE_EXTENSION_PROPERTIES;
            if (XR_FAILED(enum_ext(NULL, available_count, &available_count, available))) {
                free(available);
                available = NULL;
            }
        }
    }

    XrInstanceCreateInfo fixed = *info;
    const char **names = calloc(info->enabledExtensionCount + 1, sizeof(*names));
    if (!names) { free(available); return XR_ERROR_OUT_OF_MEMORY; }
    uint32_t kept = 0;
    for (uint32_t i = 0; i < info->enabledExtensionCount; ++i) {
        const char *ext = info->enabledExtensionNames[i];
        int supported = !available;  // if we could not enumerate, pass everything through
        for (uint32_t j = 0; available && j < available_count && !supported; ++j)
            supported = !strcmp(available[j].extensionName, ext);
        if (supported) names[kept++] = ext;
        else if (scene_emul && is_scene_extension(ext)) {
            if (!emulate_scene) LOG("emulating Meta scene/spatial-entity extensions from the guardian bounds");
            emulate_scene = 1;
        } else if (controller_models && render_models_available && !strcmp(ext, XR_FB_RENDER_MODEL_EXTENSION_NAME)) {
            emulate_render_model = 1;
            LOG("emulating XR_FB_render_model with Steam Frame controller models");
        } else if (passthrough_emul && !strcmp(ext, "XR_FB_passthrough")) {
            emulate_passthrough = 1;
            LOG("emulating XR_FB_passthrough with alpha-blended environment");
        } else LOG("dropped unsupported extension %s", ext);
    }
    if (chained && !enabled) {
        names[kept++] = "XR_KHR_android_create_instance";
        LOG("added %s", "XR_KHR_android_create_instance");
    }
    fixed.enabledExtensionNames = names;
    fixed.enabledExtensionCount = kept;
    for (uint32_t j = 0; available && j < available_count; ++j)
        if (!strcmp(available[j].extensionName, "XR_FB_composition_layer_image_layout")) runtime_has_image_layout = 1;
    // Layer types are only valid if their extension ends up enabled; some apps submit them regardless.
    int has_equirect = 0, has_equirect2 = 0, has_cylinder = 0, has_cube = 0;
    for (uint32_t i = 0; i < kept; ++i) {
        has_equirect |= !strcmp(names[i], "XR_KHR_composition_layer_equirect");
        has_equirect2 |= !strcmp(names[i], "XR_KHR_composition_layer_equirect2");
        has_cylinder |= !strcmp(names[i], "XR_KHR_composition_layer_cylinder");
        has_cube |= !strcmp(names[i], "XR_KHR_composition_layer_cube");
    }
    no_equirect = !has_equirect; no_equirect2 = !has_equirect2; no_cylinder = !has_cylinder; no_cube = !has_cube;
    free(available);
    XrResult result = fn(&fixed, instance);
    free(names);
    LOG("xrCreateInstance result=%d", result);
    if (XR_SUCCEEDED(result)) active_instance = *instance;
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrEnumerateViewConfigurationViews(XrInstance instance, XrSystemId system,
        XrViewConfigurationType type, uint32_t capacity, uint32_t *count, XrViewConfigurationView *views) {
    PFN_xrEnumerateViewConfigurationViews fn =
        (PFN_xrEnumerateViewConfigurationViews)lookup(instance, "xrEnumerateViewConfigurationViews");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(instance, system, type, capacity, count, views);
    if (XR_SUCCEEDED(result) && views && count && scale != 1.0f) {
        for (uint32_t i = 0; i < *count && i < capacity; ++i) {
            uint32_t w = (uint32_t)(views[i].recommendedImageRectWidth * scale + 0.5f);
            uint32_t h = (uint32_t)(views[i].recommendedImageRectHeight * scale + 0.5f);
            if (w > views[i].maxImageRectWidth) w = views[i].maxImageRectWidth;
            if (h > views[i].maxImageRectHeight) h = views[i].maxImageRectHeight;
            views[i].recommendedImageRectWidth = w ? w : 1;
            views[i].recommendedImageRectHeight = h ? h : 1;
            LOG("view %u recommended %ux%u", i, views[i].recommendedImageRectWidth, views[i].recommendedImageRectHeight);
        }
    }
    return result;
}

// Closest runtime-supported swapchain format for one it rejected; 0 if none.
static int64_t pick_format(XrSession session, int64_t wanted) {
    PFN_xrEnumerateSwapchainFormats fn =
        (PFN_xrEnumerateSwapchainFormats)lookup(active_instance, "xrEnumerateSwapchainFormats");
    uint32_t count = 0;
    if (!fn || XR_FAILED(fn(session, 0, &count, NULL)) || !count) return 0;
    int64_t *formats = calloc(count, sizeof(*formats));
    if (!formats || XR_FAILED(fn(session, count, &count, formats))) { free(formats); return 0; }
    static int logged;
    if (!logged++)
        for (uint32_t i = 0; i < count; ++i) LOG("runtime swapchain format[%u]=%lld", i, (long long)formats[i]);

    // Same channel layout, preferring the sRGB variant the runtime accepts.
    static const int64_t equivalents[][2] = {
        {0x8058, 0x8C43},  // GL_RGBA8 -> GL_SRGB8_ALPHA8
        {0x8051, 0x8C43},  // GL_RGB8 -> GL_SRGB8_ALPHA8
        {0x8C41, 0x8C43},  // GL_SRGB8 -> GL_SRGB8_ALPHA8
        {37, 43},          // VK_FORMAT_R8G8B8A8_UNORM -> _SRGB
        {44, 50},          // VK_FORMAT_B8G8R8A8_UNORM -> _SRGB
        {50, 43},          // VK_FORMAT_B8G8R8A8_SRGB -> R8G8B8A8_SRGB
    };
    int64_t chosen = 0;
    for (size_t e = 0; e < sizeof(equivalents) / sizeof(equivalents[0]) && !chosen; ++e)
        if (equivalents[e][0] == wanted)
            for (uint32_t i = 0; i < count && !chosen; ++i)
                if (formats[i] == equivalents[e][1]) chosen = formats[i];
    if (!chosen) chosen = formats[0];  // runtime lists its preferred format first
    free(formats);
    return chosen;
}

// Swapchains the runtime actually created; layers referencing anything else are dropped.
static pthread_mutex_t swapchains_lock = PTHREAD_MUTEX_INITIALIZER;
static XrSwapchain *swapchains;
static size_t swapchain_count, swapchain_capacity;

static void remember_swapchain(XrSwapchain handle) {
    pthread_mutex_lock(&swapchains_lock);
    if (swapchain_count == swapchain_capacity) {
        size_t capacity = swapchain_capacity ? swapchain_capacity * 2 : 64;
        XrSwapchain *grown = realloc(swapchains, capacity * sizeof(*grown));
        if (grown) { swapchains = grown; swapchain_capacity = capacity; }
    }
    if (swapchain_count < swapchain_capacity) swapchains[swapchain_count++] = handle;
    pthread_mutex_unlock(&swapchains_lock);
}

static void forget_swapchain(XrSwapchain handle) {
    pthread_mutex_lock(&swapchains_lock);
    for (size_t i = 0; i < swapchain_count; ++i)
        if (swapchains[i] == handle) { swapchains[i] = swapchains[--swapchain_count]; break; }
    pthread_mutex_unlock(&swapchains_lock);
}

static void flip_on_create_swapchain(XrSwapchain handle, const XrSwapchainCreateInfo *info);
static void flip_on_destroy(XrSwapchain handle);

static int known_swapchain(XrSwapchain handle) {
    int found = 0;
    pthread_mutex_lock(&swapchains_lock);
    for (size_t i = 0; i < swapchain_count && !found; ++i) found = swapchains[i] == handle;
    pthread_mutex_unlock(&swapchains_lock);
    return found;
}

XRAPI_ATTR XrResult XRAPI_CALL xrCreateSwapchain(XrSession session, const XrSwapchainCreateInfo *info,
        XrSwapchain *swapchain) {
    PFN_xrCreateSwapchain fn = (PFN_xrCreateSwapchain)lookup(active_instance, "xrCreateSwapchain");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    if (!info) return XR_ERROR_VALIDATION_FAILURE;
    XrSwapchainCreateInfo fixed = *info;
    if (foveation_fix) {
        // Drop leading foveation structs; the chain is const so only a prefix can be skipped.
        const XrBaseInStructure *p = (const XrBaseInStructure *)fixed.next;
        while (p && p->type == XR_TYPE_SWAPCHAIN_CREATE_INFO_FOVEATION_FB) p = p->next;
        fixed.next = p;
    }
    if (mutable_fix) fixed.usageFlags |= XR_SWAPCHAIN_USAGE_MUTABLE_FORMAT_BIT;
    XrResult result = fn(session, &fixed, swapchain);
    LOG("xrCreateSwapchain %ux%u format=%lld samples=%u array=%u faces=%u usage=0x%llx result=%d", fixed.width,
        fixed.height, (long long)fixed.format, fixed.sampleCount, fixed.arraySize, fixed.faceCount,
        (unsigned long long)fixed.usageFlags, result);
    if (XR_SUCCEEDED(result)) { remember_swapchain(*swapchain); flip_on_create_swapchain(*swapchain, &fixed); return result; }
    if (!swapchain_fix) return result;

    // Frame's runtime rejects some GLES formats (GL_RGBA8) and MSAA swapchains (overport #71).
    if (fixed.sampleCount > 1) {
        fixed.sampleCount = 1;
        result = fn(session, &fixed, swapchain);
        LOG("  retry samples=1 result=%d", result);
    }
    if (result == XR_ERROR_SWAPCHAIN_FORMAT_UNSUPPORTED) {
        int64_t replacement = pick_format(session, fixed.format);
        if (replacement && replacement != fixed.format) {
            fixed.format = replacement;
            result = fn(session, &fixed, swapchain);
            LOG("  retry format=%lld result=%d", (long long)replacement, result);
        }
    }
    if (XR_SUCCEEDED(result)) { remember_swapchain(*swapchain); flip_on_create_swapchain(*swapchain, &fixed); }
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrDestroySwapchain(XrSwapchain swapchain) {
    PFN_xrDestroySwapchain fn = (PFN_xrDestroySwapchain)lookup(active_instance, "xrDestroySwapchain");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    forget_swapchain(swapchain);
    flip_on_destroy(swapchain);
    return fn(swapchain);
}

// A layer is usable if every swapchain it references was created successfully.
static int layer_usable(const XrCompositionLayerBaseHeader *layer) {
    switch (layer->type) {
    case XR_TYPE_COMPOSITION_LAYER_PROJECTION: {
        const XrCompositionLayerProjection *p = (const XrCompositionLayerProjection *)layer;
        for (uint32_t v = 0; v < p->viewCount; ++v)
            if (!known_swapchain(p->views[v].subImage.swapchain)) return 0;
        return 1;
    }
    case XR_TYPE_COMPOSITION_LAYER_QUAD:
        return known_swapchain(((const XrCompositionLayerQuad *)layer)->subImage.swapchain);
    case XR_TYPE_COMPOSITION_LAYER_CYLINDER_KHR:
        if (no_cylinder) return 0;
        return known_swapchain(((const XrCompositionLayerCylinderKHR *)layer)->subImage.swapchain);
    case XR_TYPE_COMPOSITION_LAYER_EQUIRECT_KHR:
        if (no_equirect) return 0;
        return known_swapchain(((const XrCompositionLayerEquirectKHR *)layer)->subImage.swapchain);
    case XR_TYPE_COMPOSITION_LAYER_EQUIRECT2_KHR:
        if (no_equirect2) return 0;
        return known_swapchain(((const XrCompositionLayerEquirect2KHR *)layer)->subImage.swapchain);
    case XR_TYPE_COMPOSITION_LAYER_CUBE_KHR:
        if (no_cube) return 0;
        return known_swapchain(((const XrCompositionLayerCubeKHR *)layer)->swapchain);
    default:
        return 1;  // layer types without swapchains (e.g. passthrough) or unknown: pass through
    }
}

// ---------------------------------------------------------------- XR_FB_passthrough emulation
static uint64_t next_fake_handle = 0x7f000001;
#define FAKE_HANDLE(type) ((type)(uintptr_t)__atomic_fetch_add(&next_fake_handle, 1, __ATOMIC_RELAXED))

static XRAPI_ATTR XrResult XRAPI_CALL emu_create_passthrough(XrSession s, const XrPassthroughCreateInfoFB *i, XrPassthroughFB *out) {
    (void)s; (void)i; if (!out) return XR_ERROR_VALIDATION_FAILURE; *out = FAKE_HANDLE(XrPassthroughFB); return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_passthrough_handle(XrPassthroughFB p) { (void)p; return XR_SUCCESS; }
static XRAPI_ATTR XrResult XRAPI_CALL emu_create_passthrough_layer(XrSession s, const XrPassthroughLayerCreateInfoFB *i,
        XrPassthroughLayerFB *out) {
    (void)s; (void)i; if (!out) return XR_ERROR_VALIDATION_FAILURE; *out = FAKE_HANDLE(XrPassthroughLayerFB); return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_passthrough_layer_handle(XrPassthroughLayerFB l) { (void)l; return XR_SUCCESS; }
static XRAPI_ATTR XrResult XRAPI_CALL emu_passthrough_layer_style(XrPassthroughLayerFB l, const XrPassthroughStyleFB *st) {
    (void)l; (void)st; return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_create_geometry(XrSession s, const XrGeometryInstanceCreateInfoFB *i, XrGeometryInstanceFB *out) {
    (void)s; (void)i; if (!out) return XR_ERROR_VALIDATION_FAILURE; *out = FAKE_HANDLE(XrGeometryInstanceFB); return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_geometry_handle(XrGeometryInstanceFB g) { (void)g; return XR_SUCCESS; }
static XRAPI_ATTR XrResult XRAPI_CALL emu_geometry_transform(XrGeometryInstanceFB g, const XrGeometryInstanceTransformFB *t) {
    (void)g; (void)t; return XR_SUCCESS;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_create_mesh(XrSession s, const XrTriangleMeshCreateInfoFB *i, XrTriangleMeshFB *out) {
    (void)s; (void)i; if (!out) return XR_ERROR_VALIDATION_FAILURE; *out = FAKE_HANDLE(XrTriangleMeshFB); return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_mesh_handle(XrTriangleMeshFB m) { (void)m; return XR_SUCCESS; }
static XRAPI_ATTR XrResult XRAPI_CALL emu_create_lut(XrPassthroughFB p, const XrPassthroughColorLutCreateInfoMETA *i,
        XrPassthroughColorLutMETA *out) {
    (void)p; (void)i; if (!out) return XR_ERROR_VALIDATION_FAILURE; *out = FAKE_HANDLE(XrPassthroughColorLutMETA); return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_lut_handle(XrPassthroughColorLutMETA l) { (void)l; return XR_SUCCESS; }
static XRAPI_ATTR XrResult XRAPI_CALL emu_update_lut(XrPassthroughColorLutMETA l, const XrPassthroughColorLutUpdateInfoMETA *u) {
    (void)l; (void)u; return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_preferences(XrSession s, XrPassthroughPreferencesMETA *prefs) {
    (void)s; if (!prefs) return XR_ERROR_VALIDATION_FAILURE; prefs->flags = 0; return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_keyboard_hands(XrPassthroughLayerFB l, const XrPassthroughKeyboardHandsIntensityFB *i) {
    (void)l; (void)i; return XR_SUCCESS;
}

static PFN_xrVoidFunction passthrough_emulation(const char *name) {
    if (!emulate_passthrough) return NULL;
    static const struct { const char *name; PFN_xrVoidFunction fn; } table[] = {
        {"xrCreatePassthroughFB", (PFN_xrVoidFunction)emu_create_passthrough},
        {"xrDestroyPassthroughFB", (PFN_xrVoidFunction)emu_passthrough_handle},
        {"xrPassthroughStartFB", (PFN_xrVoidFunction)emu_passthrough_handle},
        {"xrPassthroughPauseFB", (PFN_xrVoidFunction)emu_passthrough_handle},
        {"xrCreatePassthroughLayerFB", (PFN_xrVoidFunction)emu_create_passthrough_layer},
        {"xrDestroyPassthroughLayerFB", (PFN_xrVoidFunction)emu_passthrough_layer_handle},
        {"xrPassthroughLayerPauseFB", (PFN_xrVoidFunction)emu_passthrough_layer_handle},
        {"xrPassthroughLayerResumeFB", (PFN_xrVoidFunction)emu_passthrough_layer_handle},
        {"xrPassthroughLayerSetStyleFB", (PFN_xrVoidFunction)emu_passthrough_layer_style},
        {"xrCreateGeometryInstanceFB", (PFN_xrVoidFunction)emu_create_geometry},
        {"xrDestroyGeometryInstanceFB", (PFN_xrVoidFunction)emu_geometry_handle},
        {"xrGeometryInstanceSetTransformFB", (PFN_xrVoidFunction)emu_geometry_transform},
        // Companion passthrough entry points OVRPlugin's Insight MR init requires.
        {"xrCreateTriangleMeshFB", (PFN_xrVoidFunction)emu_create_mesh},
        {"xrDestroyTriangleMeshFB", (PFN_xrVoidFunction)emu_mesh_handle},
        {"xrCreatePassthroughColorLutMETA", (PFN_xrVoidFunction)emu_create_lut},
        {"xrDestroyPassthroughColorLutMETA", (PFN_xrVoidFunction)emu_lut_handle},
        {"xrUpdatePassthroughColorLutMETA", (PFN_xrVoidFunction)emu_update_lut},
        {"xrGetPassthroughPreferencesMETA", (PFN_xrVoidFunction)emu_preferences},
        {"xrPassthroughLayerSetKeyboardHandsIntensityFB", (PFN_xrVoidFunction)emu_keyboard_hands},
    };
    for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); ++i)
        if (!strcmp(name, table[i].name)) return table[i].fn;
    return NULL;
}

XRAPI_ATTR XrResult XRAPI_CALL xrGetSystemProperties(XrInstance instance, XrSystemId system,
        XrSystemProperties *properties) {
    PFN_xrGetSystemProperties fn = (PFN_xrGetSystemProperties)lookup(instance, "xrGetSystemProperties");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(instance, system, properties);
    if (XR_SUCCEEDED(result) && (emulate_passthrough || emulate_scene || emulate_render_model) && properties)
        for (XrBaseOutStructure *p = (XrBaseOutStructure *)properties->next; p; p = p->next) {
            if (p->type == XR_TYPE_SYSTEM_PASSTHROUGH_PROPERTIES_FB)
                ((XrSystemPassthroughPropertiesFB *)p)->supportsPassthrough = XR_TRUE;
            if (p->type == XR_TYPE_SYSTEM_SPATIAL_ENTITY_PROPERTIES_FB && emulate_scene)
                ((XrSystemSpatialEntityPropertiesFB *)p)->supportsSpatialEntity = XR_TRUE;
            if (p->type == XR_TYPE_SYSTEM_PASSTHROUGH_PROPERTIES2_FB)
                ((XrSystemPassthroughProperties2FB *)p)->capabilities = XR_PASSTHROUGH_CAPABILITY_BIT_FB;
            if (p->type == XR_TYPE_SYSTEM_RENDER_MODEL_PROPERTIES_FB && emulate_render_model)
                ((XrSystemRenderModelPropertiesFB *)p)->supportsRenderModelLoading = XR_TRUE;
        }
    return result;
}

#include "flip_vk.c"

// XR_KHR_convert_timespec_time for runtimes that lack it (Frame): XrTime is derived from the monotonic clock
// using the offset measured at the last xrWaitFrame. The runtime's own implementation is preferred.
static PFN_xrConvertTimespecTimeToTimeKHR runtime_timespec_to_time;
static PFN_xrConvertTimeToTimespecTimeKHR runtime_time_to_timespec;

static XRAPI_ATTR XrResult XRAPI_CALL emu_timespec_to_time(XrInstance instance, const struct timespec *ts, XrTime *time) {
    if (runtime_timespec_to_time) {
        XrResult r = runtime_timespec_to_time(instance, ts, time);
        if (r != XR_ERROR_FUNCTION_UNSUPPORTED) return r;
    }
    if (!ts || !time) return XR_ERROR_VALIDATION_FAILURE;
    *time = (XrTime)((int64_t)ts->tv_sec * 1000000000ll + ts->tv_nsec + (xr_time_calibrated ? xr_time_offset : 0));
    return XR_SUCCESS;
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_time_to_timespec(XrInstance instance, XrTime time, struct timespec *ts) {
    if (runtime_time_to_timespec) {
        XrResult r = runtime_time_to_timespec(instance, time, ts);
        if (r != XR_ERROR_FUNCTION_UNSUPPORTED) return r;
    }
    if (!ts) return XR_ERROR_VALIDATION_FAILURE;
    int64_t mono = (int64_t)time - (xr_time_calibrated ? xr_time_offset : 0);
    ts->tv_sec = (time_t)(mono / 1000000000ll);
    ts->tv_nsec = (long)(mono % 1000000000ll);
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL xrLocateSpace(XrSpace space, XrSpace baseSpace, XrTime time, XrSpaceLocation *location) {
    if (emulate_scene && location) {
        XrPosef pose; XrSpaceLocationFlags flags;
        if (locate_fake(space, baseSpace, time, &pose, &flags)) {
            location->pose = pose;
            location->locationFlags = flags;
            return XR_SUCCESS;
        }
    }
    PFN_xrLocateSpace fn = (PFN_xrLocateSpace)lookup(active_instance, "xrLocateSpace");
    return fn ? fn(space, baseSpace, time, location) : XR_ERROR_FUNCTION_UNSUPPORTED;
}

static XrResult locate_spaces_common(const char *name, XrSession session, const XrSpacesLocateInfo *info,
                                     XrSpaceLocations *locations) {
    int any_fake = 0;
    if (emulate_scene && info && locations) {
        any_fake = fake_space_index(info->baseSpace) >= 0;
        for (uint32_t i = 0; i < info->spaceCount && !any_fake; ++i) any_fake = fake_space_index(info->spaces[i]) >= 0;
    }
    if (!any_fake) {
        PFN_xrLocateSpaces fn = (PFN_xrLocateSpaces)lookup(active_instance, name);
        return fn ? fn(session, info, locations) : XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    for (uint32_t i = 0; i < info->spaceCount && i < locations->locationCount; ++i) {
        XrSpaceLocation one = {XR_TYPE_SPACE_LOCATION, NULL, 0, {{0, 0, 0, 1}, {0, 0, 0}}};
        xrLocateSpace(info->spaces[i], info->baseSpace, info->time, &one);
        locations->locations[i].locationFlags = one.locationFlags;
        locations->locations[i].pose = one.pose;
    }
    return XR_SUCCESS;
}
XRAPI_ATTR XrResult XRAPI_CALL xrLocateSpaces(XrSession session, const XrSpacesLocateInfo *info, XrSpaceLocations *locations) {
    return locate_spaces_common("xrLocateSpaces", session, info, locations);
}
static XRAPI_ATTR XrResult XRAPI_CALL emu_locate_spaces_khr(XrSession session, const XrSpacesLocateInfo *info,
                                                            XrSpaceLocations *locations) {
    return locate_spaces_common("xrLocateSpacesKHR", session, info, locations);
}

XRAPI_ATTR XrResult XRAPI_CALL xrDestroySpace(XrSpace space) {
    if (fake_space_index(space) >= 0) return XR_SUCCESS;  // entity spaces live for the whole session
    PFN_xrDestroySpace fn = (PFN_xrDestroySpace)lookup(active_instance, "xrDestroySpace");
    return fn ? fn(space) : XR_ERROR_FUNCTION_UNSUPPORTED;
}

XRAPI_ATTR XrResult XRAPI_CALL xrCreateSession(XrInstance instance, const XrSessionCreateInfo *info, XrSession *session) {
    PFN_xrCreateSession fn = (PFN_xrCreateSession)lookup(instance, "xrCreateSession");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(instance, info, session);
    if (XR_SUCCEEDED(result)) { flip_emul = flip_emul_setting; flip_on_create_session(info); scene_on_create_session(*session); }
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrEnumerateSwapchainImages(XrSwapchain swapchain, uint32_t capacity, uint32_t *count,
        XrSwapchainImageBaseHeader *images) {
    PFN_xrEnumerateSwapchainImages fn = (PFN_xrEnumerateSwapchainImages)lookup(active_instance, "xrEnumerateSwapchainImages");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(swapchain, capacity, count, images);
    if (XR_SUCCEEDED(result) && images && capacity && count) flip_on_enumerate_images(swapchain, *count, images);
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrAcquireSwapchainImage(XrSwapchain swapchain, const XrSwapchainImageAcquireInfo *info,
        uint32_t *index) {
    PFN_xrAcquireSwapchainImage fn = (PFN_xrAcquireSwapchainImage)lookup(active_instance, "xrAcquireSwapchainImage");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(swapchain, info, index);
    if (XR_SUCCEEDED(result) && index) flip_on_acquire(swapchain, *index);
    return result;
}

// ---------------------------------------------------------------- tracking diagnostics
static XrSpace view_space_handle = XR_NULL_HANDLE;

// Frame's runtime returns untracked poses from reference spaces created before tracking starts.
// With respace_kick, once the session reaches FOCUSED we deliver synthetic "reference space change
// pending" events so the app (OVRPlugin) recreates its spaces.
static XrSession focused_session = XR_NULL_HANDLE;
static int kicks_pending = -1;  // -1: not yet armed

XRAPI_ATTR XrResult XRAPI_CALL xrPollEvent(XrInstance instance, XrEventDataBuffer *event) {
    PFN_xrPollEvent fn = (PFN_xrPollEvent)lookup(instance, "xrPollEvent");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    if (emulate_scene && event) {
        pthread_mutex_lock(&scene_lock);
        int got = pop_event(event);
        pthread_mutex_unlock(&scene_lock);
        if (got) return XR_SUCCESS;
    }
    XrResult result = fn(instance, event);
    if (!respace_kick || !event) return result;
    if (result == XR_SUCCESS && event->type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
        const XrEventDataSessionStateChanged *e = (const XrEventDataSessionStateChanged *)event;
        if (e->state == XR_SESSION_STATE_FOCUSED && kicks_pending < 0) {
            focused_session = e->session;
            kicks_pending = 3;  // LOCAL, LOCAL_FLOOR, STAGE
        }
        return result;
    }
    if (result == XR_EVENT_UNAVAILABLE && kicks_pending > 0) {
        static const XrReferenceSpaceType types[] = {XR_REFERENCE_SPACE_TYPE_STAGE,
            (XrReferenceSpaceType)1000426000 /* LOCAL_FLOOR_EXT */, XR_REFERENCE_SPACE_TYPE_LOCAL};
        XrEventDataReferenceSpaceChangePending *e = (XrEventDataReferenceSpaceChangePending *)event;
        memset(e, 0, sizeof(*e));
        e->type = XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING;
        e->session = focused_session;
        e->referenceSpaceType = types[--kicks_pending];
        e->changeTime = 0;
        e->poseValid = XR_FALSE;
        e->poseInPreviousSpace.orientation.w = 1.0f;
        LOG("respace_kick: delivered reference space change for type %d", e->referenceSpaceType);
        return XR_SUCCESS;
    }
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrCreateReferenceSpace(XrSession session, const XrReferenceSpaceCreateInfo *info,
        XrSpace *space) {
    PFN_xrCreateReferenceSpace fn = (PFN_xrCreateReferenceSpace)lookup(active_instance, "xrCreateReferenceSpace");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(session, info, space);
    if (info && space) {
        LOG("xrCreateReferenceSpace type=%d result=%d space=%p", info->referenceSpaceType, result,
            (void *)(uintptr_t)*space);
        if (XR_SUCCEEDED(result) && info->referenceSpaceType == XR_REFERENCE_SPACE_TYPE_VIEW) view_space_handle = *space;
    }
    return result;
}

static XrTime last_predicted_time;

XRAPI_ATTR XrResult XRAPI_CALL xrWaitFrame(XrSession session, const XrFrameWaitInfo *info, XrFrameState *state) {
    PFN_xrWaitFrame fn = (PFN_xrWaitFrame)lookup(active_instance, "xrWaitFrame");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(session, info, state);
    if (XR_SUCCEEDED(result) && state) {
        last_predicted_time = state->predictedDisplayTime;
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t mono = (int64_t)now.tv_sec * 1000000000ll + now.tv_nsec;
        // predictedDisplayTime is about one display period ahead of "now".
        xr_time_offset = (int64_t)(state->predictedDisplayTime - state->predictedDisplayPeriod) - mono;
        xr_time_calibrated = 1;
    }
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrLocateViews(XrSession session, const XrViewLocateInfo *info, XrViewState *state,
        uint32_t capacity, uint32_t *count, XrView *views) {
    PFN_xrLocateViews fn = (PFN_xrLocateViews)lookup(active_instance, "xrLocateViews");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(session, info, state, capacity, count, views);
    // respace_kick trigger: ~90 consecutive untracked head poses in a non-VIEW space.
    static int untracked;
    if (respace_kick && XR_SUCCEEDED(result) && state && info && info->space != view_space_handle) {
        int tracked = (state->viewStateFlags & (XR_VIEW_STATE_ORIENTATION_TRACKED_BIT | XR_VIEW_STATE_POSITION_TRACKED_BIT)) != 0;
        untracked = tracked ? 0 : untracked + 1;
        if (untracked == 90 && kicks_pending <= 0) {
            focused_session = session;
            kicks_pending = 3;
            LOG("respace_kick: head pose untracked for 90 frames, asking app to recreate its spaces");
            // Diagnostic: which freshly created space types give tracked poses right now?
            PFN_xrCreateReferenceSpace create = (PFN_xrCreateReferenceSpace)lookup(active_instance, "xrCreateReferenceSpace");
            static const XrReferenceSpaceType probe[] = {XR_REFERENCE_SPACE_TYPE_LOCAL, XR_REFERENCE_SPACE_TYPE_STAGE,
                                                         (XrReferenceSpaceType)1000426000};
            for (size_t k = 0; create && k < 3; ++k) {
                XrReferenceSpaceCreateInfo ci = {XR_TYPE_REFERENCE_SPACE_CREATE_INFO, NULL, probe[k], {{0, 0, 0, 1}, {0, 0, 0}}};
                XrSpace fresh = XR_NULL_HANDLE;
                if (XR_FAILED(create(session, &ci, &fresh))) continue;
                XrViewLocateInfo li = *info;
                li.space = fresh;
                XrViewState st = {XR_TYPE_VIEW_STATE, NULL, 0};
                XrView tmp[2] = {{XR_TYPE_VIEW, NULL, {{0, 0, 0, 1}, {0, 0, 0}}, {0, 0, 0, 0}},
                                 {XR_TYPE_VIEW, NULL, {{0, 0, 0, 1}, {0, 0, 0}}, {0, 0, 0, 0}}};
                uint32_t n = 0;
                XrResult r = fn(session, &li, &st, 2, &n, tmp);
                LOG("probe fresh space type=%d locate=%d flags=0x%llx pos=%.3f,%.3f,%.3f", probe[k], r,
                    (unsigned long long)st.viewStateFlags, tmp[0].pose.position.x, tmp[0].pose.position.y,
                    tmp[0].pose.position.z);
            }
        }
    }
    static int logged;
    if (views && count && *count && logged < 400 && (logged++ % 100) == 0)
        LOG("xrLocateViews space=%p%s result=%d flags=0x%llx view0 pos=%.3f,%.3f,%.3f time=%lld predicted=%lld",
            (void *)(uintptr_t)info->space, info->space == view_space_handle ? "(VIEW)" : "", result,
            (unsigned long long)state->viewStateFlags, views[0].pose.position.x, views[0].pose.position.y,
            views[0].pose.position.z, (long long)info->displayTime, (long long)last_predicted_time);
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrEndFrame(XrSession session, const XrFrameEndInfo *info) {
    PFN_xrEndFrame fn = (PFN_xrEndFrame)lookup(active_instance, "xrEndFrame");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    {   // frame pacing statistics every ~5 s: fps and submitted-vs-predicted display time
        static struct timespec start;
        static int frames;
        static long long drift_sum, drift_max;
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        if (!frames) start = now;
        ++frames;
        long long drift = info ? (long long)(info->displayTime - last_predicted_time) : 0;
        drift_sum += drift < 0 ? -drift : drift;
        if ((drift < 0 ? -drift : drift) > drift_max) drift_max = drift < 0 ? -drift : drift;
        double elapsed = (now.tv_sec - start.tv_sec) + (now.tv_nsec - start.tv_nsec) * 1e-9;
        if (elapsed >= 5.0) {
            LOG("pacing: %.1f fps, displayTime vs predicted: avg %.2f ms, max %.2f ms", frames / elapsed,
                drift_sum / (double)frames / 1e6, drift_max / 1e6);
            frames = 0; drift_sum = drift_max = 0;
        }
    }
    if ((!layer_fix && !swap_eyes && !emulate_passthrough && !flip_quads && !flip_emul && !strip_depth) || !info || !info->layerCount ||
        info->layerCount > 64)
        return fn(session, info);
    const XrCompositionLayerBaseHeader *kept[64];
    XrCompositionLayerProjection projections[64];
    XrCompositionLayerProjectionView views[64][2];
    XrCompositionLayerQuad quads[64];
    uint32_t count = 0, dropped = 0, swapped = 0, passthrough = 0;
    for (uint32_t i = 0; i < info->layerCount; ++i) {
        const XrCompositionLayerBaseHeader *layer = info->layers[i];
        if (!layer) { ++dropped; continue; }
        if (emulate_passthrough && layer->type == XR_TYPE_COMPOSITION_LAYER_PASSTHROUGH_FB) {
            ++passthrough;  // replaced by the runtime's camera environment below
            continue;
        }
        {
            static struct { int type; const void *key; } seen[48];
            static int nseen;
            const void *key = NULL;
            if (layer->type == XR_TYPE_COMPOSITION_LAYER_QUAD) key = (const void *)(uintptr_t)((const XrCompositionLayerQuad *)layer)->subImage.swapchain;
            if (layer->type == XR_TYPE_COMPOSITION_LAYER_CYLINDER_KHR) key = (const void *)(uintptr_t)((const XrCompositionLayerCylinderKHR *)layer)->subImage.swapchain;
            if (layer->type == XR_TYPE_COMPOSITION_LAYER_EQUIRECT2_KHR) key = (const void *)(uintptr_t)((const XrCompositionLayerEquirect2KHR *)layer)->subImage.swapchain;
            int known = 0;
            for (int k = 0; k < nseen; ++k) known |= seen[k].type == (int)layer->type && seen[k].key == key;
            if (!known && nseen < 48) {
                seen[nseen].type = layer->type; seen[nseen++].key = key;
                int tagged = 0;
                for (const XrBaseInStructure *n = (const XrBaseInStructure *)layer->next; n; n = n->next)
                    tagged |= n->type == 1000040000 && (((const XrCompositionLayerImageLayoutFB *)n)->flags & 1);
                LOG("new layer: type=%d swapchain=%p flip_tag=%d usable=%d", layer->type, key, tagged, layer_usable(layer));
            }
        }
        if (!layer || (layer_fix && !layer_usable(layer))) { ++dropped; continue; }
        if (layer->type == XR_TYPE_COMPOSITION_LAYER_QUAD) {
            const XrCompositionLayerQuad *flipped = flip_quad(session, (const XrCompositionLayerQuad *)layer, &quads[count]);
            if (flipped) { layer = (const XrCompositionLayerBaseHeader *)flipped; ++swapped; }
        }
        if (flip_quads && layer->type == XR_TYPE_COMPOSITION_LAYER_QUAD) {
            // Rotate 180 degrees about the quad's local X axis: shows the image upright without mirroring it.
            quads[count] = *(const XrCompositionLayerQuad *)layer;
            XrQuaternionf q = quads[count].pose.orientation;           // q * (1,0,0,0)
            quads[count].pose.orientation = (XrQuaternionf){q.w, q.z, -q.y, -q.x};  // (x,y,z,w) of q * (1,0,0 | 0)
            layer = (const XrCompositionLayerBaseHeader *)&quads[count];
            ++swapped;
        }
        int blend_alpha = passthrough && !alpha_blend_failed && layer->type == XR_TYPE_COMPOSITION_LAYER_PROJECTION;
        if (blend_alpha && !(swap_eyes && ((const XrCompositionLayerProjection *)layer)->viewCount == 2)) {
            // Let the camera show through where the app rendered transparent pixels.
            projections[count] = *(const XrCompositionLayerProjection *)layer;
            projections[count].layerFlags |= XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT |
                                             XR_COMPOSITION_LAYER_UNPREMULTIPLIED_ALPHA_BIT;
            layer = (const XrCompositionLayerBaseHeader *)&projections[count];
        }
        if (strip_depth && layer->type == XR_TYPE_COMPOSITION_LAYER_PROJECTION &&
            ((const XrCompositionLayerProjection *)layer)->viewCount == 2) {
            // Drop XrCompositionLayerDepthInfoKHR: bad app depth makes positional reprojection warp the image.
            const XrCompositionLayerProjection *src = (const XrCompositionLayerProjection *)layer;
            if (layer != (const XrCompositionLayerBaseHeader *)&projections[count]) projections[count] = *src;
            views[count][0] = src->views[0];
            views[count][1] = src->views[1];
            views[count][0].next = NULL;
            views[count][1].next = NULL;
            projections[count].views = views[count];
            layer = (const XrCompositionLayerBaseHeader *)&projections[count];
            static int logged;
            if (!logged++) LOG("strip_depth: removed depth info from projection layer");
            ++swapped;
        }
        if (swap_eyes && layer->type == XR_TYPE_COMPOSITION_LAYER_PROJECTION &&
            ((const XrCompositionLayerProjection *)layer)->viewCount == 2) {
            // Show each eye the image the game rendered for it: keep poses/FOVs, swap sub-images.
            const XrCompositionLayerProjection *cur = (const XrCompositionLayerProjection *)layer;
            XrCompositionLayerProjectionView v0 = cur->views[0], v1 = cur->views[1];
            if (layer != (const XrCompositionLayerBaseHeader *)&projections[count]) projections[count] = *cur;
            views[count][0] = v0;
            views[count][1] = v1;
            views[count][0].subImage = v1.subImage;
            views[count][1].subImage = v0.subImage;
            projections[count].views = views[count];
            if (blend_alpha)
                projections[count].layerFlags |= XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT |
                                                 XR_COMPOSITION_LAYER_UNPREMULTIPLIED_ALPHA_BIT;
            layer = (const XrCompositionLayerBaseHeader *)&projections[count];
            ++swapped;
        }
        kept[count++] = layer;
    }
    XrFrameEndInfo fixed = *info;
    fixed.layers = kept;
    fixed.layerCount = count;
    if (passthrough && !alpha_blend_failed) fixed.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_ALPHA_BLEND;
    static int warned, failures, passthrough_logged;
    if (dropped && warned++ < 5) LOG("xrEndFrame: dropped %u unusable layer(s)", dropped);
    if (passthrough && !passthrough_logged++) LOG("xrEndFrame: passthrough layer -> ALPHA_BLEND environment");
    static int views_logged;
    if (views_logged < 3) {  // describe the stereo layer layout a few times to debug eye/stereo problems
        for (uint32_t i = 0; i < info->layerCount; ++i) {
            const XrCompositionLayerBaseHeader *l = info->layers[i];
            if (l && l->type == XR_TYPE_COMPOSITION_LAYER_QUAD) {
                const XrCompositionLayerQuad *qd = (const XrCompositionLayerQuad *)l;
                char chain[160] = "";
                size_t used = 0;
                for (const XrBaseInStructure *n = (const XrBaseInStructure *)qd->next; n && used < sizeof(chain) - 16; n = n->next) {
                    int w = snprintf(chain + used, sizeof(chain) - used, " %d", n->type);
                    if (n->type == 1000040000)  // XrCompositionLayerImageLayoutFB
                        w += snprintf(chain + used + w, sizeof(chain) - used - w, "(flags=0x%llx)",
                                      (unsigned long long)((const XrCompositionLayerImageLayoutFB *)n)->flags);
                    used += w;
                }
                LOG("quad layer: flags=0x%llx size=%.2fx%.2f next:%s", (unsigned long long)qd->layerFlags,
                    qd->size.width, qd->size.height, chain);
            }
            if (!l || l->type != XR_TYPE_COMPOSITION_LAYER_PROJECTION) continue;
            const XrCompositionLayerProjection *p = (const XrCompositionLayerProjection *)l;
            for (uint32_t v = 0; v < p->viewCount; ++v) {
                const XrCompositionLayerProjectionView *pv = &p->views[v];
                if (v == 0 && pv->next) LOG("projection view 0 chains struct type %d", ((const XrBaseInStructure *)pv->next)->type);
                LOG("projection view %u: space=%p%s swapchain=%p rect=%d,%d %dx%d array=%u pos=%.3f,%.3f,%.3f fov=%.2f/%.2f",
                    v, (void *)(uintptr_t)p->space, p->space == view_space_handle ? "(VIEW)" : "", (void *)(uintptr_t)pv->subImage.swapchain, pv->subImage.imageRect.offset.x,
                    pv->subImage.imageRect.offset.y, pv->subImage.imageRect.extent.width,
                    pv->subImage.imageRect.extent.height, pv->subImage.imageArrayIndex, pv->pose.position.x,
                    pv->pose.position.y, pv->pose.position.z, pv->fov.angleLeft, pv->fov.angleRight);
            }
            ++views_logged;
        }
    }
    XrResult result = fn(session, (dropped || swapped || passthrough) ? &fixed : info);
    if (result == XR_ERROR_ENVIRONMENT_BLEND_MODE_UNSUPPORTED && passthrough && !alpha_blend_failed) {
        alpha_blend_failed = 1;  // runtime refuses camera blending: resubmit as a normal opaque frame
        LOG("xrEndFrame: ALPHA_BLEND unsupported, passthrough falls back to opaque");
        return xrEndFrame(session, info);
    }
    if (XR_FAILED(result) && failures++ < 3) {  // describe rejected frames to aid debugging
        LOG("xrEndFrame failed %d: blend=%d layers=%u", result, fixed.environmentBlendMode, fixed.layerCount);
        for (uint32_t i = 0; i < fixed.layerCount; ++i)
            LOG("  layer %u type=%d flags=0x%llx", i, fixed.layers[i]->type,
                (unsigned long long)fixed.layers[i]->layerFlags);
    }
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrLocateHandJointsEXT(XrHandTrackerEXT tracker,
        const XrHandJointsLocateInfoEXT *info, XrHandJointLocationsEXT *locations) {
    if (!controller_fix) {
        PFN_xrLocateHandJointsEXT fn = (PFN_xrLocateHandJointsEXT)lookup(active_instance, "xrLocateHandJointsEXT");
        return fn ? fn(tracker, info, locations) : XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    if (!locations) return XR_ERROR_VALIDATION_FAILURE;
    locations->isActive = XR_FALSE;
    for (XrBaseOutStructure *p = locations->next; p; p = p->next)
        if (p->type == XR_TYPE_HAND_TRACKING_AIM_STATE_FB) ((XrHandTrackingAimStateFB *)p)->status = 0;
    return XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL xrGetCurrentInteractionProfile(XrSession session, XrPath user,
        XrInteractionProfileState *state) {
    PFN_xrGetCurrentInteractionProfile fn =
        (PFN_xrGetCurrentInteractionProfile)lookup(active_instance, "xrGetCurrentInteractionProfile");
    if (!fn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    XrResult result = fn(session, user, state);
    if (!controller_fix || XR_FAILED(result) || !state || !state->interactionProfile || !active_instance)
        return result;
    PFN_xrPathToString to_string = (PFN_xrPathToString)lookup(active_instance, "xrPathToString");
    PFN_xrStringToPath to_path = (PFN_xrStringToPath)lookup(active_instance, "xrStringToPath");
    char name[XR_MAX_PATH_LENGTH];
    uint32_t size = 0;
    if (to_string && to_path &&
        XR_SUCCEEDED(to_string(active_instance, state->interactionProfile, sizeof(name), &size, name)) &&
        (strstr(name, "/valve/") || strstr(name, "/khr/generic_controller")))
        to_path(active_instance, "/interaction_profiles/oculus/touch_controller", &state->interactionProfile);
    return result;
}

XRAPI_ATTR XrResult XRAPI_CALL xrEnumerateInstanceExtensionProperties(const char *layer, uint32_t capacity,
        uint32_t *count, XrExtensionProperties *properties) {
    PFN_xrEnumerateInstanceExtensionProperties fn = (PFN_xrEnumerateInstanceExtensionProperties)lookup(
        XR_NULL_HANDLE, "xrEnumerateInstanceExtensionProperties");
    if (!fn) return XR_ERROR_INITIALIZATION_FAILED;
    if (!count) return XR_ERROR_VALIDATION_FAILURE;
    if ((!foveation_fix && !passthrough_emul && !scene_emul && !controller_models) || layer)
        return fn(layer, capacity, count, properties);

    uint32_t total = 0;
    XrResult result = fn(layer, 0, &total, NULL);
    if (XR_FAILED(result)) return result;
    XrExtensionProperties *all = calloc(total + 2 + SCENE_EXTENSION_COUNT, sizeof(*all));
    if (!all) return XR_ERROR_OUT_OF_MEMORY;
    for (uint32_t i = 0; i < total; ++i) all[i].type = XR_TYPE_EXTENSION_PROPERTIES;
    result = fn(layer, total, &total, all);
    if (XR_FAILED(result)) { free(all); return result; }

    uint32_t kept = 0;
    for (uint32_t i = 0; i < total; ++i) {
        if (!strcmp(all[i].extensionName, "XR_FB_passthrough")) runtime_has_fb_passthrough = 1;
        if (!strcmp(all[i].extensionName, XR_FB_RENDER_MODEL_EXTENSION_NAME)) runtime_has_render_model = 1;
        if (foveation_fix && strstr(all[i].extensionName, "foveation")) continue;
        if (properties && kept < capacity) properties[kept] = all[i];
        ++kept;
    }
    if (scene_emul) {
        for (size_t e = 0; e < SCENE_EXTENSION_COUNT; ++e) {
            int present = 0;
            for (uint32_t i = 0; i < total; ++i) present |= !strcmp(all[i].extensionName, scene_extensions[e]);
            if (present) continue;
            XrExtensionProperties fake = {XR_TYPE_EXTENSION_PROPERTIES, NULL, "", 1};
            snprintf(fake.extensionName, sizeof(fake.extensionName), "%s", scene_extensions[e]);
            if (properties && kept < capacity) properties[kept] = fake;
            ++kept;
        }
    }
    if (passthrough_emul && !runtime_has_fb_passthrough) {
        XrExtensionProperties fake = {XR_TYPE_EXTENSION_PROPERTIES, NULL, "XR_FB_passthrough", 4};
        if (properties && kept < capacity) properties[kept] = fake;
        ++kept;
    }
    if (controller_models && render_models_available && !runtime_has_render_model) {
        XrExtensionProperties fake = {XR_TYPE_EXTENSION_PROPERTIES, NULL, XR_FB_RENDER_MODEL_EXTENSION_NAME,
                                      XR_FB_render_model_SPEC_VERSION};
        if (properties && kept < capacity) properties[kept] = fake;
        ++kept;
    }
    free(all);
    *count = kept;
    return (capacity && capacity < kept) ? XR_ERROR_SIZE_INSUFFICIENT : XR_SUCCESS;
}

XRAPI_ATTR XrResult XRAPI_CALL xrEnumerateApiLayerProperties(uint32_t capacity, uint32_t *count,
        XrApiLayerProperties *properties) {
    PFN_xrEnumerateApiLayerProperties fn =
        (PFN_xrEnumerateApiLayerProperties)lookup(XR_NULL_HANDLE, "xrEnumerateApiLayerProperties");
    return fn ? fn(capacity, count, properties) : XR_ERROR_INITIALIZATION_FAILED;
}

// For native/xrshim: emulated functions that overport's dispatcher doesn't know (it never asks us for them).
__attribute__((visibility("default"))) PFN_xrVoidFunction framebridge_extension_proc(const char *name) {
    pthread_once(&init_once, initialize);
    return name ? render_model_emulation(name) : NULL;
}

XRAPI_ATTR XrResult XRAPI_CALL xrGetInstanceProcAddr(XrInstance instance, const char *name,
        PFN_xrVoidFunction *function) {
    pthread_once(&init_once, initialize);
    if (!next_gipa) return XR_ERROR_INITIALIZATION_FAILED;
    if (!name || !function) return XR_ERROR_VALIDATION_FAILURE;
#define HOOK(fn) if (!strcmp(name, #fn)) { *function = (PFN_xrVoidFunction)fn; return XR_SUCCESS; }
    if (!strcmp(name, "xrConvertTimespecTimeToTimeKHR") || !strcmp(name, "xrConvertTimeToTimespecTimeKHR")) {
        PFN_xrVoidFunction real = NULL;
        next_gipa(instance, name, &real);
        if (!strcmp(name, "xrConvertTimespecTimeToTimeKHR")) {
            runtime_timespec_to_time = (PFN_xrConvertTimespecTimeToTimeKHR)real;
            *function = (PFN_xrVoidFunction)emu_timespec_to_time;
        } else {
            runtime_time_to_timespec = (PFN_xrConvertTimeToTimespecTimeKHR)real;
            *function = (PFN_xrVoidFunction)emu_time_to_timespec;
        }
        return XR_SUCCESS;
    }
    PFN_xrVoidFunction emulated = passthrough_emulation(name);
    if (!emulated) emulated = scene_emulation(name);
    if (!emulated) emulated = render_model_emulation(name);
    if (!emulated && emulate_scene && !strcmp(name, "xrLocateSpacesKHR")) emulated = (PFN_xrVoidFunction)emu_locate_spaces_khr;
    if (emulated) { *function = emulated; return XR_SUCCESS; }
    HOOK(xrGetSystemProperties)
    HOOK(xrCreateReferenceSpace)
    HOOK(xrLocateViews)
    HOOK(xrPollEvent)
    HOOK(xrWaitFrame)
    HOOK(xrCreateSession)
    HOOK(xrEnumerateSwapchainImages)
    HOOK(xrAcquireSwapchainImage)
    HOOK(xrLocateSpace)
    HOOK(xrLocateSpaces)
    HOOK(xrDestroySpace)
    HOOK(xrCreateInstance)
    HOOK(xrEnumerateViewConfigurationViews)
    HOOK(xrCreateSwapchain)
    HOOK(xrDestroySwapchain)
    HOOK(xrEndFrame)
    HOOK(xrLocateHandJointsEXT)
    HOOK(xrGetCurrentInteractionProfile)
    HOOK(xrEnumerateInstanceExtensionProperties)
#undef HOOK
    return next_gipa(instance, name, function);
}

// ---------------------------------------------------------------------------
// Every other symbol exported by overport's generic loader is forwarded
// unchanged (see forwarders.S), so callers that dlsym() entry points directly
// keep working. Unresolved entries return XR_ERROR_FUNCTION_UNSUPPORTED.
__attribute__((visibility("hidden"))) XrResult frame_unsupported(void) { return XR_ERROR_FUNCTION_UNSUPPORTED; }

#define FORWARD(fn) extern void *fwd_##fn;
#include "forwarders.inc"
#undef FORWARD

__attribute__((constructor)) static void resolve_forwarders(void) {
    pthread_once(&init_once, initialize);
    if (!loader) return;
    void *p;
#define FORWARD(fn) if ((p = dlsym(loader, #fn))) fwd_##fn = p;
#include "forwarders.inc"
#undef FORWARD
}
