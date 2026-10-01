// SPDX-License-Identifier: GPL-3.0-only
// FramePort Vulkan shim: drops invalid pNext pointers from render-pass create infos before they reach the Vulkan
// loader. Some engines (e.g. Unreal builds such as Deadpool VR) leave the pNext of unused attachment references
// uninitialized. The Frame's driver never reads it, but Lepton always loads Steam's Fossilize layer, which records
// every render pass, follows the garbage pointer and crashes (SIGSEGV in libVkLayer_fossilize.so).
//
// The engine finds Vulkan with dlopen("libvulkan.so") + dlsym, so FramePort points that string in the engine library
// at this shim (patches/frame/vk_sanitize.py). dlsym on the shim finds the functions below first and every other
// symbol in its dependency, the real libvulkan.so. Valid pointers are kept; a pointer is dropped only when it isn't
// readable memory or doesn't point at a structure type the parent may chain.
#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TAG "FrameBridge"
#define EXPORT __attribute__((visibility("default")))
#define LOG(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

static void *real_vk;
static PFN_vkGetInstanceProcAddr real_gipa;
static PFN_vkGetDeviceProcAddr real_gdpa;
static PFN_vkCreateRenderPass2 real_rp2_trampoline;
static pthread_once_t once = PTHREAD_ONCE_INIT;

static void init(void) {
    real_vk = dlopen("libvulkan.so", RTLD_NOW | RTLD_LOCAL);
    if (real_vk) {
        real_gipa = (PFN_vkGetInstanceProcAddr)dlsym(real_vk, "vkGetInstanceProcAddr");
        real_gdpa = (PFN_vkGetDeviceProcAddr)dlsym(real_vk, "vkGetDeviceProcAddr");
        real_rp2_trampoline = (PFN_vkCreateRenderPass2)dlsym(real_vk, "vkCreateRenderPass2");
    }
    LOG("vk shim: libvulkan.so %s", real_gipa && real_gdpa ? "OK" : "MISSING");
}

// ---------------------------------------------------------------- pointer checks

static pthread_mutex_t probe_lock = PTHREAD_MUTEX_INITIALIZER;
static int probe_fd[2] = {-1, -1};

// readable(p, n): the kernel copies from p without faulting (write to a pipe fails with EFAULT otherwise)
static int readable(const void *p, size_t n) {
    if (!p || ((uintptr_t)p & 7)) return 0;
    pthread_mutex_lock(&probe_lock);
    if (probe_fd[0] < 0 && pipe2(probe_fd, O_CLOEXEC | O_NONBLOCK)) {
        pthread_mutex_unlock(&probe_lock);
        return 1;  // can't check: trust the pointer (the previous behaviour)
    }
    ssize_t w = write(probe_fd[1], p, n);
    int ok = w == (ssize_t)n;
    char sink[64];
    while (w > 0) {
        ssize_t r = read(probe_fd[0], sink, w < (ssize_t)sizeof sink ? (size_t)w : sizeof sink);
        if (r <= 0) break;
        w -= r;
    }
    pthread_mutex_unlock(&probe_lock);
    return ok;
}

static int valid_next(const void *p, const VkStructureType *allowed, int n) {
    if (!readable(p, sizeof(VkBaseInStructure))) return 0;
    VkStructureType s = ((const VkBaseInStructure *)p)->sType;
    for (int i = 0; i < n; i++)
        if (allowed[i] == s) return 1;
    return 0;
}

// ---------------------------------------------------------------- scratch memory for the cleaned copies

typedef struct { void *blocks[64]; int n; } Arena;

static void *arena_dup(Arena *a, const void *src, size_t size) {
    if (a->n >= 64) return NULL;
    void *p = malloc(size ? size : 1);
    if (!p) return NULL;
    memcpy(p, src, size);
    a->blocks[a->n++] = p;
    return p;
}

static void arena_free(Arena *a) {
    for (int i = 0; i < a->n; i++) free(a->blocks[i]);
    a->n = 0;
}

