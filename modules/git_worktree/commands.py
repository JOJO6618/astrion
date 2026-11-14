"""Quote Git arguments for the execution platform, without interpolating shell code."""
from __future__ import annotations
import base64
import shlex
import subprocess


def git_command(args: list[str], *, windows_direct: bool = False, stdout_file: str | None = None) -> str:
    return process_command(['git', '-c', 'core.quotepath=false', *args],
                           windows_direct=windows_direct, stdout_file=stdout_file)


def process_command(argv: list[str], *, windows_direct: bool = False, stdout_file: str | None = None) -> str:
    if not windows_direct:
        return shlex.join(argv) + (' > ' + shlex.quote(stdout_file) if stdout_file else '')
    # cmd.exe cannot safely quote arbitrary %, &, ^ and quotes using list2cmdline.
    # Pass only base64 through cmd; ProcessStartInfo invokes git without a shell.
    arguments = subprocess.list2cmdline(argv[1:]).replace("'", "''")
    program = argv[0].replace("'", "''")
    output = ("[IO.File]::WriteAllText('" + stdout_file.replace("'", "''") + "',$o.Result,(New-Object Text.UTF8Encoding($false)));") if stdout_file else '[Console]::Out.Write($o.Result);'
    script = (
        "$ErrorActionPreference='Stop';"
        "$p=New-Object System.Diagnostics.Process;"
        f"$p.StartInfo.FileName='{program}';"
        f"$p.StartInfo.Arguments='{arguments}';"
        "$p.StartInfo.UseShellExecute=$false;"
        "$p.StartInfo.RedirectStandardOutput=$true;"
        "$p.StartInfo.RedirectStandardError=$true;"
        "$p.StartInfo.CreateNoWindow=$true;"
        "$p.StartInfo.StandardOutputEncoding=[Text.Encoding]::UTF8;"
        "$p.StartInfo.StandardErrorEncoding=[Text.Encoding]::UTF8;"
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
        "$null=$p.Start();"
        "$o=$p.StandardOutput.ReadToEndAsync();"
        "$e=$p.StandardError.ReadToEndAsync();"
        "$p.WaitForExit();"
        + output + "[Console]::Error.Write($e.Result);"
        "exit $p.ExitCode"
    )
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return 'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded
