; FlybyCV kurucusu -- Inno Setup 6 veya 7 (Faz 10 - Adim 10.9g).
;
; Bu dosya DOGRUDAN derlenmez: scripts\build_installer.ps1 asagidaki tanimlari src\core\version.py'den
; okuyup /D ile verir. Surum bu dosyada ASLA yazilmaz (tek kaynak: src\core\version.py).
;
;   MyAppName     uygulama adi            (ornek: "FlybyCV")
;   MyAppVersion  surum                   (MAJOR.MINOR.PATCH)
;   MySourceDir   PyInstaller cikti klasoru (dist\<ad>) -- icindeki her sey kurulur
;   MyOutputDir   Setup.exe'nin yazilacagi klasor
;   MyOutputName  Setup.exe'nin uzantisiz adi

#ifndef MyAppName
  #error MyAppName tanimli degil. Derlemeyi scripts\build_installer.ps1 ile yapin.
#endif
#ifndef MyAppVersion
  #error MyAppVersion tanimli degil. Derlemeyi scripts\build_installer.ps1 ile yapin.
#endif
#ifndef MySourceDir
  #error MySourceDir tanimli degil. Derlemeyi scripts\build_installer.ps1 ile yapin.
#endif
#ifndef MyOutputDir
  #error MyOutputDir tanimli degil. Derlemeyi scripts\build_installer.ps1 ile yapin.
#endif
#ifndef MyOutputName
  #error MyOutputName tanimli degil. Derlemeyi scripts\build_installer.ps1 ile yapin.
#endif

#define MyAppExeName MyAppName + ".exe"

; Uygulamanin eski adi. Ayni AppId ile eski kurulumun ustune kurulunca eski kisayollar ve eski program
; klasoru [InstallDelete] ile temizlenir; eski veri klasoru (%APPDATA%\CV Autopilot) KURULUMDA silinmez
; (uygulama ilk acilista onu %APPDATA%\FlybyCV'ye kopyalar), yalnizca kaldiricida "Evet" denirse silinir.
#define LegacyAppName "CV Autopilot"

[Setup]
; AppId ASLA degismemeli: Windows "ayni uygulama" ve guncelleme/kaldirma eslesmesini bununla yapar.
; Ad degisikliginde (CV Autopilot -> FlybyCV) de AYNI kaldi: yeni kurulum eskisinin yerine gecer.
AppId={{B7E4A2D1-5C3F-4A86-9D0E-2F1B6C8A7E35}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Ali Utku Kılınçsoy
VersionInfoVersion={#MyAppVersion}
; Yonetici izni ISTEMEZ: kullanicinin kendi klasorune (%LOCALAPPDATA%\Programs) kurulur.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
; Eski kurulumun klasorune (Programs\CV Autopilot) degil, her zaman yeni ada kurulur.
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Kurulum dosyasinin (Setup.exe) simgesi; yol bu .iss dosyasina gore.
SetupIconFile=..\assets\app_icon.ico
; Calisan uygulamayi kapatmayi dener; kapanmazsa kullaniciya sorar.
CloseApplications=yes
RestartApplications=no
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Inno Setup 7'de varsayilan kurucu 32-bit'tir (SetupArchitecture=x86); 64-bit kurucu icin x64 secilir.
; Bu direktif 6.x'te YOKTUR (derleme hatasi verir), bu yuzden yalnizca ISPP surumu 7 ve ustuyse yazilir.
; Ver = 32-bit kodlu surum (en ust bayt ana surum).
#if Ver >= 0x07000000
SetupArchitecture=x64
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
; Turkce dil dosyasi derleyicinin Languages klasorunde yoksa (surume gore degisebilir) varsayilan Ingilizce
; kalir; kaldirici sorusu ve kisayol metinleri yine Turkce yazilidir.
#if FileExists(CompilerPath + "Languages\Turkish.isl")
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek kısayollar:"; Flags: unchecked

[InstallDelete]
; Eski adla kurulmus surumun kisayollari ve varsayilan program klasoru (kullanici verisi DEGIL).
Type: files; Name: "{autoprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{autodesktop}\{#LegacyAppName}.lnk"
Type: filesandordirs; Name: "{autopf}\{#LegacyAppName}"

[Files]
; Kisisel veri pakete girmez: build_exe.ps1 / build_installer.ps1 sizinti denetimi yapar.
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} uygulamasını başlat"; Flags: nowait postinstall skipifsilent

[Code]
// Kaldirirken kullanici verisi (%APPDATA%\<Uygulama>: profil, veritabani, olusturulan PDF'ler, log)
// SORULUR. Varsayilan "Hayir"dir; sessiz kaldirmada (/SILENT, /VERYSILENT) ASLA silinmez.
// "Evet" denirse once kasadaki (Windows Kimlik Bilgileri Yoneticisi) mail sifresi ve API anahtarlari
// (eski "cv-autopilot" kayitlari dahil) uygulamanin --purge-secrets bayragiyla silinir, sonra klasor
// ve varsa eski adin veri klasoru (%APPDATA%\CV Autopilot) silinir.

function UserDataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\{#MyAppName}');
end;

function LegacyUserDataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\{#LegacyAppName}');
end;

function DataDirsText(): String;
begin
  Result := '';
  if DirExists(UserDataDir()) then
    Result := Result + UserDataDir() + #13#10;
  if DirExists(LegacyUserDataDir()) then
    Result := Result + LegacyUserDataDir() + ' (eski ad)' + #13#10;
end;

procedure DeleteDataDir(const Dir: String);
begin
  if not DirExists(Dir) then
    Exit;
  if not DelTree(Dir, True, True, True) then
    Log('Kullanici verisi klasoru tamamen silinemedi: ' + Dir);
end;

procedure PurgeSecrets();
var
  ResultCode: Integer;
begin
  // Dosyalar henuz silinmeden calistirilmali; ciktisi yok, sonucu beklenir.
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--purge-secrets', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
    Log('--purge-secrets calistirilamadi.')
  else
    Log('--purge-secrets cikis kodu: ' + IntToStr(ResultCode));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Answer: Integer;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  if UninstallSilent() then
    Exit;
  if not DirExists(UserDataDir()) and not DirExists(LegacyUserDataDir()) then
    Exit;

  Answer := SuppressibleMsgBox(
    'Kişisel verilerin de silinsin mi?' + #13#10 + #13#10 +
    'Profilin, başvuru veritabanın, oluşturulan CV''ler ve kayıt dosyaları şurada duruyor:' + #13#10 +
    DataDirsText() + #13#10 +
    'Ayrıca Windows kasasındaki mail şifren ve API anahtarların da silinir. Bu işlem GERİ ALINAMAZ.' + #13#10 + #13#10 +
    'Verilerini korumak (ör. sonra yeniden kurmak için) istiyorsan "Hayır" seç.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO);

  if Answer = IDYES then
  begin
    PurgeSecrets();
    DeleteDataDir(UserDataDir());
    DeleteDataDir(LegacyUserDataDir());
  end;
end;
