@echo off
REM Bat dosyasının olduğu klasörü çalışma dizini yap
cd /d "%~dp0"

echo Starting FARMACHINE v6.0...
echo Using Virtual Environment Python...

"venv\Scripts\python.exe" main_gui_qml.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo --------------------------------------------------
    echo Bot exited with error code %ERRORLEVEL%.
    echo HATA: Kutuphaneler bulunamadi veya kod coktu.
    echo Lutfen .venv klasorunun var oldugundan emin olun.
    echo --------------------------------------------------
    pause
)
pause