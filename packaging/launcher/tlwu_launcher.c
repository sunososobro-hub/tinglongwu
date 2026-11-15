/* tlwu.exe: starts the app with the Python runtime that sits next to it (#923).
 *
 * Layout (packaging/runtime_layer.py):  tlwu.exe  python3XY.dll  python3XY._pth  python3XY.zip  DLLs\  <packages>\
 * The exe loads python3XY.dll from its own folder and calls Py_Main with `-I -c <BOOT> <user args>`, so
 * `tlwu.exe --smoke` behaves like `python -c BOOT --smoke`. sys.executable is this exe (Python takes it from the
 * process image), BOOT also puts it into sys.argv[0]: the app re-runs itself through both.
 * python3XY._pth pins sys.path and makes Python ignore every PYTHON* environment variable (a stray PYTHONHOME or
 * PYTHONPATH on the user's PC cannot break the app). Three of them are part of how the app talks to its own child
 * processes (tinglongwu/gui/core.py, autoupdate.py, vbcable.py, tlwu_app/setup_gui.py set them and read the child's
 * output as unbuffered UTF-8), so the launcher passes exactly those on: PYTHONUNBUFFERED -> -u, PYTHONUTF8=1 ->
 * -X utf8, PYTHONIOENCODING -> BOOT reconfigures the standard streams.
 * The exe holds no app code and no Python version: it changes only when this file changes.
 * Win32 API only (no Python headers); builds with MinGW-w64 gcc or MSVC cl.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define PATH_CHARS 32768             /* longest Win32 path, in WCHARs, with the terminating NUL */
#define MSG_CHARS 1024               /* error text: short message + the folder (cut off if longer), with NUL */
#define CONSOLE_PIDS 2               /* enough to tell "only this process" from "shared console" */
#define PYTHON_DLL_GLOB L"python3*.dll"
#define PYTHON_DLL_PREFIX L"python3" /* python3<minor>.dll; python3.dll (no digits) is the forwarder, skipped */
#define PYTHON_DLL_PREFIX_CHARS 7    /* wcslen(PYTHON_DLL_PREFIX) */
#define BUNDLED_ENV L"TLWU_BUNDLED"  /* tlwu_app.main.is_bundled() / tinglongwu.gui.autoupdate.is_bundled() */
#define MAX_FIXED_ARGS 7             /* argv[0], -I, -u, -X, utf8, -c, BOOT */
#define EXIT_NO_RUNTIME 97           /* runtime files missing or broken (not a Python exit code the app uses) */
#define ENV_CHARS 8                  /* enough to tell "", "0" and "1" apart */

/* Python source run by `-c`. Kept free of double quotes and backslashes so it is one plain C string per line. */
static const wchar_t BOOT[] =
    L"import os, sys\n"
    L"def _stdio(spec):\n"
    L"    enc, _, err = spec.partition(':')\n"
    L"    for s, e in ((sys.stdin, err), (sys.stdout, err), (sys.stderr, 'backslashreplace')):\n"
    L"        try:\n"
    L"            s.reconfigure(encoding=enc or None, errors=e or 'strict')\n"
    L"        except (AttributeError, LookupError, ValueError):\n"
    L"            pass\n"
    L"if os.environ.get('PYTHONIOENCODING'):\n"
    L"    _stdio(os.environ['PYTHONIOENCODING'])\n"
    L"sys.argv[0] = sys.executable\n"
    L"from tlwu_app.main import main\n"
    L"sys.exit(main())\n";

typedef int (*py_main_fn)(int, wchar_t **);

static wchar_t g_dir[PATH_CHARS];   /* folder of this exe, with a trailing backslash */
static wchar_t g_path[PATH_CHARS];

static int g_interactive;           /* started without arguments (double click / shortcut): errors get a dialog */

static int fail(const wchar_t *what)
{
    wchar_t msg[MSG_CHARS];
    _snwprintf(msg, MSG_CHARS - 1, L"tinglongWu cannot start: %ls\n\nFolder: %ls\nPlease install tinglongWu again.",
               what, g_dir);
    msg[MSG_CHARS - 1] = 0;
    fwprintf(stderr, L"%ls\n", msg);
    if (g_interactive)              /* never a dialog for `tlwu.exe --smoke` and other scripted runs */
        MessageBoxW(NULL, msg, L"tinglongWu", MB_OK | MB_ICONERROR);
    return EXIT_NO_RUNTIME;
}

/* Double-clicked console exe: Windows made a console only for this process; hide it before Python starts
 * (the GUI is the interface). A console shared with cmd / PowerShell is left alone. */
