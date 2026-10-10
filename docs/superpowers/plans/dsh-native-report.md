# Native helper report

Implemented `native/windows-sandbox/main.cpp`, `build.ps1`, README, three focused tests in
`tests/test_windows_execution_native.py`, and an optional separate native diagnostic payload.
Production binary is `native/windows-sandbox/bin/research-sandbox.exe`.

The CLI/envelope follow the brief. Restricted token, Low integrity, merged default DACL,
fixed rg arguments, explicit environment/handle whitelist, suspended launch, pre-resume Job
assignment, combined bounded pipe capture, timeout and private stdin liveness cancellation
are implemented. Job has kill-on-close, no breakaway, 256 MiB aggregate memory cap and four
active processes. No installation, original workspace ACL change, Git operation, or live LLM.

Validation commands and actual results:

- `.venv/Scripts/python.exe -m pytest tests/test_windows_execution_native.py -q`
  initially failed because helper was missing (two failures); default pytest temp setup also
  hit an existing temp-directory access denial. Later runs place test temp files under bin.
- `powershell -NoProfile -ExecutionPolicy Bypass -File native/windows-sandbox/build.ps1`
  built successfully with installed MSVC, no warnings. An initial cross-drive containment bug
  failed the literal search test and was fixed with case-insensitive path-prefix containment.
- `.venv/Scripts/python.exe -m pytest tests/test_windows_execution_native.py -q --basetemp=native/windows-sandbox/bin/pytest-tmp`
  passed all 3 tests after the fix.
- `powershell -NoProfile -ExecutionPolicy Bypass -File native/windows-sandbox/build.ps1 -BoundaryDiagnostic`
  built the dedicated diagnostic payload successfully, no warnings.
- `.venv/Scripts/python.exe -m pytest tests/test_windows_execution_native.py native/windows-sandbox/boundary-smoke.py -q -s --basetemp=native/windows-sandbox/bin/pytest-tmp`
  passed all 4 tests in 0.93 seconds on final rerun after rebuilding production helper. Diagnostic printed inside/outside controlled-file write
  denial=true, injected secret absent=true, Low integrity=true, timeout=timeout,
  cancellation=failed (expected cancellation), and payloads_reaped=true.

Execution-context caveat: direct Python command/script probes sometimes report
`CreateRestrictedToken: Win32 87`, including inherited and explicit environment cases.
The same binary passes in pytest execution context. No require_escalated was used by this
agent. An early CREATE_NO_WINDOW explanation was withdrawn after default-console probes
also failed; causation has not been established. Parent is testing integration in its host
execution context and reported reviewed normal-token native3+adapter1 tests passed in 0.58
seconds. Capability errors remain fail-closed, with an unavailable envelope.

Diagnostic isolation: production main.cpp contains no boundary mode, sleep mode, or payload
implementation. Its same fixed search protocol was used with the explicitly test-owned
executable solely for diagnostics. The optional build switch changes only source/output
selection; regular builds still compile main.cpp to research-sandbox.exe. Final normal build
and combined 4-test rerun passed after adding the diagnostic build switch.

Read-only self-review completed against the brief: parser rejects unknown/duplicate/missing
fields, bounds, nonabsolute/UNC/ADS paths, reparse input ancestry and descendants, invalid
executable name, and executable containment in staged inputs. The target inherits only
its three stdio handles; token and Job stay private. No unrestricted fallback exists.
Output JSON validates/filters malformed or incomplete UTF-8 and correctly escapes controls.

Not exhaustively proven: all DACL/ambient Everyone write cases, descendants/breakaway,
resource-limit enforcement under stress, helper force-kill cleanup, every handle-inheritance
edge, reparse replacement races (trusted staged directories are assumed), and full
filesystem/object boundaries. Reads and network are explicitly not isolated. Smoke success
must not be described as complete filesystem confinement. Diagnostic binary is test-only;
the adapter must select real trusted ripgrep and never this payload.
