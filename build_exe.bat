@echo off
echo ========================================
echo Building "Control temp SRT.exe"
echo ========================================
echo.

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install PyInstaller if not already installed
echo Installing PyInstaller...
pip install pyinstaller

REM Build the executable
echo.
echo Building executable...
pyinstaller --clean Control_temp_SRT.spec

echo.
echo ========================================
echo Build complete!
echo ========================================
echo.
echo Executable location: dist\Control temp SRT.exe
echo.
pause
