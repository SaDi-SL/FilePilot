; installer.iss — Inno Setup script for FilePilot
; Requirements: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Usage: Open in Inno Setup Compiler and click Build

#define AppName      "FilePilot"
#define AppVersion   "1.0.0"
#define AppPublisher "Sadi Al-lulu"
#define AppURL       "https://github.com/SaDi-SL/FilePilot"
#define AppExeName   "FilePilot.exe"
#define AppDescription "Smart desktop file automation and organization system"

[Setup]
; Basic info
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Install location
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes

; Output
OutputDir=dist\installer
OutputBaseFilename=FilePilot_Setup_v{#AppVersion}
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

; UI
WizardStyle=modern
WizardSmallImageFile=wizard_small.bmp
ShowLanguageDialog=no

; Privileges — no admin required
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Uninstall
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} v{#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Create a &desktop shortcut";    GroupDescription: "Additional shortcuts:"
Name: "startupicon";    Description: "Start FilePilot with &Windows";  GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "quicklaunchicon"; Description: "Create a &Quick Launch shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
; Main executable
Source: "dist\FilePilot.exe"; DestDir: "{app}"; Flags: ignoreversion

; Default config (only if not already present — don't overwrite user settings)
Source: "config\config.json";      DestDir: "{app}\config"; Flags: onlyifdoesntexist
Source: "config\smart_rules.json"; DestDir: "{app}\config"; Flags: onlyifdoesntexist

; Icon
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

; Plugins folder (empty placeholder)
Source: "plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: PluginsFolderExists

[Icons]
; Start Menu
Name: "{group}\{#AppName}";         Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\{#AppName} (Headless)"; Filename: "{app}\{#AppExeName}"; Parameters: "--headless"; IconFilename: "{app}\icon.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

; Desktop shortcut
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

; Quick Launch
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
; Launch after install
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; Windows startup (optional — user can toggle in app settings)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[UninstallDelete]
; Clean up runtime data (logs, stats, hash db) on uninstall
; Note: config is preserved unless user confirms
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\reports"

[Code]
function PluginsFolderExists: Boolean;
begin
  Result := DirExists(ExpandConstant('{src}\plugins'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    // Create required folders if they don't exist
    ForceDirectories(ExpandConstant('{app}\plugins'));
    ForceDirectories(ExpandConstant('{app}\logs'));
    ForceDirectories(ExpandConstant('{app}\reports'));
    ForceDirectories(ExpandConstant('{app}\config'));
    ForceDirectories(ExpandConstant('{app}\backups'));
  end;
end;
