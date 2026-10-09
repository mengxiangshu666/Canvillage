Option Explicit
Dim shell, root, python, launcher
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
python = root & "\runtime\python\pythonw.exe"
launcher = root & "\village_canvas_launch.py"
shell.Run """" & python & """ """ & launcher & """", 0, False
