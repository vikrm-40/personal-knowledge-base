@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Personal Knowledge Base - UI Phase 2
echo ============================================================
echo.

if not exist "gemini_kb_runtime\gemini_kb.sqlite" (
    echo ERROR: gemini_kb_runtime\gemini_kb.sqlite was not found.
    echo.
    echo Put this Phase 2 package in the same folder as your
    echo gemini_kb_runtime folder, then run this file again.
    echo.
    pause
    exit /b 1
)

python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Streamlit is not installed.
    echo Installing Streamlit now...
    python -m pip install streamlit
    if errorlevel 1 (
        echo.
        echo ERROR: Streamlit installation failed.
        echo Run manually:
        echo   python -m pip install streamlit
        echo.
        pause
        exit /b 1
    )
)

echo Starting Phase 2...
echo A browser window should open automatically.
echo.
python -m streamlit run app.py

pause
