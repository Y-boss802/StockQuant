@echo off
start "" "C:\Users\33664\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" "D:\HuaweiMoveData\Users\33664\Documents\deepseek-harness\default-workspace\stock_quant\server.py" --port 8765 --host 0.0.0.0 --no-open
ping -n 3 127.0.0.1 >nul
rmdir /s /q "%LOCALAPPDATA%\Temp\StockQuantEdge" >nul 2>&1
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=http://127.0.0.1:8765 --user-data-dir="%LOCALAPPDATA%\Temp\StockQuantEdge" --no-first-run --no-default-browser-check --window-size=1280,800 --window-position=80,50 --disable-features=msEdgeSidebarV2,msEdgeShoppingAssistant

