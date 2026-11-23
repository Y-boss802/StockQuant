Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "C:\Users\33664\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
sh.Run """" & pyw & """ """ & dir & "\server.py"" --port 8765 --host 0.0.0.0 --no-open", 0, False
WScript.Sleep 2500
edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
profile = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Temp\StockQuantEdge"
sh.Run "cmd /c rmdir /s /q """ & profile & """", 0, True
WScript.Sleep 400
opts = """--app=http://127.0.0.1:8765"" --user-data-dir=""" & profile & """ --no-first-run --no-default-browser-check --window-size=1200,760 --window-position=60,40 --disable-features=msEdgeSidebarV2,msEdgeShoppingAssistant"
sh.Run """" & edge & """ " & opts, 1, False

