Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
' 只启动 telegram_bot.py（监控+推送），不启动 desktop_app.py 避免 Bot 冲突
WshShell.Run "pythonw telegram_bot.py", 0, False
Set WshShell = Nothing
