/* tlwu.exe: starts the app with the Python runtime that sits next to it (#923).
 *
 * Layout (packaging/runtime_layer.py):  tlwu.exe  python3XY.dll  python3XY._pth  python3XY.zip  DLLs\  <packages>\
 * The exe loads python3XY.dll from its own folder and calls Py_Main with `-I -c <BOOT> <user args>`, so
 * `tlwu.exe --smoke` behaves like `python -c BOOT --smoke`. sys.executable is this exe (Python takes it from the
 * process image), BOOT also puts it into sys.argv[0]: the app re-runs itself through both.
 * The exe holds no app code and no Python version: it changes only when this file changes.
 * Win32 API only (no Python headers); builds with MinGW-w64 gcc or MSVC cl.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define PATH_CHARS 32768             /* longest Win32 path, in WCHARs, with the terminating NUL */
#define PYTHON_DLL_GLOB L"python3*.dll"
#define PYTHON_STABLE_ABI_DLL L"python3.dll" /* forwarder DLL: has no Py_Main of its own, skipped */
#define BUNDLED_ENV L"TLWU_BUNDLED"  /* tlwu_app.main.is_bundled() / tinglongwu.gui.autoupdate.is_bundled() */
#define FIXED_ARGS 4                 /* argv[0], -I, -c, BOOT */
#define EXIT_NO_RUNTIME 97           /* runtime files missing or broken (not a Python exit code the app uses) */

static const wchar_t BOOT[] =
    L"import sys; sys.argv[0] = sys.executable; from tlwu_app.main import main; sys.exit(main())";

typedef int (*py_main_fn)(int, wchar_t **);

static wchar_t g_dir[PATH_CHARS];   /* folder of this exe, with a trailing backslash */
static wchar_t g_path[PATH_CHARS];

static int g_interactive;           /* started without arguments (double click / shortcut): errors get a dialog */

static int fail(const wchar_t *what)
{
    wchar_t msg[1024];
    _snwprintf(msg, 1023, L"tinglongWu cannot start: %ls\n\nFolder: %ls\nPlease install tinglongWu again.",
               what, g_dir);
    msg[1023] = 0;
    fwprintf(stderr, L"%ls\n", msg);
    if (g_interactive)              /* never a dialog for `tlwu.exe --smoke` and other scripted runs */
        MessageBoxW(NULL, msg, L"tinglongWu", MB_OK | MB_ICONERROR);
    return EXIT_NO_RUNTIME;
}

/* Double-clicked console exe: Windows made a console only for this process; hide it before Python starts
 * (the GUI is the interface). A console shared with cmd / PowerShell is left alone. */
static void hide_own_console(void)
{
    DWORD pids[2];
    HWND w;
    if (GetConsoleProcessList(pids, 2) > 1)
        return;
    w = GetConsoleWindow();
    if (w != NULL)
        ShowWindow(w, SW_HIDE);
}

static int find_python_dll(void)
{
    WIN32_FIND_DATAW fd;
    HANDLE h;
    int found = 0;
    if (_snwprintf(g_path, PATH_CHARS - 1, L"%ls%ls", g_dir, PYTHON_DLL_GLOB) < 0)
        return 0;
    h = FindFirstFileW(g_path, &fd);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    do {
        if (_wcsicmp(fd.cFileName, PYTHON_STABLE_ABI_DLL) != 0 &&
            _snwprintf(g_path, PATH_CHARS - 1, L"%ls%ls", g_dir, fd.cFileName) >= 0) {
            found = 1;
            break;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return found;
}

int wmain(int argc, wchar_t **argv)
{
    HMODULE py;
    py_main_fn py_main;
    wchar_t **av, *slash;
    DWORD n;
    int i;

    n = GetModuleFileNameW(NULL, g_dir, PATH_CHARS);
    if (n == 0 || n >= PATH_CHARS)
        return fail(L"the program path cannot be read");
    slash = wcsrchr(g_dir, L'\\');
    if (slash == NULL)
        return fail(L"the program path has no folder");
    slash[1] = 0;

    g_interactive = argc <= 1;
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

    av = (wchar_t **)HeapAlloc(GetProcessHeap(), 0, sizeof(wchar_t *) * ((size_t)argc + FIXED_ARGS + 1));
    if (av == NULL)
        return fail(L"out of memory");
    av[0] = argc > 0 ? argv[0] : g_dir;
    av[1] = L"-I";
    av[2] = L"-c";
    av[3] = (wchar_t *)BOOT;
    for (i = 1; i < argc; i++)
        av[FIXED_ARGS + i - 1] = argv[i];
    av[FIXED_ARGS + (argc > 0 ? argc - 1 : 0)] = NULL;
    return py_main(FIXED_ARGS + (argc > 0 ? argc - 1 : 0), av);
}