static int fixes_logged;

static const VkStructureType REF_NEXT[] = {VK_STRUCTURE_TYPE_ATTACHMENT_REFERENCE_STENCIL_LAYOUT};
static const VkStructureType DESC_NEXT[] = {VK_STRUCTURE_TYPE_ATTACHMENT_DESCRIPTION_STENCIL_LAYOUT};
static const VkStructureType DEP_NEXT[] = {VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};

// A copy of `refs` whose pNext pointers are valid (or NULL); the original when nothing needed fixing.
static const VkAttachmentReference2 *clean_refs(Arena *a, const VkAttachmentReference2 *refs, uint32_t count, int *fixes) {
    if (!refs || !count) return refs;
    VkAttachmentReference2 *copy = NULL;
    for (uint32_t i = 0; i < count; i++) {
        if (refs[i].pNext && !valid_next(refs[i].pNext, REF_NEXT, 1)) {
            if (!copy && !(copy = arena_dup(a, refs, sizeof *refs * count))) return refs;
            copy[i].pNext = NULL;
            (*fixes)++;
        }
    }
    return copy ? copy : refs;
}

// Structures that may be chained to a subpass or to the create info, with their size (so a chain can be cut after
// them) and whether they hold an attachment reference to clean.
static size_t chain_size(VkStructureType s) {
    switch (s) {
    case VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_DEPTH_STENCIL_RESOLVE: return sizeof(VkSubpassDescriptionDepthStencilResolve);
    case VK_STRUCTURE_TYPE_FRAGMENT_SHADING_RATE_ATTACHMENT_INFO_KHR: return sizeof(VkFragmentShadingRateAttachmentInfoKHR);
    case VK_STRUCTURE_TYPE_MULTISAMPLED_RENDER_TO_SINGLE_SAMPLED_INFO_EXT: return sizeof(VkMultisampledRenderToSingleSampledInfoEXT);
    case VK_STRUCTURE_TYPE_RENDER_PASS_FRAGMENT_DENSITY_MAP_CREATE_INFO_EXT: return sizeof(VkRenderPassFragmentDensityMapCreateInfoEXT);
    case VK_STRUCTURE_TYPE_RENDER_PASS_MULTIVIEW_CREATE_INFO: return sizeof(VkRenderPassMultiviewCreateInfo);
    case VK_STRUCTURE_TYPE_RENDER_PASS_INPUT_ATTACHMENT_ASPECT_CREATE_INFO: return sizeof(VkRenderPassInputAttachmentAspectCreateInfo);
    case VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_CONTROL_EXT: return sizeof(VkRenderPassCreationControlEXT);
    case VK_STRUCTURE_TYPE_RENDER_PASS_CREATION_FEEDBACK_CREATE_INFO_EXT: return sizeof(VkRenderPassCreationFeedbackCreateInfoEXT);
    case VK_STRUCTURE_TYPE_RENDER_PASS_SUBPASS_FEEDBACK_CREATE_INFO_EXT: return sizeof(VkRenderPassSubpassFeedbackCreateInfoEXT);
    case VK_STRUCTURE_TYPE_RENDER_PASS_STRIPE_BEGIN_INFO_ARM: return 0;  // not a create-info chain member
    default: return 0;
    }
}

