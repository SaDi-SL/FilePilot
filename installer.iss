[Setup]
AppName=FilePilot
AppVersion=1.0.0
AppPublisher=Sadi Al-lulu
AppPublisherURL=
DefaultDirName={autopf}\FilePilot
DefaultGroupName=FilePilot
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=FilePilot_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; SetupIconFile=icon.ico
UninstallDisplayIcon={app}\FilePilot.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\FilePilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FilePilot"; Filename: "{app}\FilePilot.exe"
Name: "{autodesktop}\FilePilot"; Filename: "{app}\FilePilot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FilePilot.exe"; Description: "Launch FilePilot"; Flags: nowait postinstall skipifsilent