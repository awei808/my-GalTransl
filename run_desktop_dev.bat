@echo off
chcp 65001 >nul
setlocal
set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222
cd /d %~dp0

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm not found in PATH.
  pause
  exit /b 1
)

if not exist "desktop\package.json" (
  echo desktop\package.json not found.
  pause
  exit /b 1
)

if not exist "desktop\node_modules" (
  echo Installing desktop frontend dependencies...
  call npm --prefix desktop install
  if errorlevel 1 (
    echo Failed to install desktop dependencies.
    pause
    exit /b 1
  )
)

start "GalTransl Backend" cmd /k python run_backend.py --host 127.0.0.1 --port 12333

where cargo >nul 2>nul
if errorlevel 1 (
  echo Cargo not found. Falling back to browser frontend dev server.
  start "GalTransl Frontend" cmd /k "cd /d %~dp0desktop && npm run dev"
) else (
  start "GalTransl Desktop" cmd /k "set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222 && cd /d %~dp0desktop && npm run tauri:dev"
)

echo Backend and desktop frontend are starting in separate windows.
echo If Cargo is installed, the Tauri desktop shell will start (CDP debug port 9222).
echo Otherwise the browser frontend will start at the Vite dev URL.
echo.
echo.
echo ============================================
echo [MCP 调试]
echo AI 通过 CDP 端口 9222 直连 WebView2 捕获前端报错。
echo.
echo 如果 MCP 工具显示 0 tool 0 prompt，请按以下步骤操作:
echo   1. 在 PowerShell 中验证端口: netstat -an ^| findstr 9222
echo   2. 在 IDE 中重载窗口: Ctrl Shift P -> Reload Window
echo   3. 重载后 MCP 会自动连接 Tauri WebView2
echo ============================================
echo.
pause
