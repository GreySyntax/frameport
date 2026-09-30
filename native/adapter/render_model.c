// SPDX-License-Identifier: GPL-3.0-only
// XR_FB_render_model emulation (controller_models): serves Steam Frame controller models to games that ask the
// runtime for controller models (Meta XR SDK "runtime controller" / OVRRuntimeController, Unreal OculusXR), so they
// show the controllers in your hands instead of Quest Touch controllers. Games that ship their own controller meshes
// don't use this extension and are not affected.
//
// The models are glTF binaries converted on the Frame from its own SteamVR render models by the FramePort agent:
//   /sdcard/Android/data/<package>/files/framebridge/controller_{left,right}.glb
// Only active when both files exist and the runtime lacks XR_FB_render_model (a native one is used as is).
// Included by frame_adapter.c.

static int render_models_available;    // both .glb files present
static int runtime_has_render_model;
static int emulate_render_model;
static char render_model_dir[512];

static const char *const render_model_paths[2] = {"/model_fb/controller/left", "/model_fb/controller/right"};
static const char *const render_model_files[2] = {"controller_left.glb", "controller_right.glb"};
static const char *const render_model_names[2] = {"Steam Frame Controller Left", "Steam Frame Controller Right"};
#define RENDER_MODEL_VENDOR_VALVE 0x28DE
#define RENDER_MODEL_KEY_BASE 0x46500000u  // keys: base + 1 (left), base + 2 (right)

static void render_model_scan(const char *dir) {
    snprintf(render_model_dir, sizeof(render_model_dir), "%s", dir);
    char path[640];
    int found = 0;
    for (int i = 0; i < 2; ++i) {
        snprintf(path, sizeof(path), "%s/%s", render_model_dir, render_model_files[i]);
        FILE *f = fopen(path, "rb");
        if (f) { ++found; fclose(f); }
    }
    render_models_available = found == 2;
    LOG("controller models %s in %s", render_models_available ? "found" : "not found", render_model_dir);
}

static void render_model_init(const char *package) {
    if (!package || !*package) return;
    char dir[512];
    snprintf(dir, sizeof(dir), "/sdcard/Android/data/%s/files/framebridge", package);
    render_model_scan(dir);
}

static int render_model_index(XrPath path) {
    PFN_xrStringToPath to_path = (PFN_xrStringToPath)lookup(active_instance, "xrStringToPath");
    if (!to_path || !active_instance) return -1;
    for (int i = 0; i < 2; ++i) {
        XrPath p = XR_NULL_PATH;
        if (XR_SUCCEEDED(to_path(active_instance, render_model_paths[i], &p)) && p == path) return i;
    }
    return -1;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_enumerate_render_model_paths(XrSession session, uint32_t capacity,
        uint32_t *count, XrRenderModelPathInfoFB *paths) {
    (void)session;
    if (!count) return XR_ERROR_VALIDATION_FAILURE;
    *count = 2;
    if (!capacity) return XR_SUCCESS;
    if (capacity < 2 || !paths) return XR_ERROR_SIZE_INSUFFICIENT;
    PFN_xrStringToPath to_path = (PFN_xrStringToPath)lookup(active_instance, "xrStringToPath");
    if (!to_path) return XR_ERROR_RUNTIME_FAILURE;
    for (int i = 0; i < 2; ++i) {
        XrResult r = to_path(active_instance, render_model_paths[i], &paths[i].path);
        if (XR_FAILED(r)) return r;
    }
    return XR_SUCCESS;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_get_render_model_properties(XrSession session, XrPath path,
        XrRenderModelPropertiesFB *properties) {
    (void)session;
    if (!properties) return XR_ERROR_VALIDATION_FAILURE;
    XrRenderModelFlagsFB requested = XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_1_BIT_FB;
    for (const XrBaseInStructure *p = (const XrBaseInStructure *)properties->next; p; p = p->next)
        if (p->type == XR_TYPE_RENDER_MODEL_CAPABILITIES_REQUEST_FB)
            requested = ((const XrRenderModelCapabilitiesRequestFB *)p)->flags;
    int i = render_model_index(path);
    if (i < 0) {
        properties->modelKey = XR_NULL_RENDER_MODEL_KEY_FB;
        return XR_ERROR_PATH_UNSUPPORTED;
    }
    properties->vendorId = RENDER_MODEL_VENDOR_VALVE;
    snprintf(properties->modelName, sizeof(properties->modelName), "%s", render_model_names[i]);
    properties->modelKey = (XrRenderModelKeyFB)(RENDER_MODEL_KEY_BASE + 1 + i);
    properties->modelVersion = 1;
    // One mesh, one PNG texture, no animation: fits subset 1 and therefore every subset an app can ask for.
    properties->flags = requested & XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_2_BIT_FB
        ? XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_2_BIT_FB : XR_RENDER_MODEL_SUPPORTS_GLTF_2_0_SUBSET_1_BIT_FB;
    static int logged[2];
    if (!logged[i]++) LOG("render model %s -> %s/%s", render_model_paths[i], render_model_dir, render_model_files[i]);
    return XR_SUCCESS;
}

static XRAPI_ATTR XrResult XRAPI_CALL emu_load_render_model(XrSession session, const XrRenderModelLoadInfoFB *info,
        XrRenderModelBufferFB *buffer) {
    (void)session;
    if (!info || !buffer) return XR_ERROR_VALIDATION_FAILURE;
    uint64_t key = (uint64_t)info->modelKey;
    if (key != RENDER_MODEL_KEY_BASE + 1 && key != RENDER_MODEL_KEY_BASE + 2) return XR_ERROR_RENDER_MODEL_KEY_INVALID_FB;
    int i = (int)(key - RENDER_MODEL_KEY_BASE - 1);
    char path[640];
    snprintf(path, sizeof(path), "%s/%s", render_model_dir, render_model_files[i]);
    FILE *f = fopen(path, "rb");
    if (!f) { LOG("render model %s missing", path); return XR_ERROR_RENDER_MODEL_KEY_INVALID_FB; }
    long size = -1;
    if (!fseek(f, 0, SEEK_END)) size = ftell(f);
    if (size <= 0 || size > 0x7fffffffL || fseek(f, 0, SEEK_SET)) { fclose(f); return XR_ERROR_RUNTIME_FAILURE; }
    buffer->bufferCountOutput = (uint32_t)size;
    if (!buffer->bufferCapacityInput) { fclose(f); return XR_SUCCESS; }
    if (buffer->bufferCapacityInput < (uint32_t)size || !buffer->buffer) { fclose(f); return XR_ERROR_SIZE_INSUFFICIENT; }
    size_t got = fread(buffer->buffer, 1, (size_t)size, f);
    fclose(f);
    return got == (size_t)size ? XR_SUCCESS : XR_ERROR_RUNTIME_FAILURE;
}

static PFN_xrVoidFunction render_model_emulation(const char *name) {
    if (!emulate_render_model) return NULL;
    if (!strcmp(name, "xrEnumerateRenderModelPathsFB")) return (PFN_xrVoidFunction)emu_enumerate_render_model_paths;
    if (!strcmp(name, "xrGetRenderModelPropertiesFB")) return (PFN_xrVoidFunction)emu_get_render_model_properties;
    if (!strcmp(name, "xrLoadRenderModelFB")) return (PFN_xrVoidFunction)emu_load_render_model;
    return NULL;
}