static void hide_own_console(void)
{
    DWORD pids[CONSOLE_PIDS];
    HWND w;
    if (GetConsoleProcessList(pids, CONSOLE_PIDS) > 1)
        return;
    w = GetConsoleWindow();
    if (w != NULL)
        ShowWindow(w, SW_HIDE);
}

/* Minor version of a python3<minor>.dll file name (python312.dll -> 12); -1 for python3.dll and other names. */
static int python_dll_minor(const wchar_t *name)
{
    const wchar_t *p = name + PYTHON_DLL_PREFIX_CHARS;
    int minor = 0, digits = 0;
    if (_wcsnicmp(name, PYTHON_DLL_PREFIX, PYTHON_DLL_PREFIX_CHARS) != 0)
        return -1;
    for (; *p >= L'0' && *p <= L'9' && digits < 4; p++, digits++)
        minor = minor * 10 + (*p - L'0');
    return (digits > 0 && _wcsicmp(p, L".dll") == 0) ? minor : -1;
}

/* Full path of the newest python3<minor>.dll next to the exe -> g_path. An install made over an older one can hold
 * two (the wizard does not delete files); directory order must never pick the Python this folder was not built for. */
static int find_python_dll(void)
{
    WIN32_FIND_DATAW fd;
    HANDLE h;
    int best = -1, minor;
    wchar_t best_name[MAX_PATH];
    if (_snwprintf(g_path, PATH_CHARS - 1, L"%ls%ls", g_dir, PYTHON_DLL_GLOB) < 0)
        return 0;
    g_path[PATH_CHARS - 1] = 0;
    h = FindFirstFileW(g_path, &fd);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    do {
        minor = python_dll_minor(fd.cFileName);    /* cFileName is MAX_PATH WCHARs, NUL-terminated */
        if (minor > best) {
            best = minor;
            wcscpy(best_name, fd.cFileName);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    if (best < 0 || _snwprintf(g_path, PATH_CHARS - 1, L"%ls%ls", g_dir, best_name) < 0)
        return 0;
    g_path[PATH_CHARS - 1] = 0;
    return 1;
}

/* 1 when the environment variable is set to a non-empty value (PYTHONUNBUFFERED semantics). */
static int env_set(const wchar_t *name, wchar_t *value)
{
    DWORD n = GetEnvironmentVariableW(name, value, ENV_CHARS);
    if (n >= ENV_CHARS)             /* longer than the buffer: set, and not "0" / "1" */
        value[0] = 0;
    return n > 0;
}

int wmain(int argc, wchar_t **argv)
{
    HMODULE py;
    py_main_fn py_main;
    wchar_t **av, *slash, value[ENV_CHARS];
    DWORD n;
    int i, k = 0;

    g_interactive = argc <= 1;      /* before the first fail(): a double-clicked exe must show its error */
    n = GetModuleFileNameW(NULL, g_dir, PATH_CHARS);
    if (n == 0 || n >= PATH_CHARS)
        return fail(L"the program path cannot be read");
    slash = wcsrchr(g_dir, L'\\');
    if (slash == NULL)
        return fail(L"the program path has no folder");
    slash[1] = 0;

    if (g_interactive)
        hide_own_console();
    SetEnvironmentVariableW(BUNDLED_ENV, L"1");

    if (!find_python_dll())
        return fail(L"python3*.dll is missing next to tlwu.exe");
    py = LoadLibraryExW(g_path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH); /* its vcruntime140.dll sits beside it */
    if (py == NULL)
        return fail(L"the Python runtime DLL cannot be loaded");
    py_main = (py_main_fn)(void *)GetProcAddress(py, "Py_Main");
    if (py_main == NULL)
        return fail(L"the Python runtime DLL has no Py_Main");

    av = (wchar_t **)HeapAlloc(GetProcessHeap(), 0, sizeof(wchar_t *) * ((size_t)argc + MAX_FIXED_ARGS + 1));
    if (av == NULL)
        return fail(L"out of memory");
    av[k++] = argc > 0 ? argv[0] : g_dir;
    av[k++] = L"-I";
    if (env_set(L"PYTHONUNBUFFERED", value))
        av[k++] = L"-u";
    if (env_set(L"PYTHONUTF8", value) && wcscmp(value, L"1") == 0) {
        av[k++] = L"-X";
        av[k++] = L"utf8";
    }
    av[k++] = L"-c";
    av[k++] = (wchar_t *)BOOT;
    for (i = 1; i < argc; i++)
        av[k++] = argv[i];
    av[k] = NULL;                   /* k <= MAX_FIXED_ARGS + argc - 1: the array holds MAX_FIXED_ARGS + argc + 1 */
    return py_main(k, av);
}
