#!/usr/bin/env python3
"""Generate the adapter's tail-call forwarders for every export of overport's libopenxr_loader_generic.so that
frame_adapter.c does not hook: forwarders.S (arm64), forwarders_arm32.S and forwarders.inc (C declarations).

A function counts as hooked when frame_adapter.c defines it (`XRAPI_CALL xrName(` at top level). Regenerate after
adding a hook, or after overport's loader gains exports (update generic_exports.txt from `llvm-nm -D --defined-only`).
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def hooked() -> set[str]:
    src = (HERE / "frame_adapter.c").read_text()
    return set(re.findall(r"^\s*(?:__attribute__\(\([^)]*\)\)\s*)?(?:XRAPI_ATTR\s+)?XrResult\s+XRAPI_CALL\s+(xr\w+)\s*\(", src, re.M))


def main() -> int:
    exports = [l.strip() for l in (HERE / "generic_exports.txt").read_text().split() if l.strip().startswith("xr")]
    fwd = [e for e in exports if e not in hooked()]
    a64 = ["// Generated: register-preserving tail-call forwarders (see frame_adapter.c).", "    .text"]
    a32 = ["// Generated: ARM32 register-preserving tail-call forwarders (see frame_adapter.c).", "    .syntax unified",
           "    .arm", "    .text"]
    inc = ["// Generated from overport 3.4.3 libopenxr_loader_generic.so exports minus hooked functions."]
    for f in fwd:
        a64 += ["    .p2align 2", f"    .globl {f}", f"    .type {f}, %function", f"{f}:", f"    adrp x16, fwd_{f}",
                f"    ldr x16, [x16, :lo12:fwd_{f}]", "    br x16", f"    .size {f}, .-{f}", "    .data", "    .p2align 3",
                f"    .globl fwd_{f}", f"    .hidden fwd_{f}", f"fwd_{f}:", "    .quad frame_unsupported", "    .text"]
        a32 += ["    .p2align 2", f"    .globl {f}", f"    .type {f}, %function", f"{f}:", "    ldr ip, 1f",
                "2:  add ip, pc, ip", "    ldr ip, [ip]", "    bx ip", f"1:  .word fwd_{f} - (2b + 8)", f"    .size {f}, .-{f}",
                "    .data", "    .p2align 2", f"    .globl fwd_{f}", f"    .hidden fwd_{f}", f"fwd_{f}:",
                "    .word frame_unsupported", "    .text"]
        inc.append(f"FORWARD({f})")
    if fwd:  # no dangling .text before the note section
        a64.pop()
        a32.pop()
    a64.append('    .section .note.GNU-stack,"",%progbits')
    a32.append('    .section .note.GNU-stack,"",%progbits')
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    (out / "forwarders.S").write_text("\n".join(a64) + "\n")
    (out / "forwarders_arm32.S").write_text("\n".join(a32) + "\n")
    (out / "forwarders.inc").write_text("\n".join(inc) + "\n")
    print(f"{len(fwd)} forwarders ({len(exports) - len(fwd)} hooked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
