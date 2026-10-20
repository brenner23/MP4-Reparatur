@echo off
REM ===========================================================================
REM  MP4-Reparatur - Build-Skript / build script
REM
REM  Copyright (c) 2026 Brenner23 - MIT License (siehe / see LICENSE)
REM  https://github.com/brenner23/MP4-Reparatur
REM
REM  DE: Du baust das Programm selbst aus dem Quelltext. Es werden keine
REM      fertigen EXE-Dateien mitgeliefert. ffmpeg ist nicht enthalten, das
REM      Programm laedt es beim ersten Start selbst (eigene Lizenz, GPL).
REM  EN: You build the program yourself from source. No ready-made EXE files
REM      are shipped. ffmpeg is not included, the program downloads it on
REM      first start (separate licence, GPL).
REM
REM  Aufruf / usage:
REM    build.bat            Ordner mit EXE + _internal (empfohlen) + ZIP
REM                         + Installer, falls Inno Setup installiert ist
REM    build.bat onefile    eine einzelne EXE (Windows/SmartScreen warnt
REM                         dabei leider haeufiger)
REM
REM  Braucht / needs: Python 3.9+ (python.org) und PyInstaller:
REM    python -m pip install pyinstaller
REM  Optional fuer den Installer / optional for the installer:
REM    Inno Setup 6 oder 7 (kostenlos / free): https://jrsoftware.org/isdl.php
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "APP=MP4-Reparatur"
set "VERSION=1.0.1"
set "PUBLISHER=Brenner23"
set "MODE=onedir"
if /i "%~1"=="onefile" set "MODE=onefile"

echo.
echo  %APP% %VERSION% - Build (%MODE%)
echo  =========================================
echo.

REM --- Python + PyInstaller pruefen ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python wurde nicht gefunden. Bitte von https://www.python.org installieren
    echo          und beim Setup "Add python.exe to PATH" anhaken.
    goto :ende
)
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] PyInstaller fehlt - wird jetzt installiert ...
    python -m pip install --user pyinstaller
    python -m PyInstaller --version >nul 2>&1
    if errorlevel 1 (
        echo [FEHLER] PyInstaller konnte nicht installiert werden.
        goto :ende
    )
)

REM --- EXE bauen ---
if exist build rmdir /s /q build
if exist "dist\%APP%" rmdir /s /q "dist\%APP%"
if exist "dist\%APP%.exe" del /q "dist\%APP%.exe"
python -m PyInstaller --noconfirm --clean --%MODE% --noconsole --icon "%APP%.ico" ^
    --add-data "%APP%.ico;." --name "%APP%" "%APP%.py"
if errorlevel 1 (
    echo [FEHLER] PyInstaller ist fehlgeschlagen.
    goto :ende
)
rmdir /s /q build >nul 2>&1
del /q "%APP%.spec" >nul 2>&1
if not exist release mkdir release

if /i "%MODE%"=="onefile" (
    copy /y "dist\%APP%.exe" "release\%APP%_%VERSION%.exe" >nul
    echo.
    echo [OK] release\%APP%_%VERSION%.exe
    goto :ende
)

REM --- Portable ZIP (Ordner mit EXE + _internal) ---
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\%APP%' -DestinationPath 'release\%APP%_%VERSION%_portable.zip'"
echo [OK] dist\%APP%\%APP%.exe  (ohne Installation startbar / runs without installing)
echo [OK] release\%APP%_%VERSION%_portable.zip

REM --- Installer, falls Inno Setup vorhanden ---
set "ISCC="
for %%P in ("%ProgramFiles%\Inno Setup 7\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if not defined ISCC if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    echo.
    echo [HINWEIS] Inno Setup nicht gefunden - es wird kein Installer gebaut.
    echo           Das ist kein Fehler: der Ordner dist\%APP% bzw. das ZIP reicht.
    echo           Wer einen Installer moechte: https://jrsoftware.org/isdl.php
    goto :ende
)

set "ISS=%TEMP%\%APP%_build.iss"
(
echo [Setup]
echo AppName=%APP%
echo AppVersion=%VERSION%
echo AppPublisher=%PUBLISHER%
echo AppPublisherURL=https://github.com/brenner23/MP4-Reparatur
echo AppId={{%APP%-SETUP-0001}
echo WizardStyle=modern
echo DefaultDirName={autopf}\%APP%
echo DefaultGroupName=%APP%
echo UninstallDisplayIcon={app}\%APP%.exe
echo OutputDir=%CD%\release
echo OutputBaseFilename=%APP%_%VERSION%_Setup
echo SetupIconFile=%CD%\%APP%.ico
echo LicenseFile=%CD%\LICENSE
echo Compression=lzma2
echo SolidCompression=yes
echo PrivilegesRequired=admin
echo ArchitecturesAllowed=x64compatible
echo ArchitecturesInstallIn64BitMode=x64compatible
echo.
echo [Languages]
echo Name: "german"; MessagesFile: "compiler:Languages\German.isl"
echo Name: "english"; MessagesFile: "compiler:Default.isl"
echo.
echo [Files]
echo Source: "%CD%\dist\%APP%\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
echo.
echo [Icons]
echo Name: "{group}\%APP%"; Filename: "{app}\%APP%.exe"
echo Name: "{group}\%APP% deinstallieren"; Filename: "{uninstallexe}"
echo Name: "{commondesktop}\%APP%"; Filename: "{app}\%APP%.exe"; Tasks: desktopicon
echo.
echo [Tasks]
echo Name: "desktopicon"; Description: "Desktop-Icon / desktop icon"; Flags: unchecked
echo.
echo [Run]
echo Filename: "{app}\%APP%.exe"; Description: "%APP%"; Flags: nowait postinstall skipifsilent
echo.
echo [UninstallDelete]
echo Type: filesandordirs; Name: "{localappdata}\%APP%"
echo Type: filesandordirs; Name: "{%%USERPROFILE}\%APP%"
) > "%ISS%"
"%ISCC%" /Q "%ISS%"
if errorlevel 1 (
    echo [FEHLER] Inno Setup ist fehlgeschlagen.
) else (
    echo [OK] release\%APP%_%VERSION%_Setup.exe
)
del /q "%ISS%" >nul 2>&1

:ende
echo.
if not defined NOPAUSE pause
endlocal
