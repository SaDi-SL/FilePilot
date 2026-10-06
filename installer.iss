; Per-user Windows installer for the packaged professional Qt application.
; Build with: python build.py (or python build.py --installer after --exe)

#ifndef AppName
  #error AppName must be supplied by build.py
#endif
#ifndef AppVersion
  #error AppVersion must be supplied by build.py
#endif
#ifndef AppNumericVersion
  #error AppNumericVersion must be supplied by build.py
#endif
#ifndef AppExeName
  #error AppExeName must be supplied by build.py
#endif

[Setup]
; Keep this AppId stable: it is the product's Windows upgrade identity.
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppNumericVersion}
AppComments=Smart desktop file automation and organization system

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
AllowNoIcons=no

OutputDir=dist\installer
OutputBaseFilename=FilePilot-Setup-{#AppVersion}
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
LicenseFile=LICENSE

UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; User configuration and runtime data are created under LocalAppData by the app.
; The installer owns only application binaries and never packages live user data.
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\ocr\*"; DestDir: "{app}\ocr"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
