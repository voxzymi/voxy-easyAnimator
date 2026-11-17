; Voxy EasyAnimator installer (Inno Setup 6).
;   iscc /DAppVersion=0.1.0 installer\VoxyEasyAnimator.iss        (build_windows.ps1 does this for you)
;
; *** NOT BUILT OR RUN IN DEVELOPMENT (no Windows, no Inno Setup there). Treat every [Code] line as untested. ***
; Paths that are marked [verify] come from memory of Adobe's layout; see installer/ae_locations.json.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#include "..\build\supported_ae.inc"

[Setup]
AppId={{6C1F2A3E-7B1D-4F0A-9E54-0A0B0C0D0E01}
AppName=Voxy EasyAnimator
AppVersion={#AppVersion}
AppPublisher=Voxy
DefaultDirName={autopf}\VoxyEasyAnimator
DefaultGroupName=Voxy EasyAnimator
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\build\license_and_notices.txt
OutputDir=..\build\installer
OutputBaseFilename=VoxyEasyAnimator-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\service\voxy-service.exe
DisableProgramGroupPage=yes
; Signing is optional until a certificate is bought (see docs/decisions-needed.md):
;   iscc /DSIGN /Ssigntool="signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f cert.pfx /p $$PASSWORD $f"
#ifdef SIGN
SignTool=signtool
SignedUninstaller=yes
#endif

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut to the help page on the desktop"; Flags: unchecked
#ifdef UNSIGNED_PANEL
Name: "unsignedpanel"; Description: "Allow After Effects to open the Voxy panel (needed until the panel is signed)"; Flags: checkedonce
#endif

[Files]
Source: "..\build\service\*"; DestDir: "{app}\service"; Flags: recursesubdirs ignoreversion
Source: "..\build\models\*"; DestDir: "{app}\models"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist
Source: "..\build\tools\*"; DestDir: "{app}\tools"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist
Source: "..\configs\service.release.yaml"; DestDir: "{app}\configs"; DestName: "service.yaml"; Flags: ignoreversion
Source: "..\build\plugin\VoxyEasyAnimator.aex"; DestDir: "{app}\plugin"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\ae_host\panel\*"; DestDir: "{commoncf32}\Adobe\CEP\extensions\com.voxy.easyanimator"; Excludes: "tests,package.json,node_modules"; Flags: recursesubdirs ignoreversion
Source: "..\docs\INSTALL.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\build\license_and_notices.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Voxy EasyAnimator help"; Filename: "{commoncf32}\Adobe\CEP\extensions\com.voxy.easyanimator\help\index.html"
Name: "{group}\Uninstall Voxy EasyAnimator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Voxy EasyAnimator help"; Filename: "{commoncf32}\Adobe\CEP\extensions\com.voxy.easyanimator\help\index.html"; Tasks: desktopicon

#ifdef UNSIGNED_PANEL
[Registry]
; Without a signed panel, After Effects only loads it when PlayerDebugMode is 1. Remove this once the panel is signed.
Root: HKCU; Subkey: "Software\Adobe\CSXS.9";  ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"; Tasks: unsignedpanel
Root: HKCU; Subkey: "Software\Adobe\CSXS.10"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"; Tasks: unsignedpanel
Root: HKCU; Subkey: "Software\Adobe\CSXS.11"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"; Tasks: unsignedpanel
Root: HKCU; Subkey: "Software\Adobe\CSXS.12"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"; Tasks: unsignedpanel
#endif

[Code]
var
  AEPage: TInputOptionWizardPage;
  CheckPage: TOutputMsgMemoWizardPage;
  AEPaths: array of String;      // install folder of each entry on AEPage
  AENames: array of String;
  UnsupportedNote: String;
  InstalledPlugins: TStringList;

function AfterEffectsRunning(): Boolean;
var Code: Integer;
begin
  // [verify] the After Effects process is AfterFX.exe
  Result := Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq AfterFX.exe" | find /I "AfterFX.exe"', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function FindInstall(const Key, Folder: String): String;
var P: String;
begin
  Result := '';
  // [verify] HKLM\SOFTWARE\Adobe\After Effects\<version>\InstallPath
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Adobe\After Effects\' + Key, 'InstallPath', P) and DirExists(P) then begin Result := P; exit; end;
  P := ExpandConstant('{pf64}\Adobe\') + Folder;
  if DirExists(P) then Result := P;
end;

procedure AddAE(const Name, Key, Folder: String);
var P: String; N: Integer;
begin
  P := FindInstall(Key, Folder);
  if P = '' then exit;
  N := GetArrayLength(AEPaths);
  SetArrayLength(AEPaths, N + 1); SetArrayLength(AENames, N + 1);
  AEPaths[N] := P; AENames[N] := Name;
  AEPage.CheckListBox.AddCheckBox(Name + '  (' + P + ')', '', 0, True, True, False, True, nil);
end;

procedure InitializeWizard();
begin
  AEPage := CreateInputOptionPage(wpSelectDir, 'After Effects versions',
    'Which After Effects should get Voxy EasyAnimator?',
    'Only versions that Voxy supports are listed. Close After Effects before you continue.', False, False);
#if AECount > 0
  AddAE('{#AEName0}', '{#AEKey0}', '{#AEFolder0}');
#endif
#if AECount > 1
  AddAE('{#AEName1}', '{#AEKey1}', '{#AEFolder1}');
#endif
#if AECount > 2
  AddAE('{#AEName2}', '{#AEKey2}', '{#AEFolder2}');
#endif
#if AECount > 3
  AddAE('{#AEName3}', '{#AEKey3}', '{#AEFolder3}');
#endif
#if AECount > 4
  AddAE('{#AEName4}', '{#AEKey4}', '{#AEFolder4}');
#endif
  CheckPage := CreateOutputMsgMemoPage(wpInstalling, 'Checking your setup', 'This tells you in plain words if everything is ready.', 'Results:', '');
  InstalledPlugins := TStringList.Create;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = AEPage.ID then begin
    if GetArrayLength(AEPaths) = 0 then begin
      MsgBox('Setup did not find a supported version of After Effects. Voxy EasyAnimator needs one of the versions listed in the install guide.', mbInformation, MB_OK);
    end;
    while AfterEffectsRunning() do
      if MsgBox('After Effects is open. Close it, then press Retry to continue.', mbError, MB_RETRYCANCEL) = IDCANCEL then begin Result := False; exit; end;
  end;
end;

procedure InstallPlugins();
var I: Integer; Dest: String;
begin
  for I := 0 to GetArrayLength(AEPaths) - 1 do
    if AEPage.Values[I] then begin
      Dest := AEPaths[I] + '\Support Files\Plug-ins\Voxy';           // [verify]
      ForceDirectories(Dest);
      if FileCopy(ExpandConstant('{app}\plugin\VoxyEasyAnimator.aex'), Dest + '\VoxyEasyAnimator.aex', False) then
        InstalledPlugins.Add(Dest + '\VoxyEasyAnimator.aex');
    end;
  InstalledPlugins.SaveToFile(ExpandConstant('{app}\installed_plugins.txt'));
end;

procedure WriteInstallInfo();
var Dir, S: String;
begin
  Dir := ExpandConstant('{localappdata}\VoxyEasyAnimator');
  ForceDirectories(Dir);
  S := '{"service": ["' + ExpandConstant('{app}') + '\service\voxy-service.exe"], "app_dir": "' + ExpandConstant('{app}') + '", "version": "{#AppVersion}"}';
  StringChangeEx(S, '\', '\\', True);
  SaveStringToFile(Dir + '\install.json', S, False);
end;

procedure RunChecks();
var Code: Integer; Out: AnsiString; Tmp, Mark: String;
begin
  Tmp := ExpandConstant('{tmp}\voxy-preflight.txt');
  CheckPage.RichEditViewer.Lines.Clear;
  if Exec(ExpandConstant('{cmd}'), '/C ""' + ExpandConstant('{app}\service\voxy-service.exe') + '" --preflight --plain > "' + Tmp + '""', '', SW_HIDE, ewWaitUntilTerminated, Code)
     and LoadStringFromFile(Tmp, Out) then
    CheckPage.RichEditViewer.Lines.Text := String(Out)
  else
    CheckPage.RichEditViewer.Lines.Text := 'FIX  Voxy could not run its checks. Try running the installer again.';
  Tmp := ExpandConstant('{tmp}\voxy-selftest.txt');
  if Exec(ExpandConstant('{cmd}'), '/C ""' + ExpandConstant('{app}\service\voxy-service.exe') + '" --selftest > "' + Tmp + '""', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    CheckPage.RichEditViewer.Lines.Add('OK   Voxy starts and cuts the sample character.')
  else
    CheckPage.RichEditViewer.Lines.Add('FIX  Voxy did not start. Run the installer again, or open the install guide (Troubleshooting).');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    InstallPlugins();
    WriteInstallInfo();
    RunChecks();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var L: TStringList; I: Integer; Data: String;
begin
  if CurUninstallStep = usUninstall then begin
    L := TStringList.Create;
    try
      if FileExists(ExpandConstant('{app}\installed_plugins.txt')) then begin
        L.LoadFromFile(ExpandConstant('{app}\installed_plugins.txt'));
        for I := 0 to L.Count - 1 do begin DeleteFile(L[I]); RemoveDir(ExtractFileDir(L[I])); end;
      end;
    finally L.Free; end;
  end;
  if CurUninstallStep = usPostUninstall then begin
    Data := ExpandConstant('{localappdata}\VoxyEasyAnimator');
    if DirExists(Data) then
      if MsgBox('Do you also want to delete your Voxy settings and the backups of projects that were not saved yet?' + #13#10 +
                'Backups that sit next to your own project files are never deleted.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
