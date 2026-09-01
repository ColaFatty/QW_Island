' ============================================================
' QoderWork Dynamic Island Guardian v2.0
' 灵动岛智能守护脚本 - 跟随启动，尊重手动关闭
'
' 逻辑：
'   - QoderWork 每次启动时，自动拉起灵动岛（仅一次）
'   - 用户手动关闭灵动岛后，不会自动重启
'   - QoderWork 关闭后再重新打开，灵动岛会再次自动跟随启动
'   - 完全静默运行，无弹窗无感知
'
' 放置位置：Windows 启动文件夹（开机自动运行）
' ============================================================

Dim shell, wmi, islandPath, interval, launched

Set shell = CreateObject("WScript.Shell")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

islandPath = shell.ExpandEnvironmentStrings("%APPDATA%") & _
    "\QoderWork\Island\QoderWork_Island.exe"
interval = 10
launched = False  ' track if we already launched Island this QW session

' --- Helper: check if a process is running ---
Function ProcessExists(ByVal name)
    On Error Resume Next
    Set procs = wmi.ExecQuery( _
        "SELECT * FROM Win32_Process WHERE Name='" & name & "'")
    If Err.Number <> 0 Then
        ProcessExists = False
        Err.Clear
    Else
        ProcessExists = (procs.Count > 0)
    End If
    On Error GoTo 0
End Function

' --- Helper: launch the Island silently ---
Sub LaunchIsland()
    On Error Resume Next
    ' 已有一个灵动岛在运行则不重复启动（避免手动+自动双实例）
    If ProcessExists("QoderWork_Island.exe") Then Exit Sub
    shell.Run Chr(34) & islandPath & Chr(34), 0, False
End Sub

' ============= Main loop =============
Do
    qwRunning = ProcessExists("QoderWork.exe")

    If qwRunning Then
        ' QoderWork just started a new session -> launch Island once
        If Not launched Then
            LaunchIsland
            launched = True
        End If
    Else
        ' QoderWork closed -> reset flag for next session
        launched = False
    End If

    WScript.Sleep interval * 1000
Loop
