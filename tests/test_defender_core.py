import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

import radar_core as rc
from fakes import COMFY, P, meneer_snapshot

ODD = "C:\\Program Files\\O'Brien’s Tools $x `y\\a b.exe"      # apostrophe, curly quote, $, backtick, spaces


def test_dedupe_by_exe_and_interpreter_flag():
    procs = [P(1, "python.exe", exe=r"C:\Python314\python.exe"), P(2, "python.exe", exe=r"C:\PYTHON314\Python.exe"),
             P(3, "python.exe", exe=r"C:\Python314\python.exe"), P(4, "ollama.exe", exe=r"C:\o\ollama.exe"),
             P(5, "System")]                                          # no exe path -> skipped
    t = rc.defender_targets(procs)
    assert [x.name.lower() for x in t] == ["ollama.exe", "python.exe"]
    py = t[1]
    assert [pid for pid, _ in py.procs] == [1, 2, 3]
    assert py.is_interpreter and "every script" in py.warning
    assert not t[0].is_interpreter and t[0].warning == ""
    assert t[0].folder == r"C:\o"
    for name in ("node.exe", "pythonw.exe", "powershell.exe", "cmd.exe", "pwsh.exe", "python3.13.exe", "node18.exe"):
        assert rc.defender_targets([P(9, name, exe="C:\\x\\" + name)])[0].is_interpreter


def test_service_exe_paths():
    snap = meneer_snapshot()
    ol = rc.exe_paths_of_service(snap, "ollama")
    assert [x.exe for x in ol] == [r"C:\Users\Shadow\AppData\Local\Programs\Ollama\ollama.exe"]  # runner has no exe in fake
    lu = rc.exe_paths_of_service(snap, "locallyuncensored")
    assert {x.name for x in lu} == {"locally-uncensored.exe", "lu-llama-server.exe", "python.exe"}  # + its children
    comfy = rc.exe_paths_of_service(snap, "comfyui")
    assert comfy[0].exe == COMFY + r"\python_embeded\python.exe" and comfy[0].is_interpreter
    assert rc.exe_paths_of_service(snap, "nope") == []


def test_ps_single_quote():
    assert rc.ps_single_quote(r"C:\a b\x.exe") == r"'C:\a b\x.exe'"
    assert rc.ps_single_quote("O'Brien") == "'O''Brien'"
    assert rc.ps_single_quote("a’b‘c") == "'a’’b‘‘c'"
    assert rc.ps_single_quote("$env:x `n") == "'$env:x `n'"
    with pytest.raises(ValueError):
        rc.ps_single_quote("a\nb")


def test_selection_to_change_dedupes_folders():
    t = rc.defender_targets([P(1, "a.exe", exe=r"C:\App\a.exe"), P(2, "b.exe", exe=r"C:\app\b.exe"),
                             P(3, "c.exe", exe=r"D:\c.exe")])
    ch = rc.selection_to_change(t, {r"C:\App\a.exe", r"D:\c.exe"}, {r"C:\App\a.exe", r"C:\app\b.exe"})
    assert ch.process_paths == [r"C:\App\a.exe", r"D:\c.exe"]
    assert ch.folder_paths == [r"C:\App"]
    assert rc.selection_to_change(t, set(), set()).is_empty


def test_script_contains_only_quoted_paths():
    ch = rc.ExclusionChange([ODD], [r"C:\Users\Shadow\Documents\My Tools"])
    s = rc.build_apply_script(ch, r"C:\Users\Shadow\AppData\Roaming\ProcessRadar\defender-result.json")
    assert "Add-MpPreference -ExclusionProcess " + rc.ps_single_quote(ODD) in s
    assert "Add-MpPreference -ExclusionPath 'C:\\Users\\Shadow\\Documents\\My Tools'" in s
    # the raw path never appears unquoted (e.g. inside a double-quoted string)
    assert s.count(ODD) == 0
    assert "Remove-MpPreference" not in s and "\r\n" in s
    r = rc.build_remove_script([ODD], [], "C:\\r.json")
    assert "Remove-MpPreference -ExclusionProcess " + rc.ps_single_quote(ODD) in r and "Add-MpPreference" not in r
    rd = rc.build_read_script("C:\\r.json")
    assert "Add-MpPreference" not in rd and "Remove-MpPreference" not in rd and "Get-MpPreference" in rd


def _res(**kw):
    base = {"ok": True, "action": "add", "added": [], "removed": [], "errors": [], "exclusionProcess": [],
            "exclusionPath": [], "tamper": True, "elevated": True,
            "defender": {"service": True, "antivirus": True, "realtime": True, "mode": "Normal"}}
    base.update(kw)
    return "\ufeff" + json.dumps(base)                     # Windows PowerShell writes a BOM


def test_parse_success():
    r = rc.parse_defender_result(_res(added=[{"type": "process", "value": "C:\\a.exe"}],
                                      exclusionProcess=["C:\\a.exe", "C:\\old.exe"], exclusionPath="D:\\x"))
    assert r.ok and r.added == [("process", "C:\\a.exe")] and r.exclusion_path == ["D:\\x"]
    assert r.not_applied == [] and r.defender_problem == ""
    assert "Added 1 exclusion(s)" in rc.summarize_defender(r)


def test_parse_denied_non_admin():
    r = rc.parse_defender_result(_res(ok=False, elevated=False,
                                      errors=["process 'C:\\a.exe' : Operation failed with the following error: 0x800106ba"],
                                      exclusionProcess=["N/A: Must be an administrator to view exclusions"],
                                      exclusionPath=["N/A: Must be an administrator to view exclusions"]))
    assert not r.ok and r.denied and r.exclusion_process == [] and r.exclusion_path == []
    txt = rc.summarize_defender(r)
    assert "Access denied" in txt and "0x800106ba" in txt


