# Meta XR Audio metadata lifetime

FrameBridge automatically handles metadata retirement in the verified AArch64
Meta XR Audio Wwise build (`e1619e7fb839badf0ea5ecc22d9f028d0b17c34d`). It requires
the build ID and exact function prologues; other builds and 32-bit adapters are
left unchanged. No additional patch switch or recipe entry is required.

The SDK queues terminated metadata for deletion, then drains those queues before
reading the current `AkAudioObjects` snapshot. In Batman: Arkham Shadow, that
snapshot can still reference queued metadata during smoke-bomb playback. The
audio thread subsequently clears its dirty flags at offset 8 through the stale
pointer. A hardware watch captured this store in
`OculusEndpointSink::ConsumeObjectExperimentalMetadata`, at library offset
`0x2ced90`. Reallocated input tree nodes occupy the same 48-byte size class;
the stale store corrupts their right pointer and the game crashes later.

`native/adapter/audio_metadata.h` wraps the audio consumption boundary to make
the current snapshot available to metadata cleanup. `audio_metadata_queue.h`
retains queued objects still referenced by that snapshot, then reclaims them
normally when the references disappear. It preserves the SDK's queue mutex,
virtual deleting destructor, audio parameters and processing. There is no timed
quarantine, replacement allocator, audio suppression or diagnostic memory arena.

Synthetic regression tests exercise mixed referenced and unreferenced queue
entries, both metadata types, subsequent retirement, and cleanup without a live
snapshot. On-headset smoke-bomb tests first showed the observed overwrite stopped,
then passed with ordinary allocations and all memory diagnostics removed.

The existing `frame.adapter` install step includes the repair in its packaged
adapter and updates already wrapped APKs. Rebuild the adapters and checksums with
`python native/build.py --only adapter`.
