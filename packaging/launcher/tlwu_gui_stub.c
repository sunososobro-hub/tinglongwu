/* tlwu-gui.exe: the exe the Start menu / desktop shortcuts and the installer's [Run] entry start (#1524).
 *
 * It is a GUI-subsystem program (no console of its own) that starts tlwu.exe from the same folder with
 * CREATE_NO_WINDOW and exits. tlwu.exe stays a console app for the CLI (`tlwu.exe --selftest`, `setup`), and it is
 * still the process that runs Python, so sys.executable / is_bundled() / the app's child processes are unchanged.
 * Why not hide the console from inside tlwu.exe: on Windows 11 Windows Terminal is the default console host and
 * ShowWindow(GetConsoleWindow()) does not remove its tab (an empty Terminal tab stayed next to the GUI).
 * Win32 API only; builds with MinGW-w64 gcc (-mwindows -municode) or MSVC cl (/SUBSYSTEM:WINDOWS).
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#define PATH_CHARS 32768
#define TARGET_EXE L"tlwu.exe"

/* The command line after the first token (the program name, quoted or not); "" when there are no arguments. */
static const wchar_t *args_of(const wchar_t *cmd)
{
    int quoted = 0;
    while (*cmd == L' ' || *cmd == L'\t')
        cmd++;
    for (; *cmd; cmd++) {
        if (*cmd == L'"')
            quoted = !quoted;
        else if (!quoted && (*cmd == L' ' || *cmd == L'\t'))
            break;
    }
    while (*cmd == L' ' || *cmd == L'\t')
        cmd++;
    return cmd;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmdline, int show)
{
    static wchar_t dir[PATH_CHARS], exe[PATH_CHARS], cmd[PATH_CHARS];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    wchar_t *slash;
    DWORD n;
    (void)inst; (void)prev; (void)cmdline; (void)show;

    n = GetModuleFileNameW(NULL, dir, PATH_CHARS);
    slash = (n > 0 && n < PATH_CHARS) ? wcsrchr(dir, L'\\') : NULL;
    if (slash == NULL) {
        MessageBoxW(NULL, L"tinglongWu cannot start: the program path cannot be read.", L"tinglongWu",
                    MB_OK | MB_ICONERROR);
        return 1;
    }
    slash[1] = 0;
    if (_snwprintf(exe, PATH_CHARS - 1, L"%ls%ls", dir, TARGET_EXE) < 0 ||
        _snwprintf(cmd, PATH_CHARS - 1, L"\"%ls\" %ls", exe, args_of(GetCommandLineW())) < 0) {
        MessageBoxW(NULL, L"tinglongWu cannot start: the program path is too long.", L"tinglongWu",
                    MB_OK | MB_ICONERROR);
        return 1;
    }
    exe[PATH_CHARS - 1] = 0;
    cmd[PATH_CHARS - 1] = 0;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    if (!CreateProcessW(exe, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &si, &pi)) {
        wchar_t msg[1024];
        _snwprintf(msg, 1023, L"tinglongWu cannot start: %ls is missing or cannot run (error %lu).\n\nFolder: %ls\n"
                   L"Please install tinglongWu again.", TARGET_EXE, (unsigned long)GetLastError(), dir);
        msg[1023] = 0;
        MessageBoxW(NULL, msg, L"tinglongWu", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