def test_parse_policy_silently_ignored():
    r = rc.parse_defender_result(_res(added=[{"type": "process", "value": "C:\\a.exe"},
                                             {"type": "path", "value": "D:\\Tools"}],
                                      exclusionProcess=["C:\\A.EXE"], exclusionPath=[]))
    assert not r.ok and r.not_applied == [("path", "D:\\Tools")]
    assert "NOT in the exclusion list" in rc.summarize_defender(r)


def test_parse_defender_off_or_passive():
    r = rc.parse_defender_result(_res(defender={"service": True, "antivirus": True, "mode": "Passive Mode"}))
    assert "another antivirus" in r.defender_problem
    r = rc.parse_defender_result(_res(defender={"service": False, "antivirus": False, "mode": "Not running"}))
    assert "turned off" in r.defender_problem
    r = rc.parse_defender_result(_res(ok=False, defender=None,
                                      errors=["Microsoft Defender is not available: Invalid class"]))
    assert "not available" in r.defender_problem and "not available" in rc.summarize_defender(r)


def test_exe_in_exclusions():
    assert rc.exe_in_exclusions(r"C:\O\ollama.exe", [r"c:\o\OLLAMA.exe"], [])
    assert rc.exe_in_exclusions(r"C:\O\ollama.exe", ["ollama.exe"], [])          # bare-name process exclusion
    assert rc.exe_in_exclusions(COMFY + r"\python_embeded\python.exe", [], [COMFY + "\\"])
    assert not rc.exe_in_exclusions(r"C:\Other\python.exe", [], [COMFY])
    assert not rc.exe_in_exclusions(r"C:\ComfyUI2\x.exe", [], [r"C:\ComfyUI"])
    assert not rc.exe_in_exclusions("", ["x"], ["y"])


# ---- end-to-end: run the generated script in real PowerShell with STUB Defender cmdlets.
# Functions take precedence over cmdlets, so this never touches the real Defender settings.
STUBS = r"""
$global:MP_P = [System.Collections.ArrayList]@('C:\existing.exe'); $global:MP_D = [System.Collections.ArrayList]@()
function Get-MpComputerStatus { [pscustomobject]@{ IsTamperProtected = $true; AMServiceEnabled = $true;
    AntivirusEnabled = $true; RealTimeProtectionEnabled = $true; AMRunningMode = 'Normal' } }
function Add-MpPreference { param([string[]]$ExclusionProcess, [string[]]$ExclusionPath)
    if ($env:STUB_DENY) { throw 'Operation failed with the following error: 0x800106ba' }
    foreach ($x in $ExclusionProcess) { [void]$global:MP_P.Add($x) }; foreach ($x in $ExclusionPath) { [void]$global:MP_D.Add($x) } }
function Remove-MpPreference { param([string[]]$ExclusionProcess, [string[]]$ExclusionPath)
    foreach ($x in $ExclusionProcess) { $global:MP_P.Remove($x) }; foreach ($x in $ExclusionPath) { $global:MP_D.Remove($x) } }
function Get-MpPreference { if ($env:STUB_DENY) { return [pscustomobject]@{ ExclusionProcess = @('N/A: Must be an administrator to view exclusions'); ExclusionPath = @('N/A: Must be an administrator to view exclusions') } }
    [pscustomobject]@{ ExclusionProcess = @($global:MP_P); ExclusionPath = @($global:MP_D) } }
"""


def _powershell():
    for c in (os.environ.get("PR_PWSH"), "/workspace/tools/pwsh/pwsh", shutil.which("pwsh"), shutil.which("powershell")):
        if c and os.path.exists(c):
            return c
    return None


def _run(script, deny=False, extra=""):
    ps = _powershell()
    if not ps:
        pytest.skip("no PowerShell available")
    d = tempfile.mkdtemp()
    res = os.path.join(d, "result.json")
    body = script(res)
    path = os.path.join(d, "s.ps1")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(STUBS + extra + body)
    env = dict(os.environ)
    env.pop("STUB_DENY", None)
    if deny:
        env["STUB_DENY"] = "1"
    subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path], env=env,
                   capture_output=True, timeout=120, check=False)
    with open(res, encoding="utf-8-sig") as f:
        return rc.parse_defender_result(f.read())


def test_powershell_roundtrip_add_with_odd_paths():
    folder = "D:\\My ‘Tools’ & O'Neil"
    r = _run(lambda res: rc.build_apply_script(rc.ExclusionChange([ODD], [folder]), res))
    assert r.ok, r.raw
    assert r.added == [("process", ODD), ("path", folder)]
    assert ODD in r.exclusion_process and "C:\\existing.exe" in r.exclusion_process
    assert r.exclusion_path == [folder] and r.not_applied == [] and r.tamper is True


def test_powershell_roundtrip_remove():
    pre = "Add-MpPreference -ExclusionProcess " + rc.ps_single_quote(ODD) + "\r\n"
    r = _run(lambda res: rc.build_remove_script([ODD], [], res), extra=pre)
    assert r.ok, r.raw
    assert r.removed == [("process", ODD)] and ODD not in r.exclusion_process


def test_powershell_denied():
    r = _run(lambda res: rc.build_apply_script(rc.ExclusionChange([ODD], []), res), deny=True)
    assert not r.ok and r.denied and r.added == []
    assert "0x800106ba" in " ".join(r.errors)
