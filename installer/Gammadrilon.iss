; Установщик Gammadrilon: per-user по умолчанию, без запроса прав.
;
; Почему так: приложение должно ставиться «из коробки» на чужой ПК, где у
; человека нет прав администратора. В {autopf} при обычных правах Inno
; подставляет %LOCALAPPDATA%\Programs — туда писать можно всегда. Кто хочет
; поставить для всех пользователей, выбирает это в мастере (UAC спросят один раз).
;
; Что НЕ ставится намеренно: zapret, WinDivert и драйвер PawnIO. Это отдельные
; компоненты уровня ядра; их установка антивирусами и Windows воспринимается
; как действие зловреда, а виноватым окажется установщик Gammadrilon.

#define AppName "Gammadrilon"
; Версия приходит из build_release.ps1 (/DAppVersion=...), здесь — запасная.
#ifndef AppVersion
  #define AppVersion "3.6.2"
#endif
#define AppExe "Gammadrilon.exe"
#define AppId "{{8F9E4A21-5B3C-4D1E-9A77-2C6F8B1D0E3A}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; версия в метаданных setup.exe: без неё «Свойства → Подробно» пустые
VersionInfoVersion={#AppVersion}.0.0
AppPublisher=Gammadrilon
AppPublisherURL=https://github.com/bitarovyuriy/Gammadrilon
AppSupportURL=https://github.com/bitarovyuriy/Gammadrilon
DefaultDirName={autopf}\Gammadrilon
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Gammadrilon-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
CloseApplicationsFilter=*.exe
MinVersion=10.0
DisableWelcomePage=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Портативная сборка целиком: exe + _internal. presets.json и журнал лежат
; в ProgramData и установкой не затрагиваются.
Source: "..\dist\Gammadrilon\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Файлы, добавленные автообновлением, в манифесте установщика не значатся.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function InitializeSetup(): Boolean;
var
  Release: Cardinal;
begin
  Result := True;
  { .NET Framework 4.8 нужен C#-хелперу датчиков. Ставим не блок, а честное
    предупреждение: без него приложение работает, просто без температур платы. }
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full',
                        'Release', Release) then
  begin
    if Release < 528040 then
      MsgBox('Найдена более старая версия .NET Framework.' + #13#10 +
             'Приложение запустится, но температуры платы будут недоступны.' + #13#10 +
             'Для датчиков нужен .NET Framework 4.8.',
             mbInformation, MB_OK);
  end
  else
    MsgBox('Не найден .NET Framework 4.x.' + #13#10 +
           'Приложение запустится, но температуры платы будут недоступны.',
           mbInformation, MB_OK);
end;
