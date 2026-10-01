// Test fixture for patches/frame/swapchain_limit.py: overport-style size guard in xrCreateSwapchain.
// Build: aarch64-linux-android29-clang -shared -O2 -fPIC fakeoverport.c -o ../libfakeoverport_arm64.so
#include <stdlib.h>

typedef struct {
    int type; void *next; unsigned long long flags, usage; long long format;
    unsigned samples, width, height, faces, arrays, mips;
} CreateInfo;

int xrCreateSwapchain(void *session, const CreateInfo *ci, void **out) {
    if (ci->width > 4096 || ci->height > 4096) abort();  // "Wrong createInfo size"
    *out = session;
    return 0;
}
