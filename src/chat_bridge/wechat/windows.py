"""Windows-only identity checks and current-user DPAPI credential loading."""

import base64
import ctypes
import json
import os
import subprocess
from ctypes import wintypes

from chat_bridge.wechat.errors import GatewayError


def unprotect_credential(value: str) -> str:
    if os.name != "nt" or not isinstance(value, str) or not value.startswith("userdpapi:"):
        raise GatewayError("windows_dpapi_required")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    try:
        data = base64.b64decode(value.split(":", 1)[1], validate=True)
        if not data or len(data) > 16384:
            raise ValueError()
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source, output = Blob(len(data), buffer), Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise ValueError()
        try:
            return ctypes.string_at(output.data, output.size).decode("ascii")
        finally:
            kernel.LocalFree(output.data)
    except (ValueError, OSError, UnicodeError):
        raise GatewayError("reader_credential_unavailable") from None


def verify_native_process(config) -> int:
    if os.name != "nt":
        raise GatewayError("windows_required")
    # No values are interpolated into executable code. Configuration is JSON on stdin.
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$cfg = [Console]::In.ReadToEnd() | ConvertFrom-Json
$listeners = @(Get-NetTCPConnection -LocalPort 30001 -State Listen)
if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') { throw 'listener_mismatch' }
$process = Get-Process -Id $listeners[0].OwningProcess
if ($process.Path -ine $cfg.expectedExecutable) { throw 'executable_mismatch' }
$core = @($process.Modules | Where-Object { $_.ModuleName -ieq 'Weixin.dll' })
$expectedCore = Join-Path (Split-Path $cfg.expectedExecutable) '4.1.10.27\Weixin.dll'
if ($core.Count -ne 1 -or $core[0].FileVersionInfo.FileVersion -ne '4.1.10.27' -or $core[0].FileName -ine $expectedCore) { throw 'core_mismatch' }
$hookPath = Join-Path (Split-Path $cfg.expectedExecutable) 'version.dll'
$hook = @($process.Modules | Where-Object { $_.ModuleName -ieq 'version.dll' -and $_.FileName -ieq $hookPath })
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $hookHash = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($hookPath))).Replace('-', '') }
finally { $sha.Dispose() }
if ($hook.Count -ne 1 -or $hookHash -ine $cfg.hookSha256) { throw 'hook_mismatch' }
$boxArg = '/box:' + $cfg.sandbox
$listing = & 'C:\Program Files\Sandboxie-Plus\Start.exe' $boxArg /listpids | Out-String
$ids = @($listing -split '\s+' | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ })
if ($process.Id -notin $ids) { throw 'sandbox_mismatch' }
[pscustomobject]@{pid=$process.Id} | ConvertTo-Json -Compress
'''
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=json.dumps({"expectedExecutable": str(config.expected_executable), "sandbox": config.sandbox,
                              "hookSha256": config.hook_sha256}),
            capture_output=True, encoding="utf-8", timeout=20, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        pid = json.loads(result.stdout)["pid"]
        if type(pid) is not int or pid <= 0:
            raise ValueError()
        return pid
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        raise GatewayError("native_process_identity_check_failed") from None
