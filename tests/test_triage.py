from frameport.validate.triage import triage

LOG_OK = """
09-28 17:39:01.000  1000  1000 I ActivityManager: Start proc 1147:com.example.game/u0a55 for activity
09-28 17:39:02.000  1147  1174 I FrameBridge: scale=1.00 foveation_fix=1 controller_fix=1
09-28 17:39:02.100  1147  1174 I FrameBridge: xrCreateInstance result=0
09-28 17:39:03.000  1147  1174 I OVRPortVrApi: Created OpenXR session with the application's GLES context
09-28 17:39:05.000  1147  1174 I FrameBridge: pacing: 72.2 fps, displayTime vs predicted: avg 0.00 ms
"""

LOG_BAD = """
09-28 17:39:01.000  1000  1000 I ActivityManager: Start proc 1150:com.example.game/u0a55 for activity
09-28 17:39:02.000  1150  1170 E AndroidRuntime: java.lang.UnsatisfiedLinkError: dlopen failed: cannot locate symbol "ovr_User_GetLoggedInUser" referenced by "libgame.so"
09-28 17:39:02.000  1150  1170 I GLShim  : SHADER COMPILE FAILED 1: 0:9(1): error: #extension directive is not allowed in the middle of a shader
"""


def test_healthy_log():
    r = triage(LOG_OK, "RUNNING", "com.example.game")
    assert r.verdict == "pass"
    assert r.milestone == "Submitting frames"
    assert r.fps == 72.2


def test_failures_map_to_patches():
    r = triage(LOG_BAD, "EXITED", "com.example.game")
    ids = {f.id for f in r.findings}
    assert {"missing-ovr-symbol", "gl-shader-failed"} <= ids
    assert "frame.ovrstubs" in r.suggestions() and "frame.gl_shim" in r.suggestions()
    assert r.verdict == "fail"


def test_launcher_signature():
    r = triage("lepton: APP_ACTIVITY is empty\n", "NEVER_STARTED")
    assert r.suggestions() == ["frame.launcher"]