// Walk a pNext chain from `head` (the pNext value of a structure we own a copy of); returns the new head. Known
// members are copied so a bad link after them can be cut; an unknown but valid member ends the checks (it can't be
// copied without knowing its size, so what follows it stays as is).
static const void *clean_chain(Arena *a, const void *head, int *fixes) {
    const void *first = head;
    VkBaseOutStructure *prev = NULL;  // our copy of the previous member
    const void *cur = head;
    while (cur) {
        if (!readable(cur, sizeof(VkBaseInStructure))) {
            if (prev) prev->pNext = NULL; else first = NULL;
            (*fixes)++;
            break;
        }
        VkStructureType s = ((const VkBaseInStructure *)cur)->sType;
        size_t size = chain_size(s);
        if (!size || !readable(cur, size)) {
            if (!size && s < 1100000000u && (s < 1000u || s >= 1000000000u)) break;  // plausible unknown member: keep
            if (prev) prev->pNext = NULL; else first = NULL;
            (*fixes)++;
            break;
        }
        VkBaseOutStructure *copy = arena_dup(a, cur, size);
        if (!copy) break;
        if (s == VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_DEPTH_STENCIL_RESOLVE) {
            VkSubpassDescriptionDepthStencilResolve *r = (void *)copy;
            r->pDepthStencilResolveAttachment = clean_refs(a, r->pDepthStencilResolveAttachment, 1, fixes);
        } else if (s == VK_STRUCTURE_TYPE_FRAGMENT_SHADING_RATE_ATTACHMENT_INFO_KHR) {
            VkFragmentShadingRateAttachmentInfoKHR *r = (void *)copy;
            r->pFragmentShadingRateAttachment = clean_refs(a, r->pFragmentShadingRateAttachment, 1, fixes);
        }
        if (prev) prev->pNext = copy; else first = copy;
        prev = copy;
        cur = copy->pNext;
    }
    return first;
}

static VkRenderPassCreateInfo2 clean_create_info(Arena *a, const VkRenderPassCreateInfo2 *ci, int *fixes) {
    VkRenderPassCreateInfo2 out = *ci;
    out.pNext = clean_chain(a, ci->pNext, fixes);
    if (ci->pAttachments && ci->attachmentCount) {
        VkAttachmentDescription2 *d = arena_dup(a, ci->pAttachments, sizeof *d * ci->attachmentCount);
        if (d) {
            for (uint32_t i = 0; i < ci->attachmentCount; i++)
                if (d[i].pNext && !valid_next(d[i].pNext, DESC_NEXT, 1)) { d[i].pNext = NULL; (*fixes)++; }
            out.pAttachments = d;
        }
    }
    if (ci->pSubpasses && ci->subpassCount) {
        VkSubpassDescription2 *sp = arena_dup(a, ci->pSubpasses, sizeof *sp * ci->subpassCount);
        if (sp) {
            for (uint32_t i = 0; i < ci->subpassCount; i++) {
                sp[i].pNext = clean_chain(a, sp[i].pNext, fixes);
                sp[i].pInputAttachments = clean_refs(a, sp[i].pInputAttachments, sp[i].inputAttachmentCount, fixes);
                sp[i].pColorAttachments = clean_refs(a, sp[i].pColorAttachments, sp[i].colorAttachmentCount, fixes);
                sp[i].pResolveAttachments = clean_refs(a, sp[i].pResolveAttachments, sp[i].colorAttachmentCount, fixes);
                sp[i].pDepthStencilAttachment = clean_refs(a, sp[i].pDepthStencilAttachment, 1, fixes);
            }
            out.pSubpasses = sp;
        }
    }
    if (ci->pDependencies && ci->dependencyCount) {
        VkSubpassDependency2 *dep = arena_dup(a, ci->pDependencies, sizeof *dep * ci->dependencyCount);
        if (dep) {
            for (uint32_t i = 0; i < ci->dependencyCount; i++)
                if (dep[i].pNext && !valid_next(dep[i].pNext, DEP_NEXT, 1)) { dep[i].pNext = NULL; (*fixes)++; }
            out.pDependencies = dep;
        }
    }
    return out;
}

// ---------------------------------------------------------------- entry points

#define MAX_DEVICES 8
static struct { VkDevice device; PFN_vkCreateRenderPass2 rp2, rp2khr; } devices[MAX_DEVICES];
static PFN_vkCreateRenderPass2 fallback_rp2khr;  // from vkGetInstanceProcAddr
static pthread_mutex_t dev_lock = PTHREAD_MUTEX_INITIALIZER;

