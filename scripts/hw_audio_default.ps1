# Get / set / restore the Windows default audio OUTPUT device (hardware CI on the owner PC, #6 #207 #311).
# ASCII only. Windows PowerShell 5.1. No windows are opened.
#   -Action Get                       print "<endpoint id>|<friendly name>" of the default render device
#   -Action SetByName -Name <regex>   make the active render device matching <regex> the default
#   -Action Set -Id <endpoint id>     make that endpoint the default
#   -Action RestoreSpeakers [-Id <saved id>]
#       restore <saved id> unless it is a virtual cable / missing, then fall back to the real speakers
#       (endpoint named Speakers or U+5587 U+53ED, on a "High Definition Audio Device" adapter)
# Exit 0 = done, 1 = failed.
param(
    [Parameter(Mandatory = $true)][ValidateSet('Get', 'Set', 'SetByName', 'RestoreSpeakers')][string]$Action,
    [string]$Id = '',
    [string]$Name = ''
)
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TlwuAudio {
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        int NotImpl1();
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice dev);
    }
    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        int NotImpl1();
        int NotImpl2();
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    }
    [ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig {
        int M1(); int M2(); int M3(); int M4(); int M5(); int M6(); int M7(); int M8(); int M9(); int M10();
        [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCom { }
    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")] class PolicyConfigCom { }

    public static string GetDefault() {
        var e = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
        IMMDevice d; int hr = e.GetDefaultAudioEndpoint(0, 0, out d);   // eRender, eConsole
        if (hr != 0) return "";
        string id; d.GetId(out id); return id;
    }
    public static void SetDefault(string id) {
        var p = (IPolicyConfig)new PolicyConfigCom();
        for (int role = 0; role < 3; role++) {   // console, multimedia, communications
            int hr = p.SetDefaultEndpoint(id, role);
            if (hr != 0) throw new Exception("SetDefaultEndpoint failed, hr=0x" + hr.ToString("X"));
        }
    }
}
'@

$root = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
function Get-Renders {
    foreach ($k in Get-ChildItem $root) {
        if ((Get-ItemProperty $k.PSPath).DeviceState -ne 1) { continue }   # active only
        $p = Get-ItemProperty "$($k.PSPath)\Properties"
        $ep = [string]$p.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
        $ad = [string]$p.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
        [pscustomobject]@{ Id = '{0.0.0.00000000}.' + $k.PSChildName; Endpoint = $ep; Adapter = $ad; Friendly = "$ep ($ad)" }
    }
}
function Get-Friendly([string]$id) {
    $r = Get-Renders | Where-Object { $_.Id -eq $id } | Select-Object -First 1
    if ($r) { $r.Friendly } else { '?' }
}

switch ($Action) {
    'Get' { $cur = [TlwuAudio]::GetDefault(); "$cur|$(Get-Friendly $cur)" }
    'Set' {
        [TlwuAudio]::SetDefault($Id)
        "default -> $(Get-Friendly ([TlwuAudio]::GetDefault()))"
    }
    'SetByName' {
        $m = Get-Renders | Where-Object { $_.Friendly -match $Name } | Select-Object -First 1
        if (-not $m) { Write-Error "no active render device matches '$Name'" }
        [TlwuAudio]::SetDefault($m.Id)
        "default -> $($m.Friendly)"
    }
    'RestoreSpeakers' {
        $all = @(Get-Renders)
        $target = $null
        if ($Id) { $target = $all | Where-Object { $_.Id -eq $Id -and $_.Friendly -notmatch 'CABLE|VB-Audio|Steam' } | Select-Object -First 1 }
        if (-not $target) {
            $target = $all | Where-Object { $_.Adapter -eq 'High Definition Audio Device' -and $_.Endpoint -match '^(Speakers|\u5587\u53ED)' } | Select-Object -First 1
        }
        if (-not $target) { Write-Error 'real speakers not found; default left unchanged'; exit 1 }
        [TlwuAudio]::SetDefault($target.Id)
        "default restored -> $($target.Friendly)"
    }
}
