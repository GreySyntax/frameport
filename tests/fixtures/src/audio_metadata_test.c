// SPDX-License-Identifier: GPL-3.0-only
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define LOG(...) ((void)0)
#include "audio_metadata_queue.h"
static unsigned destroyed;
static void destroy(void *p) {destroyed++;free(p);}
static void (*vtable[2])(void*)={NULL,destroy};
static void *parameter(size_t next_offset,void *next) {
    unsigned char *p=calloc(1,48);assert(p);
    memcpy(p,&(void*){vtable},8);memcpy(p+next_offset,&next,8);p[8]=255;
    return p;
}
static void exercise(size_t next_offset) {
    void *fourth=parameter(next_offset,NULL),*third=parameter(next_offset,fourth);
    void *second=parameter(next_offset,third),*first=parameter(next_offset,second),*head=first;
    unsigned char metadata[48]={0},object[120]={0},snapshot[24]={0};
    memcpy(metadata+8,&first,8);memcpy(metadata+32,&third,8);
    void *metadata_pointer=metadata;memcpy(object+104,&metadata_pointer,8);
    uint32_t two=2,one=1;memcpy(object+112,&two,4);
    void *objects[1]={object},*objects_pointer=objects;
    memcpy(snapshot,&one,4);memcpy(snapshot+16,&objects_pointer,8);mx_audio_objects=snapshot;
    unsigned before=destroyed;mx_audio_drain(&head,next_offset);
    assert(destroyed==before+2 && head==first);
    void *next;memcpy(&next,(unsigned char*)first+next_offset,8);assert(next==third);
    memcpy(&next,(unsigned char*)third+next_offset,8);assert(!next);
    // Audio can still consume and modify the queued metadata in this frame.
    ((unsigned char*)first)[8]=0;((unsigned char*)third)[8]=0;
    mx_audio_drain(&head,next_offset);assert(destroyed==before+2);
    // Once the audio snapshot drops the references, both objects are reclaimed.
    memset(snapshot,0,4);mx_audio_drain(&head,next_offset);
    assert(destroyed==before+4 && !head);mx_audio_objects=NULL;
}
int main(void) {
    exercise(40);exercise(24);
    assert(destroyed==8);
    // No active frame preserves the original immediate reclamation behavior.
    void *head=parameter(40,NULL);mx_audio_drain(&head,40);assert(!head && destroyed==9);
    puts("Queued metadata survives live frame references; mixed queues unlink correctly; retired objects are reclaimed without leaks.");
}