static PFN_vkCreateRenderPass2 real_for(VkDevice device, int khr) {
    PFN_vkCreateRenderPass2 fn = NULL;
    pthread_mutex_lock(&dev_lock);
    for (int i = 0; i < MAX_DEVICES; i++)
        if (devices[i].device == device) { fn = khr ? devices[i].rp2khr : devices[i].rp2; break; }
    pthread_mutex_unlock(&dev_lock);
    if (!fn && real_gdpa) fn = (PFN_vkCreateRenderPass2)real_gdpa(device, khr ? "vkCreateRenderPass2KHR" : "vkCreateRenderPass2");
    if (!fn) fn = khr ? fallback_rp2khr : real_rp2_trampoline;
    return fn;
}

static VkResult create_render_pass2(VkDevice device, const VkRenderPassCreateInfo2 *ci, const VkAllocationCallbacks *alloc,
                                    VkRenderPass *rp, int khr) {
    PFN_vkCreateRenderPass2 real = real_for(device, khr);
    if (!real) return VK_ERROR_INITIALIZATION_FAILED;
    if (!ci) return real(device, ci, alloc, rp);
    Arena a = {0};
    int fixes = 0;
    VkRenderPassCreateInfo2 clean = clean_create_info(&a, ci, &fixes);
    if (fixes && fixes_logged < 5) {
        fixes_logged++;
        LOG("vk shim: dropped %d invalid pNext pointer(s) in vkCreateRenderPass2%s", fixes, khr ? "KHR" : "");
    }
    VkResult r = real(device, fixes ? &clean : ci, alloc, rp);
    arena_free(&a);
    return r;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkCreateRenderPass2(VkDevice device, const VkRenderPassCreateInfo2 *ci,
                                                          const VkAllocationCallbacks *alloc, VkRenderPass *rp) {
    pthread_once(&once, init);
    return create_render_pass2(device, ci, alloc, rp, 0);
}

static VKAPI_ATTR VkResult VKAPI_CALL create_render_pass2_khr(VkDevice device, const VkRenderPassCreateInfo2 *ci,
                                                             const VkAllocationCallbacks *alloc, VkRenderPass *rp) {
    return create_render_pass2(device, ci, alloc, rp, 1);
}

static PFN_vkVoidFunction wrap(const char *name) {
    if (!strcmp(name, "vkCreateRenderPass2")) return (PFN_vkVoidFunction)vkCreateRenderPass2;
    if (!strcmp(name, "vkCreateRenderPass2KHR")) return (PFN_vkVoidFunction)create_render_pass2_khr;
    return NULL;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char *name) {
    pthread_once(&once, init);
    if (!real_gdpa || !name) return NULL;
    PFN_vkVoidFunction fn = real_gdpa(device, name);
    PFN_vkVoidFunction w = fn ? wrap(name) : NULL;
    if (!w) return fn;
    int khr = name[strlen(name) - 1] == 'R';
    pthread_mutex_lock(&dev_lock);
    int slot = -1;
    for (int i = 0; i < MAX_DEVICES; i++) {
        if (devices[i].device == device) { slot = i; break; }
        if (slot < 0 && !devices[i].device) slot = i;
    }
    if (slot >= 0) {
        devices[slot].device = device;
        if (khr) devices[slot].rp2khr = (PFN_vkCreateRenderPass2)fn; else devices[slot].rp2 = (PFN_vkCreateRenderPass2)fn;
    }
    pthread_mutex_unlock(&dev_lock);
    return w;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *name) {
    pthread_once(&once, init);
    if (!real_gipa || !name) return NULL;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
    if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)vkGetInstanceProcAddr;
    PFN_vkVoidFunction fn = real_gipa(instance, name);
    PFN_vkVoidFunction w = fn ? wrap(name) : NULL;
    if (!w) return fn;
    if (!strcmp(name, "vkCreateRenderPass2KHR")) fallback_rp2khr = (PFN_vkCreateRenderPass2)fn;
    return w;
}
