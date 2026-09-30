/* fp_oculushmd.exe — the PC VR counterpart of overport's patch_oculus_unreal, for Rift games under Proton on the Frame.
 *
 * Unreal's OculusVR plugin (and LibOVR's ovr_Detect) only start when the Windows named event "OculusHMDConnected"
 * exists and is signalled; on a real PC the Oculus service creates it. Without it Unreal never loads OVRPlugin and the
 * game runs as a flat window. Revive hooks OpenEventW for the same purpose, but that relies on Detours patching Wine's
 * kernelbase, which is ARM64EC code under Proton arm64. This helper simply provides the real event:
 *
 *     fp_oculushmd.exe <command line to run>      (e.g. ReviveInjector.exe /openxr Z:\...\Game-Win64-Shipping.exe)
 *
 * It creates the event (manual reset, signalled), runs the command inside a job object and stays alive (keeping the
 * event) until every process in the job has exited — ReviveInjector exits right after starting the game, the game
 * and its children stay in the job. Returns the exit code of the command.
 *
 * Freestanding (no CRT, no Windows SDK headers): built by native/build.py with the NDK's clang + lld-link.
 */
typedef void *HANDLE;
typedef unsigned long DWORD;
typedef int BOOL;
typedef unsigned short WORD;
typedef unsigned short WCHAR;
typedef long long LONGLONG;

typedef struct {
    DWORD cb;
    WCHAR *lpReserved, *lpDesktop, *lpTitle;
    DWORD dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    WORD wShowWindow, cbReserved2;
    unsigned char *lpReserved2;
    HANDLE hStdInput, hStdOutput, hStdError;
} STARTUPINFOW;

typedef struct {
    HANDLE hProcess, hThread;
    DWORD dwProcessId, dwThreadId;
} PROCESS_INFORMATION;

typedef struct {
    LONGLONG TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime;
    DWORD TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses;
} JOBOBJECT_BASIC_ACCOUNTING_INFORMATION;

#define WINAPI __attribute__((ms_abi))
#define IMPORT __declspec(dllimport)
IMPORT HANDLE WINAPI CreateEventW(void *, BOOL, BOOL, const WCHAR *);
IMPORT HANDLE WINAPI CreateJobObjectW(void *, const WCHAR *);
IMPORT BOOL WINAPI AssignProcessToJobObject(HANDLE, HANDLE);
IMPORT BOOL WINAPI QueryInformationJobObject(HANDLE, int, void *, DWORD, DWORD *);
IMPORT BOOL WINAPI CreateProcessW(const WCHAR *, WCHAR *, void *, void *, BOOL, DWORD, void *, const WCHAR *,
                                  STARTUPINFOW *, PROCESS_INFORMATION *);
IMPORT DWORD WINAPI ResumeThread(HANDLE);
IMPORT DWORD WINAPI WaitForSingleObject(HANDLE, DWORD);
IMPORT BOOL WINAPI GetExitCodeProcess(HANDLE, DWORD *);
IMPORT BOOL WINAPI CloseHandle(HANDLE);
IMPORT void WINAPI Sleep(DWORD);
IMPORT WCHAR *WINAPI GetCommandLineW(void);
IMPORT DWORD WINAPI GetLastError(void);
IMPORT HANDLE WINAPI GetStdHandle(DWORD);
IMPORT BOOL WINAPI WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
IMPORT __attribute__((noreturn)) void WINAPI ExitProcess(unsigned);

#define CREATE_SUSPENDED 0x4
#define STD_ERROR_HANDLE ((DWORD)-12)
#define JobObjectBasicAccountingInformation 1
#ifndef EVENT_NAME /* overridable for tests on a PC where the Oculus service owns the real one */
#define EVENT_NAME L"OculusHMDConnected"  /* OVR_HMD_CONNECTED_EVENT_NAME */
#endif
#define GRACE_MS 180000 /* no job tracking: keep the event this long after the command exits (Unreal checks at start) */

static void say(const char *msg) {
    DWORD n = 0, len = 0;
    while (msg[len]) len++;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), msg, len, &n, 0);
}

static void say_error(const char *msg) {  /* msg + " (error N)" */
    char buf[16], *p = buf + sizeof buf;
    DWORD e = GetLastError();
    *--p = 0, *--p = '\n', *--p = ')';
    do *--p = (char)('0' + e % 10); while (e /= 10);
    say(msg);
    say(" (error ");
    say(p);
}

/* The command line after our own program name (quoted or not), without leading blanks. */
static WCHAR *rest_of_command_line(WCHAR *p) {
    if (*p == '"') {
        for (p++; *p && *p != '"'; p++) {}
        if (*p) p++;
    } else {
        while (*p && *p != ' ' && *p != '\t') p++;
    }
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

void start(void) {
    WCHAR *cmd = rest_of_command_line(GetCommandLineW());
    if (!*cmd) {
        say("usage: fp_oculushmd.exe <command line>\n");
        ExitProcess(2);
    }
    HANDLE event = CreateEventW(0, 1, 1, EVENT_NAME);
    if (event)
        say("FramePort oculushmd: OculusHMDConnected event ready\n");
    else
        say_error("FramePort oculushmd: could not create the OculusHMDConnected event");

    static STARTUPINFOW si;  /* static: zeroed without memset */
    static PROCESS_INFORMATION pi;
    si.cb = sizeof si;
    if (!CreateProcessW(0, cmd, 0, 0, 0, CREATE_SUSPENDED, 0, 0, &si, &pi)) {
        say_error("FramePort oculushmd: could not start the command");
        ExitProcess(1);
    }
    HANDLE job = CreateJobObjectW(0, 0);
    BOOL tracked = job && AssignProcessToJobObject(job, pi.hProcess);
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);

    if (tracked) {  /* the game started by the command (and its children) are in the job too */
        say("FramePort oculushmd: waiting for the game to exit\n");
        for (;;) {
            static JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info;
            if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &info, sizeof info, 0)
                || info.ActiveProcesses == 0)
                break;
            Sleep(1000);
        }
    } else {
        say("FramePort oculushmd: no job tracking; keeping the event for 3 minutes\n");
        Sleep(GRACE_MS);
    }
    if (event) CloseHandle(event);
    ExitProcess(code);
}
