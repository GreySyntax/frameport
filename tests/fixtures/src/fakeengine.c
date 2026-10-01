// Test fixture for patches/frame/vk_sanitize.py: an engine library that finds Vulkan with dlopen.
// Build: aarch64-linux-android29-clang -shared -O2 -fPIC fakeengine.c -o ../libfakeengine_arm64.so
#include <dlfcn.h>

void *load_vulkan(void) { return dlopen("libvulkan.so", RTLD_NOW); }
