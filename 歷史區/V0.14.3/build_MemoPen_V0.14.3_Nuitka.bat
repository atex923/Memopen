@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"
set "APP=MemoPen_V0.14.3.py"
set "DIST=%~dp0dist"
set "CHECK=%~dp0nuitka_standalone_check"

if not exist "%APP%" (
    echo [錯誤] 找不到 %APP%
    pause
    exit /b 1
)

echo ============================================================
echo MemoPen V0.14.3 - Nuitka 建置
echo ============================================================

echo [1/4] 安裝或更新建置套件...
py -m pip install --upgrade Nuitka PySide6 ordered-set zstandard
if errorlevel 1 goto :error

echo.
echo [2/4] 檢查 Python 語法...
py -m py_compile "%APP%"
if errorlevel 1 goto :error

echo.
echo [3/4] 先以 standalone 模式檢查 PySide6 與 DLL 相依項...
if exist "%CHECK%" rmdir /s /q "%CHECK%"
py -m nuitka ^
  --mode=standalone ^
  --assume-yes-for-downloads ^
  --remove-output ^
  --output-dir="%CHECK%" ^
  --report="%CHECK%\MemoPen_V0.14.3_standalone_report.xml" ^
  "%APP%"
if errorlevel 1 goto :error

echo.
echo [4/4] 相依項正常，建立單一 EXE...
if not exist "%DIST%" mkdir "%DIST%"
py -m nuitka ^
  --mode=onefile ^
  --assume-yes-for-downloads ^
  --remove-output ^
  --output-dir="%DIST%" ^
  --output-filename=MemoPen_V0.14.3.exe ^
  --report="%DIST%\MemoPen_V0.14.3_onefile_report.xml" ^
  "%APP%"
if errorlevel 1 goto :error

echo.
echo ============================================================
echo 完成：%DIST%\MemoPen_V0.14.3.exe
echo ============================================================
explorer "%DIST%"
pause
exit /b 0

:error
echo.
echo ============================================================
echo 建置失敗，錯誤碼：%errorlevel%
echo 請查看上方訊息及 Nuitka report XML。
echo ============================================================
pause
exit /b 1
