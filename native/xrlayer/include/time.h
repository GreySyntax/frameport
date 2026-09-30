/* Minimal <time.h> for the freestanding timefix layer (aarch64 Linux: time_t and long are 64-bit). The layer links
 * against nothing at build time; clock_gettime resolves to the process's libc when the loader dlopens it. */
#pragma once
typedef long time_t;
struct timespec { time_t tv_sec; long tv_nsec; };
#define CLOCK_MONOTONIC 1
int clock_gettime(int clock_id, struct timespec *ts);
