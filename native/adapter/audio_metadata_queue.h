// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
// Experimental lifetime repair for the pinned Meta XR Audio Wwise build.
// A queued metadata object must not be reclaimed while the current immutable
// AkAudioObjects snapshot still references it. Preserve the SDK's queue mutex,
// virtual destructor, list linkage, audio processing, and dirty-flag writes.
static _Thread_local const unsigned char *mx_audio_objects;
static unsigned mx_audio_held,mx_audio_reclaimed;
static int mx_audio_referenced(const void *pointer) {
    if(!mx_audio_objects)return 0;
    uint32_t count;memcpy(&count,mx_audio_objects,4);
    const unsigned char *const *objects;memcpy(&objects,mx_audio_objects+16,8);
    // Refuse to reclaim on an unrecognised snapshot rather than dereferencing
    // unbounded arrays. These bounds exceed the game's ordinary voice counts.
    if(count>4096 || (count && !objects))return 1;
    for(unsigned i=0;i<count;i++) {
        const unsigned char *object=objects[i];if(!object)continue;
        uint32_t metadata_count;const unsigned char *metadata;
        memcpy(&metadata,object+104,8);memcpy(&metadata_count,object+112,4);
        if(metadata_count>4096 || (metadata_count && !metadata))return 1;
        for(unsigned j=0;j<metadata_count;j++) {
            const void *parameter;memcpy(&parameter,metadata+(size_t)j*24+8,8);
            if(parameter==pointer)return 1;
        }
    }
    return 0;
}
// Caller holds the original queue's mutex, just as ExecuteKillList did.
static void mx_audio_drain(void **head,size_t next_offset) {
    void **link=head;
    while(*link) {
        void *pointer=*link;void **next=(void**)((unsigned char*)pointer+next_offset);
        if(mx_audio_referenced(pointer)) {
            link=next;
            unsigned held=__atomic_fetch_add(&mx_audio_held,1,__ATOMIC_RELAXED);
            if(held<8)LOG("FrameBridge audio metadata: queued metadata=%p still referenced by audio frame; retained",pointer);
        } else {
            *link=*next;
            void (**table)(void*)=*(void (***)(void*))pointer;
            table[1](pointer);
            __atomic_fetch_add(&mx_audio_reclaimed,1,__ATOMIC_RELAXED);
        }
    }
}
