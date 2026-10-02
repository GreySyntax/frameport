# Security

FramePort talks to your Steam Frame over SSH with its own key and runs a small agent there; it also downloads tools
and its own updates (checked against the release's SHA-256 sums and, on Windows, its signature).

To report a vulnerability, please open a GitHub security advisory for this repository (Security → Report a
vulnerability) instead of a public issue. Include the FramePort version (`frameport --version`) and how to reproduce
it. Fixes are released as normal updates; FramePort offers them automatically.
