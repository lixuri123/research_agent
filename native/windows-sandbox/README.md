# Native Windows search helper

Build with `powershell -NoProfile -ExecutionPolicy Bypass -File native/windows-sandbox/build.ps1`.
The script uses an existing Visual Studio C++ SDK, optionally discovered through vswhere;
it never installs software. Compiler outputs stay in `bin/`.

Production entrypoints are `--probe` and the fixed `--search` protocol documented in
`docs/superpowers/plans/dsh-native-brief.md`. Python supplies the trusted absolute rg executable,
staged input directory, literal query and bounded limits. The helper builds rg arguments itself.
It emits exactly one protocol-1 JSON result; valid result envelopes use helper exit code 0.

The parent must supply a private stdin pipe and keep its write end open while waiting.
EOF or incoming data cancels the target Job. Target stdio are separate pipes, with an explicit
inheritance list. Output is drained continuously and stored under one combined byte bound.
Timeout/cancellation terminates the Job and joins readers before emitting a result.

This is the approved **partial read-only Windows ACL mechanism**: restricted token with
DISABLE_MAX_PRIVILEGE, LUA_TOKEN and WRITE_RESTRICTED, restricting logon SID and Everyone,
Low integrity, and a default-DACL Everyone grant for newly created pipe/synchronization objects.
No workspace ACLs are modified. Ambient Everyone/logon grants may permit some writes.
File reads and network access are not isolated. The Job prohibits breakaway, allows at most
four processes, caps aggregate committed memory at 256 MiB, and kills members on handle close.

Native capability probes can fail closed when the host already applies an incompatible
restricted token. On this machine some direct sandboxed Python commands report
CreateRestrictedToken Win32 87, while the pytest execution context succeeds. The probe is
therefore authoritative for each execution context. Hidden startup uses STARTUPINFO SW_HIDE;
the helper target preserves console inheritance as in dsh.

Normal smoke checks:

```
.venv/Scripts/python.exe -m pytest tests/execution/test_windows_execution_native.py -q --basetemp=native/windows-sandbox/bin/pytest-tmp
```

The optional `-BoundaryDiagnostic` build compiles a dedicated test payload as
`bin/boundary/rg.exe`. This is not ripgrep and must never be supplied by the production adapter.
The production helper has no diagnostic command or arbitrary argv support. Controlled test
files are created only under `bin/`; no original files or ACLs are changed.

```
powershell -NoProfile -ExecutionPolicy Bypass -File native/windows-sandbox/build.ps1 -BoundaryDiagnostic
.venv/Scripts/python.exe -m pytest tests/execution/test_windows_execution_native.py native/windows-sandbox/boundary-smoke.py -q -s --basetemp=native/windows-sandbox/bin/pytest-tmp
```

These checks cover token/Job setup, Unicode literal dash queries, bounded output, unknown
flags, Low integrity, denied writes to two controlled medium-integrity files, secret removal,
timeout and stdin cancellation with payload exit verification. They do not exhaustively prove
all filesystem/object boundary cases, handle inheritance, descendants, memory/active-process
limit enforcement, helper force-kill cleanup, or ambient-write cases.
