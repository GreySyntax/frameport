/* fp_vrsettings.exe — read/write SteamVR settings through OpenVR's IVRSettings (Windows x64, run on the PC).
 *
 * SteamVR keeps its settings in memory and rewrites steamvr.vrsettings on exit, so editing the file while SteamVR runs
 * is lost. This helper connects as a utility application (no rendering, no hardware), which works whether SteamVR is
 * running or not, and changes settings the supported way; SteamVR applies and persists them.
 *
 *     fp_vrsettings.exe <openvr_api.dll> (get|set) <section> <key> <b|i|f|s> [value] [(get|set) ...]
 *
 * Prints one line per operation: "ok <section> <key> <value>" or "error <section> <key> <SteamVR error>".
 * Exit code 0 when every operation succeeded, 1 on an error, 2 on bad usage / OpenVR init failure.
 * Freestanding (no CRT, no Windows SDK headers): built by native/build.py with the NDK's clang + lld-link.
 */
typedef void *HANDLE;
typedef unsigned long DWORD;
typedef int BOOL;

#define WINAPI __attribute__((ms_abi))
#define IMPORT __declspec(dllimport)
IMPORT HANDLE WINAPI LoadLibraryA(const char *);
IMPORT void *WINAPI GetProcAddress(HANDLE, const char *);
IMPORT char *WINAPI GetCommandLineA(void);
IMPORT HANDLE WINAPI GetStdHandle(DWORD);
IMPORT BOOL WINAPI WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
IMPORT __attribute__((noreturn)) void WINAPI ExitProcess(unsigned);

#define STD_OUTPUT_HANDLE ((DWORD)-11)
#define VRApplication_Utility 4
int _fltused = 1; /* MSVC ABI marker the compiler emits for float code (normally from the CRT) */

/* openvr_capi.h: struct VR_IVRSettings_FnTable (IVRSettings_003) */
typedef int EVRSettingsError;
typedef struct {
    char *(*GetSettingsErrorNameFromEnum)(EVRSettingsError);
    void (*SetBool)(const char *, const char *, int, EVRSettingsError *);
    void (*SetInt32)(const char *, const char *, int, EVRSettingsError *);
    void (*SetFloat)(const char *, const char *, float, EVRSettingsError *);
    void (*SetString)(const char *, const char *, const char *, EVRSettingsError *);
    int (*GetBool)(const char *, const char *, EVRSettingsError *);
    int (*GetInt32)(const char *, const char *, EVRSettingsError *);
    float (*GetFloat)(const char *, const char *, EVRSettingsError *);
    void (*GetString)(const char *, const char *, char *, unsigned, EVRSettingsError *);
    void (*RemoveSection)(const char *, EVRSettingsError *);
    void (*RemoveKeyInSection)(const char *, const char *, EVRSettingsError *);
} SettingsFnTable;

typedef unsigned (*PFN_InitInternal2)(int *, int, const char *);
typedef void (*PFN_ShutdownInternal)(void);
typedef void *(*PFN_GetGenericInterface)(const char *, int *);

static void out(const char *s) {
    DWORD n = 0, len = 0;
    while (s[len]) len++;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), s, len, &n, 0);
}

static void out_int(long long v) {
    char buf[24], *p = buf + sizeof buf;
    int neg = v < 0;
    unsigned long long u = neg ? (unsigned long long)-v : (unsigned long long)v;
    *--p = 0;
    do *--p = (char)('0' + u % 10); while (u /= 10);
    if (neg) *--p = '-';
    out(p);
}

static void out_float(float f) {  /* 3 decimals is plenty for refresh rates / scales */
    long long milli = (long long)(f * 1000.0f + (f < 0 ? -0.5f : 0.5f));
    if (milli < 0) { out("-"); milli = -milli; }
    out_int(milli / 1000);
    char frac[5] = {'.', (char)('0' + milli / 100 % 10), (char)('0' + milli / 10 % 10), (char)('0' + milli % 10), 0};
    out(frac);
}

static long long parse_int(const char *s) {
    long long v = 0;
    int neg = *s == '-';
    if (neg || *s == '+') s++;
    while (*s >= '0' && *s <= '9') v = v * 10 + (*s++ - '0');
    return neg ? -v : v;
}

