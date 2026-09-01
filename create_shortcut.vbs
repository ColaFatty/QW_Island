' ============================================================
' Create desktop shortcut for QoderWork Dynamic Island
' Pure ASCII + ChrW for Chinese name (immune to encoding issues)
' SpecialFolders("Desktop") handles OneDrive desktop redirect
' ============================================================
Set ws = CreateObject("WScript.Shell")
islandDir = ws.ExpandEnvironmentStrings("%APPDATA%") & "\QoderWork\Island"
desktop = ws.SpecialFolders("Desktop")

Set s = ws.CreateShortcut(desktop & "\" & ChrW(28789) & ChrW(21160) & ChrW(23707) & ".lnk")
s.TargetPath = islandDir & "\QoderWork_Island.exe"
s.WorkingDirectory = islandDir
s.IconLocation = islandDir & "\island.ico,0"
s.Description = "QoderWork Dynamic Island"
s.Save
