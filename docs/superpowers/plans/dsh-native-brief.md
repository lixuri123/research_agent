# Native helper task brief

Project root D:\agent\research_agent. Own ONLY native/windows-sandbox/* and tests/test_windows_execution_native.py. No Git repo. Apply_patch edits only. User requests few tests, no live LLM or installation/OS ACL changes. Read TDD skill. Implement read-only first phase of approved dsh design.

Reference local dsh token.ts and packages/subprocess/win32-process/src/process.ts under D:\agent\deepseek-harness, read-only. Mechanism: restricted token DISABLE_MAX_PRIVILEGE|LUA_TOKEN|WRITE_RESTRICTED, restricting login SID + Everyone (no workspace write SID), Low integrity. TokenDefaultDacl merged Everyone sufficient grant for new pipes/sync objects using dsh semantics. No workspace ACL changes. Windows partial file write restriction; reads/network not isolated.

Helper CLI contract (all paths provided only by trusted Python):
`helper.exe --probe`
`helper.exe --search <absolute trusted rg.exe> --input <absolute staged inputs dir> --query <literal query> --limit <1..50> --timeout-ms <1..15000> --output-limit <1024..16384>`
Reject missing/duplicate/unknown fields, invalid limits, NUL, nonabsolute paths, rg not named rg.exe, linked/reparse input paths, rg inside inputs. Do not accept arbitrary argv/command; build rg arguments internally: --no-config --json --fixed-strings --max-count <limit> -- <query> . ; cwd inputs. No arbitrary code or network confinement claim.

Own target stdout/stderr pipes; helper stdout is ONLY one final JSON envelope, escape correctly UTF-8 (query Chinese supported). Fields: protocol=1, backend="windows-acl", mode="read-only", enforcement="partial", file_read="not_isolated", network="not_isolated", status="succeeded"|"failed"|"timeout"|"unavailable", exit_code integer or null, stdout string, stderr string, truncated bool, error string or null. Probe same envelope status=succeeded on token+Low+Job setup success; reports error otherwise, no target startup. Target exit 0 or 1 means search success. Helper own exit 0 when valid envelope even command failure, nonzero only protocol/internal irrecoverable errors.

Target environment whitelist system runtime essentials; do not inherit API keys, proxy config, NODE_OPTIONS or user config. CreateProcessAsUserW with CREATE_SUSPENDED and exact Windows quoting, inherit ONLY target stdio (control stdout must not be inherited). Job KILL_ON_JOB_CLOSE, disallow breakaway, memory cap 256 MiB and active process limit 4. Assign job before ResumeThread; terminate if assignment fails. Pipe readers drain continuously but store COMBINED stdout/stderr <= output limit, mark truncation; timeout kills job and waits/readers finish. Output should valid UTF-8 even if truncated; replace/drop incomplete bytes safely before JSON encode.

Python keeps helper stdin pipe open; monitor EOF in helper with a cancellation thread and kill Job when parent closes stdin or dies. Thread/handle lifetime must not block exit; design clean joins/cancel reads. Child cannot inherit helper stdin cancellation pipe. Helper killed also closes its noninherited Job handle and kills target. Target does not inherit Job control.

build.ps1 uses current cl SDK environment, can locate VS BuildTools/Community and vcvarsall if needed, builds exe at native/windows-sandbox/bin/research-sandbox.exe. No install/download, no visible background windows. Keep artifacts confined in native/windows-sandbox/bin.

Tests: minimal missing-helper red then native probe/search smoke with temp notes Chinese/literal dash query, unexpected flags rejection, timeout if feasible without arbitrary command exposure. Native safety boundary checks can use dedicated test helper/payload NOT exposed in production; no ACL mutation on real user files. Do not claim all boundary checks covered if only smoke. Run direct .venv Python pytest (uv cache restricted may require env UV_CACHE_DIR=$PWD\.uv-cache). Compiler cl is available; inspect Get-Command if needed.

Read-only self-review helper before returning. Write report at docs/superpowers/plans/dsh-native-report.md (exception to file ownership permitted for report), include exact test commands/results, constraints not proven. Return concise status and concerns.