static float parse_float(const char *s) {
    int neg = *s == '-';
    if (neg || *s == '+') s++;
    float v = 0, scale = 1;
    while (*s >= '0' && *s <= '9') v = v * 10 + (float)(*s++ - '0');
    if (*s == '.')
        for (s++; *s >= '0' && *s <= '9'; s++) v += (float)(*s - '0') * (scale /= 10);
    return neg ? -v : v;
}

/* Split the command line in place into argv (double quotes group; no escapes needed for our arguments). */
static int split(char *p, char **argv, int max) {
    int n = 0;
    while (*p && n < max) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;
        if (*p == '"') {
            argv[n++] = ++p;
            while (*p && *p != '"') p++;
        } else {
            argv[n++] = p;
            while (*p && *p != ' ' && *p != '\t') p++;
        }
        if (*p) *p++ = 0;
    }
    return n;
}

static int streq(const char *a, const char *b) {
    while (*a && *a == *b) a++, b++;
    return *a == *b;
}

void start(void) {
    static char *argv[128];
    int argc = split(GetCommandLineA(), argv, 128);
    if (argc < 6) {
        out("usage: fp_vrsettings.exe <openvr_api.dll> (get|set) <section> <key> <b|i|f|s> [value] ...\n");
        ExitProcess(2);
    }
    HANDLE lib = LoadLibraryA(argv[1]);
    PFN_InitInternal2 init = lib ? (PFN_InitInternal2)GetProcAddress(lib, "VR_InitInternal2") : 0;
    PFN_ShutdownInternal shutdown = lib ? (PFN_ShutdownInternal)GetProcAddress(lib, "VR_ShutdownInternal") : 0;
    PFN_GetGenericInterface iface = lib ? (PFN_GetGenericInterface)GetProcAddress(lib, "VR_GetGenericInterface") : 0;
    if (!init || !shutdown || !iface) {
        out("error could not load openvr_api.dll\n");
        ExitProcess(2);
    }
    int err = 0;
    init(&err, VRApplication_Utility, 0);
    if (err) {
        out("error VR_Init failed: ");
        out_int(err);
        out("\n");
        ExitProcess(2);
    }
    SettingsFnTable *s = (SettingsFnTable *)iface("FnTable:IVRSettings_003", &err);
    if (!s || err) {
        out("error no IVRSettings_003\n");
        shutdown();
        ExitProcess(2);
    }
    int failed = 0;
    for (int i = 2; i + 3 < argc;) {
        const char *op = argv[i], *section = argv[i + 1], *key = argv[i + 2], *type = argv[i + 3];
        int set = streq(op, "set");
        if (set && i + 4 >= argc) { out("error missing value\n"); failed = 1; break; }
        const char *value = set ? argv[i + 4] : 0;
        i += set ? 5 : 4;
        EVRSettingsError e = 0;
        char buf[512];
        buf[0] = 0;
        if (set) {
            if (type[0] == 'b') s->SetBool(section, key, streq(value, "true") || streq(value, "1"), &e);
            else if (type[0] == 'i') s->SetInt32(section, key, (int)parse_int(value), &e);
            else if (type[0] == 'f') s->SetFloat(section, key, parse_float(value), &e);
            else s->SetString(section, key, value, &e);
        }
        /* read back (also the result of "get") */
        EVRSettingsError ge = 0;
        long long iv = 0;
        float fv = 0;
        if (type[0] == 'b') iv = s->GetBool(section, key, &ge);
        else if (type[0] == 'i') iv = s->GetInt32(section, key, &ge);
        else if (type[0] == 'f') fv = s->GetFloat(section, key, &ge);
        else s->GetString(section, key, buf, sizeof buf, &ge);
        EVRSettingsError report = e ? e : ge;
        out(report ? "error " : "ok ");
        out(section);
        out(" ");
        out(key);
        out(" ");
        if (report) {
            const char *name = s->GetSettingsErrorNameFromEnum(report);
            out(name ? name : "?");
            if (set || report != 5 /* UnsetSettingHasNoDefault: "get" of an unset key */) failed = 1;
        } else if (type[0] == 'f') {
            out_float(fv);
        } else if (type[0] == 's') {
            out(buf);
        } else {
            out_int(iv);
        }
        out("\n");
    }
    shutdown();
    ExitProcess(failed);
}
