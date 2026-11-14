; tinglongWu Windows installer wizard (Inno Setup 6, per-user, no admin). Build: python installer\build_installer.py
; Next -> Installing (install.ps1 runs hidden, its [n/7] steps drive the progress bar, output in install.log)
; -> Finish. Start menu + desktop shortcuts with the app icon; uninstall in "Apps / Add or remove programs"
; asks whether to keep the downloaded models. No elevation beyond -ExecutionPolicy Bypass for install.ps1.
; Defines passed by build_installer.py: StageDir, IconFile, OutDir, AppVersion, ExtraDiskBytes.

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
ExtraDiskSpaceRequired={#ExtraDiskBytes}
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
Name: "autoupdate"; Description: "Update automatically (checks GitHub once a day) / 自動更新（每天檢查一次 GitHub）"

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
var
  Steps: Integer;
  ProgressPage: TOutputProgressWizardPage;
  InstallRC: Integer;
  KeepModels: Boolean;
  CpuTaskPreset: Boolean;
  LangPage: TInputOptionWizardPage;

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

{ #672: primary language id of the OS UI language -> index into the pack list below (zh, en, ja, ko, ru); #754: $12 Korean }
function LocaleLangIndex: Integer;
begin
  case GetUILanguage and $3FF of
    $04: Result := 0;
    $11: Result := 2;
    $12: Result := 3;
    $19: Result := 4;
  else Result := 1;
  end;
end;

{ #672: approximate download MB of the ticked packs; mirrors tinglongwu_setup/packs.py (ja / ko = Hy-MT2 1100, ru = Hy-MT2 + Piper 60, shared once) }
function PackSizeMB: Integer;
begin
  Result := 0;
  if LangPage.Values[2] or LangPage.Values[3] or LangPage.Values[4] then Result := 1100;
  if LangPage.Values[4] then Result := Result + 60;
end;

{ comma list for tlwu-setup --packs; zh+en when nothing is ticked }
function SelectedPacks: String;
var
  I: Integer;
  Codes: array[0..4] of String;
begin
  Codes[0] := 'zh'; Codes[1] := 'en'; Codes[2] := 'ja'; Codes[3] := 'ko'; Codes[4] := 'ru';
  Result := '';
  for I := 0 to 4 do
    if LangPage.Values[I] then begin
      if Result <> '' then Result := Result + ',';
      Result := Result + Codes[I];
    end;
  if Result = '' then Result := 'zh,en';
end;

procedure InitializeWizard;
begin
  LangPage := CreateInputOptionPage(wpSelectTasks, 'Which language do you want to understand? / 你想聽懂哪些語言？',
    'Only the chosen language packs are downloaded. 只會下載勾選的語言包。', '', False, False);
  LangPage.Add('中文 Chinese (zh)');
  LangPage.Add('English (en)');
  LangPage.Add('日本語 Japanese (ja, about 1.1 GB)');
  LangPage.Add('한국어 Korean (ko, about 1.1 GB)');
  LangPage.Add('Русский Russian (ru, about 1.2 GB)');
  LangPage.Values[LocaleLangIndex] := True;
  if LocaleLangIndex <> 0 then LangPage.Values[0] := True;   { zh is the output language of listen mode }
  LangPage.Add('GPU pack for NVIDIA (CUDA libraries) / NVIDIA 顯示卡加速包');
  LangPage.Values[5] := HasNvidiaDriver;
  ProgressPage := CreateOutputProgressPage('Installing Python packages and models / 安裝套件與模型',
    'This can take 10-40 minutes. 需要 10-40 分鐘，請保持網路連線。');
  InstallRC := -1;
end;

{ highest "[n/N]" step that install.ps1 has printed so far; N is read from the line itself (never hard-coded, #700) }
function LastStep(const LogFile: String): Integer;
var
  Lines: TArrayOfString;
  I, N, Total, Slash, Close: Integer;
begin
  Result := 0;   { the caller keeps the highest value seen: the log can be briefly locked by the writer }
  if not LoadStringsFromFile(LogFile, Lines) then exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos('[', Lines[I]) = 1 then begin
      Slash := Pos('/', Lines[I]);
      Close := Pos(']', Lines[I]);
      if (Slash > 2) and (Close > Slash + 1) then begin
        N := StrToIntDef(Copy(Lines[I], 2, Slash - 2), 0);
        Total := StrToIntDef(Copy(Lines[I], Slash + 1, Close - Slash - 1), 0);
        if (N > 0) and (Total >= N) then begin
          Steps := Total;
          if N > Result then Result := N;
          ProgressPage.SetText(Lines[I], LogFile);
        end;
      end;
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
  if WizardIsTaskSelected('cpu') or (not LangPage.Values[5]) then Params := Params + ' -Cpu';
  Params := Params + ' -Packs ' + SelectedPacks;
  ProgressPage.Description := 'This can take 10-40 minutes (about ' + IntToStr(PackSizeMB) + ' MB of language packs plus the core models and Python packages). 需要 10-40 分鐘，請保持網路連線。';
  ProgressPage.SetText('Starting install.ps1 ...', LogFile);
  Steps := 8;   { replaced by N from the first "[n/N]" log line }
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

{ #698: set KEY=VALUE in the user config (%APPDATA%\tinglongwu\config, read by tinglongwu.userdirs), keeping other keys }
procedure SetConfigValue(const Key, Value: String);
var
  CfgPath, Buf: String;
  Lines: TArrayOfString;
  I: Integer;
  Found: Boolean;
begin
  CfgPath := ExpandConstant('{userappdata}\tinglongwu\config');
  ForceDirectories(ExtractFileDir(CfgPath));
  Buf := '';
  Found := False;
  if LoadStringsFromFile(CfgPath, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do begin
      if Pos(Key + '=', Trim(Lines[I])) = 1 then begin
        Lines[I] := Key + '=' + Value;
        Found := True;
      end;
      if Trim(Lines[I]) <> '' then Buf := Buf + Lines[I] + #13#10;
    end;
  if not Found then Buf := Buf + Key + '=' + Value + #13#10;
  SaveStringToFile(CfgPath, Buf, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    { #698: the wizard is where the user chooses; a silent self-update keeps the existing choice }
    if not WizardSilent then
      if WizardIsTaskSelected('autoupdate') then SetConfigValue('auto_update', '1')
      else SetConfigValue('auto_update', '0');
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
