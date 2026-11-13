; tinglongWu Windows installer wizard (Inno Setup 6, per-user, no admin). Build: python installer\build_installer.py
; Next -> Installing (install.ps1 runs hidden, its [n/7] steps drive the progress bar, output in install.log)
; -> Finish. Start menu + desktop shortcuts with the app icon; uninstall in "Apps / Add or remove programs"
; asks whether to keep the downloaded models. No elevation beyond -ExecutionPolicy Bypass for install.ps1.
; Defines passed by build_installer.py: StageDir, IconFile, OutDir, AppVersion.

#define AppName "tinglongWu"
#define AppTitle "聽攏有 tinglongWu"
#define TalkTitle "聽攏有 說話模式"

[Setup]
AppId={{22BD466E-AB81-4A04-8523-A363FB79F081}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppTitle} {#AppVersion}
AppPublisher=tinglongWu
AppPublisherURL=https://github.com/sunososobro-hub/tinglongwu
AppSupportURL=https://github.com/sunososobro-hub/tinglongwu/issues
DefaultDirName={localappdata}\Programs\tinglongWu
DefaultGroupName={#AppTitle}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#StageDir}\LICENSE
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\tinglongwu.ico
UninstallDisplayName={#AppTitle}
OutputDir={#OutDir}
OutputBaseFilename=tinglongwu-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Desktop shortcuts / 桌面捷徑"
Name: "cpu"; Description: "Force the CPU path (no NVIDIA GPU use) / 強制 CPU 路線"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "tinglongwu.ico"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"; Tasks: desktopicon
Name: "{autoprograms}\{#AppTitle} - repair install (重新安裝)"; Filename: "{app}\install.bat"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"

[Run]
Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; Description: "Start {#AppTitle} / 啟動"; Flags: postinstall nowait skipifsilent skipifdoesntexist
Filename: "{app}\install.log"; Description: "Show install.log / 顯示安裝紀錄"; Flags: postinstall shellexec skipifsilent unchecked skipifdoesntexist

[UninstallDelete]
; talk-mode shortcuts are made in code only when tlwu-talk.ps1 exists (#124)
Type: files; Name: "{autoprograms}\{#TalkTitle}.lnk"
Type: files; Name: "{autodesktop}\{#TalkTitle}.lnk"

[Code]
const
  Steps = 7;
var
  ProgressPage: TOutputProgressWizardPage;
  InstallRC: Integer;
  KeepModels: Boolean;
  CpuTaskPreset: Boolean;

{ #600: NVIDIA driver present? nvcuda.dll / nvml.dll in System32, or nvidia-smi exits 0 }
function HasNvidiaDriver: Boolean;
var
  RC: Integer;
begin
  Result := FileExists(ExpandConstant('{sys}\nvcuda.dll')) or FileExists(ExpandConstant('{sys}\nvml.dll'));
  if not Result then
    Result := Exec(ExpandConstant('{cmd}'), '/C nvidia-smi >NUL 2>&1', '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 0);
  Log('NVIDIA driver detected: ' + IntToStr(Ord(Result)));
end;

{ #600: no NVIDIA driver -> pre-tick "Force the CPU path"; the box stays visible as an advanced override }
procedure PresetCpuTask;
var
  I: Integer;
begin
  if CpuTaskPreset then exit;
  CpuTaskPreset := True;
  if HasNvidiaDriver then exit;
  for I := 0 to WizardForm.TasksList.Items.Count - 1 do
    if Pos('Force the CPU path', WizardForm.TasksList.ItemCaption[I]) = 1 then
      WizardForm.TasksList.Checked[I] := True;
end;

procedure InitializeWizard;
begin
  ProgressPage := CreateOutputProgressPage('Installing Python packages and models / 安裝套件與模型',
    'This can take 10-40 minutes (about 3 GB of packages plus models). 需要 10-40 分鐘，請保持網路連線。');
  InstallRC := -1;
end;

{ highest "[n/7]" step that install.ps1 has printed so far }
function LastStep(const LogFile: String): Integer;
var
  Lines: TArrayOfString;
  I, N: Integer;
begin
  Result := 0;   { the caller keeps the highest value seen: the log can be briefly locked by the writer }
  if not LoadStringsFromFile(LogFile, Lines) then exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
    if (Pos('[', Lines[I]) = 1) and (Pos('/' + IntToStr(Steps) + ']', Lines[I]) = 3) then begin
      N := StrToIntDef(Copy(Lines[I], 2, 1), 0);
      if N > Result then Result := N;
      ProgressPage.SetText(Lines[I], LogFile);
    end;
end;

procedure RunInstall;
var
  Params, LogFile, DoneFile: String;
  RC, Waited, Seen: Integer;
  Done: AnsiString;
begin
  LogFile := ExpandConstant('{app}\install.log');
  DoneFile := ExpandConstant('{app}\install.done');
  DeleteFile(DoneFile);
  Params := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ExpandConstant('{app}\installer\run_install.ps1') + '"';
  if WizardIsTaskSelected('cpu') then Params := Params + ' -Cpu';
  ProgressPage.SetText('Starting install.ps1 ...', LogFile);
  ProgressPage.SetProgress(0, Steps);
  ProgressPage.Show;
  try
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, ExpandConstant('{app}'),
                SW_HIDE, ewNoWait, RC) then begin
      Log('could not start PowerShell: ' + SysErrorMessage(RC));
      exit;
    end;
    Waited := 0;
    Seen := 0;
    while (not FileExists(DoneFile)) and (Waited < 6 * 3600 * 2) do begin   { 6 h cap, 500 ms per tick }
      RC := LastStep(LogFile);
      if RC > Seen then Seen := RC;
      ProgressPage.SetProgress(Seen, Steps);
      Sleep(500);
      Waited := Waited + 1;
    end;
    if LoadStringFromFile(DoneFile, Done) then InstallRC := StrToIntDef(Trim(String(Done)), 1);
  finally
    ProgressPage.Hide;
  end;
end;

procedure MakeTalkShortcuts;   { #124 adds tlwu-talk.ps1; until then nothing is created }
var
  Script, Args: String;
begin
  Script := ExpandConstant('{app}\tlwu-talk.ps1');
  if not FileExists(Script) then exit;
  Args := '-NoExit -ExecutionPolicy Bypass -File "' + Script + '"';
  CreateShellLink(ExpandConstant('{autoprograms}\{#TalkTitle}.lnk'), '{#TalkTitle}',
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Args, ExpandConstant('{app}'),
    ExpandConstant('{app}\tinglongwu.ico'), 0, SW_SHOWNORMAL);
  if WizardIsTaskSelected('desktopicon') then
    CreateShellLink(ExpandConstant('{autodesktop}\{#TalkTitle}.lnk'), '{#TalkTitle}',
      ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Args, ExpandConstant('{app}'),
      ExpandConstant('{app}\tinglongwu.ico'), 0, SW_SHOWNORMAL);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RunInstall;
    MakeTalkShortcuts;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then PresetCpuTask;
  if (CurPageID = wpFinished) and (InstallRC <> 0) then
    WizardForm.FinishedLabel.Caption :=
      'install.ps1 did not finish cleanly (exit code ' + IntToStr(InstallRC) + '). See ' +
      ExpandConstant('{app}\install.log') + ', fix the cause (usually the internet connection) and run "' +
      '{#AppTitle} - repair install" from the Start menu.' + #13#10 + #13#10 +
      '安裝沒有完成，請看 install.log，確認網路後從開始選單執行「重新安裝」。';
end;

{ uninstall: optionally keep models\, then remove what install.ps1 created (.venv, logs, caches) }
procedure DeleteLeftovers(const Dir: String);
var
  F: TFindRec;
begin
  if FindFirst(Dir + '\*', F) then
    try
      repeat
        if (F.Name <> '.') and (F.Name <> '..') and (Pos('unins', Lowercase(F.Name)) <> 1) and not (KeepModels and (CompareText(F.Name, 'models') = 0)) then begin
          if F.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then DelTree(Dir + '\' + F.Name, True, True, True)
          else DeleteFile(Dir + '\' + F.Name);
        end;
      until not FindNext(F);
    finally
      FindClose(F);
    end;
  if not KeepModels then RemoveDir(Dir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KeepModels := DirExists(ExpandConstant('{app}\models')) and (UninstallSilent or
      (MsgBox('Keep the downloaded models (several GB) for a later reinstall?' + #13#10 +
              '保留已下載的模型（數 GB），以便日後重新安裝？', mbConfirmation, MB_YESNO) = IDYES));
  if CurUninstallStep = usPostUninstall then DeleteLeftovers(ExpandConstant('{app}'));
end;
