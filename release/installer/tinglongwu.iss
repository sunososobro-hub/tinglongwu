; tinglongWu Windows installer wizard (Inno Setup 6, per-user, no admin). Build: python release\installer\build_installer.py
; Next -> Installing (install.ps1 runs hidden, its [n/7] steps drive the progress bar, output in install.log)
; -> Finish. Start menu + desktop shortcuts with the app icon; uninstall in "Apps / Add or remove programs"
; asks whether to keep the downloaded models. No elevation beyond -ExecutionPolicy Bypass for install.ps1.
; Defines passed by build_installer.py: StageDir, IconFile, OutDir, AppVersion, ExtraDiskBytes; CoreLayout (--core-dir,
; #767): the stage is the prebuilt core folder, shortcuts start {app}\tlwu-gui.exe (#1524) and install.ps1 is not run;
; DefaultListenMB (#771, core only): download size of `tlwu.exe setup --default-listen --defer-mt` (Whisper only; the app fetches Hy-MT2, #1359), which the core wizard runs
; instead (no language page, no pip).

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
; #1002: the first wizard page asks for the language (default = the Windows UI language); every page follows it
ShowLanguageDialog=yes
UsePreviousLanguage=no

[Languages]
; #1002: Chinese (Traditional / Simplified) are Inno Setup's unofficial translations, vendored from
; jrsoftware/issrc tag is-6_7_4 (Files/Languages/Unofficial, Inno Setup license: LICENSE-InnoSetup.txt).
; Default.isl is loaded first so a message the translation lacks falls back to English instead of failing the build.
; Order matters: a Windows language with no exact match (zh-HK, zh-MO, zh-SG) takes the first entry of its primary
; language, so Traditional Chinese comes before Simplified.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesetrad"; MessagesFile: "compiler:Default.isl,Languages\ChineseTraditional.isl"
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl,Languages\ChineseSimplified.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
; #1002: every text the wizard shows comes from here, one language per page (no "English / 中文" pairs);
; %1, %2, %3 are filled in by FmtMessage; source-only / core-only texts sit in the #ifndef / #ifdef CoreLayout blocks
english.TaskDesktop=Desktop shortcuts
english.TaskAutoUpdate=Update automatically (checks GitHub once a day)
english.RunStart=Start {#AppTitle}
english.RunLog=Show install.log
english.SizeAbout=about %1 GB
english.Elapsed=elapsed %1
english.StillWorking=still working, slow network?
english.KeepModels=Keep the downloaded models (several GB) for a later reinstall?
chinesetrad.TaskDesktop=建立桌面捷徑
chinesetrad.TaskAutoUpdate=自動更新（每天檢查 GitHub 一次）
chinesetrad.RunStart=啟動 {#AppTitle}
chinesetrad.RunLog=顯示 install.log
chinesetrad.SizeAbout=約 %1 GB
chinesetrad.Elapsed=已用時間 %1
chinesetrad.StillWorking=仍在進行，網路是否太慢？
chinesetrad.KeepModels=保留已下載的模型（數 GB），以便日後重新安裝？
chinesesimp.TaskDesktop=创建桌面快捷方式
chinesesimp.TaskAutoUpdate=自动更新（每天检查 GitHub 一次）
chinesesimp.RunStart=启动 {#AppTitle}
chinesesimp.RunLog=显示 install.log
chinesesimp.SizeAbout=约 %1 GB
chinesesimp.Elapsed=已用时间 %1
chinesesimp.StillWorking=仍在进行，网络是否太慢？
chinesesimp.KeepModels=保留已下载的模型（数 GB），以便日后重新安装？
japanese.TaskDesktop=デスクトップにショートカットを作成
japanese.TaskAutoUpdate=自動更新する（1 日 1 回 GitHub を確認）
japanese.RunStart={#AppTitle} を起動
japanese.RunLog=install.log を表示
japanese.SizeAbout=約 %1 GB
japanese.Elapsed=経過時間 %1
japanese.StillWorking=処理中です。ネットワークが遅いかもしれません。
japanese.KeepModels=ダウンロード済みのモデル（数 GB）を再インストール用に残しますか？
russian.TaskDesktop=Ярлыки на рабочем столе
russian.TaskAutoUpdate=Обновлять автоматически (проверка GitHub раз в день)
russian.RunStart=Запустить {#AppTitle}
russian.RunLog=Показать install.log
russian.SizeAbout=около %1 ГБ
russian.Elapsed=прошло %1
russian.StillWorking=всё ещё работает, медленная сеть?
russian.KeepModels=Сохранить загруженные модели (несколько ГБ) для повторной установки?
#ifndef CoreLayout
english.RepairName={#AppTitle} - repair install
english.LangPageTitle=Which languages do you want to understand?
english.LangPageDesc=Only the chosen language packs are downloaded.
english.PackZh=Chinese
english.PackEn=English
english.PackJa=Japanese
english.PackKo=Korean
english.PackRu=Russian
english.GpuPack=GPU pack for NVIDIA graphics cards (CUDA libraries)
english.ProgressTitle=Installing Python packages and models
english.ProgressDesc=This can take 10-40 minutes. Keep the internet connection on.
english.ProgressDescSize=This can take 10-40 minutes and downloads %1 (models, language packs and Python packages). Keep the internet connection on.
english.StartInstall=Starting install.ps1 ...
english.FailInstall=install.ps1 did not finish cleanly (exit code %1). See %2, fix the cause (usually the internet connection) and run "%3" from the Start menu.
chinesetrad.RepairName={#AppTitle} - 重新安裝
chinesetrad.LangPageTitle=你想聽懂哪些語言？
chinesetrad.LangPageDesc=只會下載勾選的語言包。
chinesetrad.PackZh=中文
chinesetrad.PackEn=英文
chinesetrad.PackJa=日文
chinesetrad.PackKo=韓文
chinesetrad.PackRu=俄文
chinesetrad.GpuPack=NVIDIA 顯示卡加速包（CUDA 函式庫）
chinesetrad.ProgressTitle=安裝 Python 套件與模型
chinesetrad.ProgressDesc=需要 10-40 分鐘，請保持網路連線。
chinesetrad.ProgressDescSize=需要 10-40 分鐘，下載%1（模型、語言包與 Python 套件），請保持網路連線。
chinesetrad.StartInstall=正在啟動 install.ps1 ...
chinesetrad.FailInstall=安裝沒有完成（結束代碼 %1）。請看 %2，排除原因（通常是網路連線）後，從開始選單執行「%3」。
chinesesimp.RepairName={#AppTitle} - 重新安装
chinesesimp.LangPageTitle=你想听懂哪些语言？
chinesesimp.LangPageDesc=只会下载勾选的语言包。
chinesesimp.PackZh=中文
chinesesimp.PackEn=英语
chinesesimp.PackJa=日语
chinesesimp.PackKo=韩语
chinesesimp.PackRu=俄语
chinesesimp.GpuPack=NVIDIA 显卡加速包（CUDA 库）
chinesesimp.ProgressTitle=安装 Python 软件包与模型
chinesesimp.ProgressDesc=需要 10-40 分钟，请保持网络连接。
chinesesimp.ProgressDescSize=需要 10-40 分钟，下载%1（模型、语言包与 Python 软件包），请保持网络连接。
chinesesimp.StartInstall=正在启动 install.ps1 ...
chinesesimp.FailInstall=安装没有完成（退出代码 %1）。请查看 %2，排除原因（通常是网络连接）后，从开始菜单运行“%3”。
japanese.RepairName={#AppTitle} - 再インストール
japanese.LangPageTitle=どの言語を聞き取りたいですか？
japanese.LangPageDesc=選んだ言語パックだけをダウンロードします。
japanese.PackZh=中国語
japanese.PackEn=英語
japanese.PackJa=日本語
japanese.PackKo=韓国語
japanese.PackRu=ロシア語
japanese.GpuPack=NVIDIA グラフィックカード用 GPU パック（CUDA ライブラリ）
japanese.ProgressTitle=Python パッケージとモデルをインストール中
japanese.ProgressDesc=10～40 分かかります。インターネットに接続したままにしてください。
japanese.ProgressDescSize=10～40 分かかり、%1をダウンロードします（モデル、言語パック、Python パッケージ）。インターネットに接続したままにしてください。
japanese.StartInstall=install.ps1 を開始しています ...
japanese.FailInstall=install.ps1 が正常に終了しませんでした（終了コード %1）。%2 を確認し、原因（多くはインターネット接続）を直してから、スタートメニューの「%3」を実行してください。
russian.RepairName={#AppTitle} - переустановка
russian.LangPageTitle=Какие языки вы хотите понимать?
russian.LangPageDesc=Загружаются только выбранные языковые пакеты.
russian.PackZh=Китайский
russian.PackEn=Английский
russian.PackJa=Японский
russian.PackKo=Корейский
russian.PackRu=Русский
russian.GpuPack=Пакет GPU для видеокарт NVIDIA (библиотеки CUDA)
russian.ProgressTitle=Установка пакетов Python и моделей
russian.ProgressDesc=Это займёт 10-40 минут. Не отключайте интернет.
russian.ProgressDescSize=Это займёт 10-40 минут, загрузка: %1 (модели, языковые пакеты и пакеты Python). Не отключайте интернет.
russian.StartInstall=Запуск install.ps1 ...
russian.FailInstall=install.ps1 не завершился (код выхода %1). Посмотрите %2, устраните причину (обычно это интернет) и запустите "%3" из меню Пуск.
#else
english.CoreTitle=Downloading the default models
english.CoreDesc=Downloading %1. Keep the internet connection on.
english.StartSetup=Starting setup ...
english.FailCore=The default models did not finish downloading (exit code %1). See %2, check the internet connection and start %3: it retries the download on first start.
chinesetrad.CoreTitle=下載預設模型
chinesetrad.CoreDesc=下載%1，請保持網路連線。
chinesetrad.StartSetup=正在啟動安裝 ...
chinesetrad.FailCore=預設模型沒有下載完成（結束代碼 %1）。請看 %2，確認網路連線後啟動 %3，它會在第一次啟動時再試一次下載。
chinesesimp.CoreTitle=下载默认模型
chinesesimp.CoreDesc=下载%1，请保持网络连接。
chinesesimp.StartSetup=正在启动安装 ...
chinesesimp.FailCore=默认模型没有下载完成（退出代码 %1）。请查看 %2，确认网络连接后启动 %3，它会在第一次启动时重新下载。
japanese.CoreTitle=既定のモデルをダウンロード中
japanese.CoreDesc=%1をダウンロードします。インターネットに接続したままにしてください。
japanese.StartSetup=セットアップを開始しています ...
japanese.FailCore=既定のモデルのダウンロードが完了しませんでした（終了コード %1）。%2 を確認し、インターネット接続を確かめてから %3 を起動してください。初回起動時にダウンロードを再試行します。
russian.CoreTitle=Загрузка моделей по умолчанию
russian.CoreDesc=Загрузка: %1. Не отключайте интернет.
russian.StartSetup=Запуск установки ...
russian.FailCore=Модели по умолчанию не загрузились (код выхода %1). Посмотрите %2, проверьте интернет и запустите %3: загрузка повторится при первом запуске.
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktop}"
Name: "autoupdate"; Description: "{cm:TaskAutoUpdate}"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "tinglongwu.ico"; Flags: ignoreversion

[Icons]
#ifdef CoreLayout
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\{#GuiExe}"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\{#GuiExe}"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"; Tasks: desktopicon
#else
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"; Tasks: desktopicon
Name: "{autoprograms}\{cm:RepairName}"; Filename: "{app}\install.bat"; WorkingDir: "{app}"; IconFilename: "{app}\tinglongwu.ico"
#endif

[Run]
#ifdef CoreLayout
Filename: "{app}\{#GuiExe}"; WorkingDir: "{app}"; Description: "{cm:RunStart}"; Flags: postinstall nowait skipifsilent skipifdoesntexist
#else
Filename: "{app}\.venv\Scripts\tlwu-gui.exe"; WorkingDir: "{app}"; Description: "{cm:RunStart}"; Flags: postinstall nowait skipifsilent skipifdoesntexist
Filename: "{app}\install.log"; Description: "{cm:RunLog}"; Flags: postinstall shellexec skipifsilent unchecked skipifdoesntexist
#endif

[InstallDelete]
; #1664: pre-move root packages (or their __pycache__) shadow src/ as namespace packages; the stage ships src/
Type: filesandordirs; Name: "{app}\tinglongwu"
Type: filesandordirs; Name: "{app}\tinglongwu_setup"
Type: filesandordirs; Name: "{app}\tlwu_app"
Type: filesandordirs; Name: "{app}\tlwu_eval"

[UninstallDelete]
; talk-mode shortcuts are made in code only when tlwu-talk.ps1 exists (#124)
Type: files; Name: "{autoprograms}\{#TalkTitle}.lnk"
Type: files; Name: "{autodesktop}\{#TalkTitle}.lnk"

[Code]
#ifndef CoreLayout
const
  { #751: indices of the options on the language page }
  PACK_ZH = 0;
  PACK_EN = 1;
  PACK_JA = 2;
  PACK_KO = 3;   { #754 }
  PACK_RU = 4;
  OPT_GPU = 5;
  { #753: the one download-size table (MB) behind the language page labels and the progress text.
    Model sizes mirror tinglongwu_setup/packs.py SIZE_MB; the core totals are install.ps1's $SizeGB ('~2 GB' / '~3 GB')
    and "~3 GB of Python packages" lines. Hy-MT2 is in the core download on every path (install.ps1 step 5, #301), so a
    ja / ko / ru pack adds no second copy of it: only the Russian Piper voice is extra. }
  HYMT2_MB = 1100;
  PIPER_RU_MB = 60;
  CORE_MODELS_CPU_MB = 2000;   { Whisper small + Hy-MT2 + Kokoro }
  CORE_MODELS_GPU_MB = 3000;   { Whisper large-v3-turbo + Hy-MT2 + Kokoro }
  PY_PACKAGES_MB = 3000;
#endif

const
  { #755: the page refreshes every REFRESH_TICKS * POLL_MS = 5 s; STALL_TICKS * POLL_MS = 120 s without log growth -> "still working" }
  POLL_MS = 500;
  REFRESH_TICKS = 10;
  STALL_TICKS = 240;

var
  LastLine: String;     { #755: last non-empty install.log line, shown on the page }
  LogLineCount: Integer;
  Steps: Integer;
  ProgressPage: TOutputProgressWizardPage;
  InstallRC: Integer;
  KeepModels: Boolean;
#ifndef CoreLayout
  LangPage: TInputOptionWizardPage;
#endif

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

#ifndef CoreLayout
{ #672: primary language id of the OS UI language -> index into the pack list below (zh, en, ja, ko, ru); #754: $12 Korean }
function LocaleLangIndex: Integer;
begin
  case GetUILanguage and $3FF of
    $04: Result := PACK_ZH;
    $11: Result := PACK_JA;
    $12: Result := PACK_KO;
    $19: Result := PACK_RU;
  else Result := PACK_EN;
  end;
end;

{ #753: size of one language pack on its own (what the language page shows); mirrors packs.pack_size_mb }
function PackMB(Pack: Integer): Integer;
begin
  case Pack of
    PACK_JA, PACK_KO: Result := HYMT2_MB;
    PACK_RU: Result := HYMT2_MB + PIPER_RU_MB;
  else Result := 0;
  end;
end;

#endif

{ #753: "about 1.1 GB" (MB rounded to 0.1 GB) }
function SizeText(MB: Integer): String;
var
  Tenths: Integer;
begin
  Tenths := (MB + 50) div 100;
  Result := FmtMessage(CustomMessage('SizeAbout'), [IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10)]);
end;

#ifndef CoreLayout
{ #753: ", about 1.1 GB" for a language page label, '' for a pack with nothing of its own (zh, en) }
function PackNote(Pack: Integer): String;
begin
  Result := '';
  if PackMB(Pack) > 0 then Result := ', ' + SizeText(PackMB(Pack));
end;

{ #753 (replaces the #672 pack-only sum, which said "about 0 MB" for zh + en): whole download of this install in MB =
  core models (Hy-MT2 included, Whisper by tier) + Python packages + what the ticked packs add beyond the core (ru Piper) }
function DownloadMB(Gpu: Boolean): Integer;
begin
  if Gpu then Result := CORE_MODELS_GPU_MB else Result := CORE_MODELS_CPU_MB;
  Result := Result + PY_PACKAGES_MB;
  if LangPage.Values[PACK_RU] then Result := Result + PIPER_RU_MB;
end;

{ comma list for tlwu-setup --packs; zh+en when nothing is ticked }
function SelectedPacks: String;
var
  I: Integer;
  Codes: array of String;
begin
  SetArrayLength(Codes, PACK_RU + 1);
  Codes[PACK_ZH] := 'zh'; Codes[PACK_EN] := 'en'; Codes[PACK_JA] := 'ja'; Codes[PACK_KO] := 'ko'; Codes[PACK_RU] := 'ru';
  Result := '';
  for I := PACK_ZH to PACK_RU do
    if LangPage.Values[I] then begin
      if Result <> '' then Result := Result + ',';
      Result := Result + Codes[I];
    end;
  if Result = '' then Result := 'zh,en';
end;
#endif

procedure InitializeWizard;
begin
#ifdef CoreLayout
  { #771: no language page in the core wizard: the default listen models (en -> zh-TW) are all it installs }
  ProgressPage := CreateOutputProgressPage(CustomMessage('CoreTitle'),
    FmtMessage(CustomMessage('CoreDesc'), [SizeText({#DefaultListenMB})]));
  InstallRC := -1;
end;
#else
  LangPage := CreateInputOptionPage(wpSelectTasks, CustomMessage('LangPageTitle'), CustomMessage('LangPageDesc'), '', False, False);
  LangPage.Add(CustomMessage('PackZh') + ' (zh' + PackNote(PACK_ZH) + ')');
  LangPage.Add(CustomMessage('PackEn') + ' (en' + PackNote(PACK_EN) + ')');
  LangPage.Add(CustomMessage('PackJa') + ' (ja' + PackNote(PACK_JA) + ')');
  LangPage.Add(CustomMessage('PackKo') + ' (ko' + PackNote(PACK_KO) + ')');
  LangPage.Add(CustomMessage('PackRu') + ' (ru' + PackNote(PACK_RU) + ')');
  LangPage.Values[PACK_ZH] := True;   { zh is the output language of listen mode }
  LangPage.Values[PACK_EN] := True;   { #751: English meetings are the main use case, always pre-checked }
  LangPage.Values[LocaleLangIndex] := True;   { plus the OS-language pack (ja / ko / ru) }
  LangPage.Add(CustomMessage('GpuPack'));
  LangPage.Values[OPT_GPU] := HasNvidiaDriver;
  ProgressPage := CreateOutputProgressPage(CustomMessage('ProgressTitle'), CustomMessage('ProgressDesc'));
  InstallRC := -1;
end;
#endif

{ highest "[n/N]" step that install.ps1 has printed so far; N is read from the line itself (never hard-coded, #700) }
function LastStep(const LogFile: String): Integer;
var
  Lines: TArrayOfString;
  I, N, Total, Slash, Close: Integer;
begin
  Result := 0;   { the caller keeps the highest value seen: the log can be briefly locked by the writer }
  if not LoadStringsFromFile(LogFile, Lines) then exit;
  LogLineCount := GetArrayLength(Lines);
  for I := LogLineCount - 1 downto 0 do
    if Trim(Lines[I]) <> '' then begin LastLine := Trim(Lines[I]); break; end;
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
        end;
      end;
    end;
end;

{ #755: "NN%" at the end of a download line (tlwu-setup / pip print it); -1 when the line has none }
function LinePercent(const Line: String): Integer;
var
  P, S: Integer;
begin
  Result := -1;
  P := Pos('%', Line);
  if P < 2 then exit;
  S := P - 1;
  while (S > 0) and (Line[S] >= '0') and (Line[S] <= '9') and (P - S <= 3) do S := S - 1;
  if S < P - 1 then Result := StrToIntDef(Copy(Line, S + 1, P - S - 1), -1);
  if Result > 100 then Result := -1;
end;

function Pad2(N: Integer): String;
begin
  Result := IntToStr(N);
  if N < 10 then Result := '0' + Result;
end;

{ #1002: language picked on the first wizard page -> tlwu language code (install.ps1 -Lang, ui_lang config;
  tinglongwu.i18n.normalize maps zh-CN to the zh-TW UI and ja to en). Every Chinese / Russian Windows now gets its own
  wizard page by default, so "english" is a real choice (or the default on a Windows whose language the app has no UI
  for, which falls back to English anyway). }
function WizardLangCode: String;
begin
  Result := 'en';
  if ActiveLanguage = 'chinesetrad' then Result := 'zh-TW'
  else if ActiveLanguage = 'chinesesimp' then Result := 'zh-CN'
  else if ActiveLanguage = 'japanese' then Result := 'ja'
  else if ActiveLanguage = 'russian' then Result := 'ru';
end;

procedure RunInstall;
var
  Params, LogFile, DoneFile, Size, Exe, StartMsg: String;
  RC, Waited, Seen, Sub, Pct, SecInStep, Elapsed, QuietTicks, LastCount: Integer;
  Msg: String;
  Gpu: Boolean;
  Done: AnsiString;
begin
  LogFile := ExpandConstant('{app}\install.log');
  DoneFile := ExpandConstant('{app}\install.done');
  DeleteFile(DoneFile);
#ifdef CoreLayout
  { #771: tlwu.exe setup --default-listen, hidden, output to install.log (its "[n/N]" lines drive the bar); cmd writes
    the real exit code to install.done once the exe has finished. /V:ON so !errorlevel! is read after the exe ran. }
  Exe := ExpandConstant('{cmd}');
  Params := '/V:ON /C ""' + ExpandConstant('{app}\tlwu.exe') + '" setup --default-listen --defer-mt >"' + LogFile + '" 2>&1 & echo !errorlevel! >"' + DoneFile + '""';
  Size := SizeText({#DefaultListenMB});
  ProgressPage.Description := FmtMessage(CustomMessage('CoreDesc'), [Size]);
  StartMsg := CustomMessage('StartSetup');
  Steps := 2;   { replaced by N from the first "[n/N]" log line }
#else
  Exe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Params := '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ExpandConstant('{app}\installer\run_install.ps1') + '"';
  { #1531: no force-CPU task; the app falls back to CPU by itself when the GPU self-test fails }
  Gpu := LangPage.Values[OPT_GPU];
  if not Gpu then Params := Params + ' -Cpu';
  Params := Params + ' -Packs ' + SelectedPacks;
  { #1002: install.ps1 -Lang also rewrites ui_lang, so a silent self-update passes none and keeps the user's choice }
  if not WizardSilent then Params := Params + ' -Lang ' + WizardLangCode;
  Size := SizeText(DownloadMB(Gpu));
  ProgressPage.Description := FmtMessage(CustomMessage('ProgressDescSize'), [Size]);
  StartMsg := CustomMessage('StartInstall');
  Steps := 8;   { replaced by N from the first "[n/N]" log line }
#endif
  ProgressPage.SetText(StartMsg, LogFile);
  ProgressPage.SetProgress(0, Steps * 100);
  ProgressPage.Show;
  try
    if not Exec(Exe, Params, ExpandConstant('{app}'), SW_HIDE, ewNoWait, RC) then begin
      Log('could not start the install process: ' + SysErrorMessage(RC));
      exit;
    end;
    Waited := 0;
    Seen := 0;
    SecInStep := 0;
    QuietTicks := 0;
    LastCount := -1;
    LastLine := '';
    LogLineCount := 0;
    while (not FileExists(DoneFile)) and (Waited < 6 * 3600 * 2) do begin   { 6 h cap, 500 ms per tick }
      RC := LastStep(LogFile);
      if RC > Seen then begin Seen := RC; SecInStep := 0; end;
      if LogLineCount <> LastCount then begin LastCount := LogLineCount; QuietTicks := 0; end
      else QuietTicks := QuietTicks + 1;
      if Waited mod REFRESH_TICKS = 0 then begin
        Elapsed := Waited * POLL_MS div 1000;
        SecInStep := SecInStep + REFRESH_TICKS * POLL_MS div 1000;
        { sub-progress inside the current step: the download percent when the last line has one, else a slow creep
          that stops at 90 so the bar always moves but never claims the step is done }
        Pct := LinePercent(LastLine);
        if Pct >= 0 then Sub := Pct
        else begin Sub := SecInStep div 2 + 2; if Sub > 90 then Sub := 90; end;
        if Sub > 99 then Sub := 99;
        if Seen > 0 then Msg := '[' + IntToStr(Seen) + '/' + IntToStr(Steps) + ']' else Msg := StartMsg;
        Msg := Msg + '   ' + FmtMessage(CustomMessage('Elapsed'), [IntToStr(Elapsed div 60) + ':' + Pad2(Elapsed mod 60)]);
        if QuietTicks >= STALL_TICKS then Msg := Msg + '   ' + CustomMessage('StillWorking');
        ProgressPage.SetText(Msg, LastLine);
        if Seen > 0 then ProgressPage.SetProgress((Seen - 1) * 100 + Sub, Steps * 100)
        else ProgressPage.SetProgress(Sub div 4, Steps * 100);
      end;
      Sleep(POLL_MS);
      Waited := Waited + 1;
    end;
    LastStep(LogFile);   { #705: the polling may be up to 5 s behind: read the final lines (they say why a step stopped) }
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

{ #1002: the repair shortcut is named in the wizard language (RepairName). Before the icons are made, remove the old
  "<app> - ..." Start menu shortcut of an earlier install (the bilingual one, or another language), so a reinstall
  leaves exactly one. Only .lnk files with that prefix: the app shortcut has no " - ", the talk shortcut another name. }
procedure RemoveOldRepairShortcuts;
var
  F: TFindRec;
  Dir: String;
begin
  Dir := ExpandConstant('{autoprograms}');
  if FindFirst(Dir + '\{#AppTitle} - *.lnk', F) then
    try
      repeat
        DeleteFile(Dir + '\' + F.Name);
      until not FindNext(F);
    finally
      FindClose(F);
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then RemoveOldRepairShortcuts;
  if CurStep = ssPostInstall then begin
    { #698: the wizard is where the user chooses; a silent self-update keeps the existing choice }
    if not WizardSilent then
      if WizardIsTaskSelected('autoupdate') then SetConfigValue('auto_update', '1')
      else SetConfigValue('auto_update', '0');
    { #1002: tlwu-gui opens in the wizard language (zh/en/ru UI only: ja falls back to en); a silent self-update
      keeps the user's existing ui_lang }
    if not WizardSilent then SetConfigValue('ui_lang', WizardLangCode);
    RunInstall;   { source layout: install.ps1; core layout (#771): tlwu.exe setup --default-listen }
    MakeTalkShortcuts;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
#ifdef CoreLayout
  if (CurPageID = wpFinished) and (InstallRC <> 0) then
    { a [Code] line must not start with '[': ISCC reads it as a section tag }
    WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('FailCore'), [IntToStr(InstallRC),
      ExpandConstant('{app}\install.log'), '{#AppTitle}']);
#else
  if (CurPageID = wpFinished) and (InstallRC <> 0) then begin
    WizardForm.FinishedLabel.Caption := FmtMessage(CustomMessage('FailInstall'), [IntToStr(InstallRC),
      ExpandConstant('{app}\install.log'), CustomMessage('RepairName')]);
    { #705: the last install.log line names the cause (e.g. the missing Microsoft Visual C++ runtime) }
    if LastLine <> '' then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + #13#10 + LastLine;
  end;
#endif
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
      (MsgBox(CustomMessage('KeepModels'), mbConfirmation, MB_YESNO) = IDYES));
  if CurUninstallStep = usPostUninstall then DeleteLeftovers(ExpandConstant('{app}'));
end;
