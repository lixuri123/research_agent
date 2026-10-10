// Diagnostic-only executable. Build as bin/boundary/rg.exe; never distribute as trusted rg.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <iostream>
#include <string>
int wmain(int argc, wchar_t** argv) {
    std::wstring mode = argc > 7 ? argv[7] : L"";
    if (mode == L"sleep") {
        std::cout << GetCurrentProcessId() << std::endl;
        Sleep(10000);
        return 0;
    }
    HANDLE file = CreateFileW(L"existing.txt", GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    bool denied = file == INVALID_HANDLE_VALUE && GetLastError() == ERROR_ACCESS_DENIED;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    file = CreateFileW(L"../outside.txt", GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    bool outsideDenied = file == INVALID_HANDLE_VALUE && GetLastError() == ERROR_ACCESS_DENIED;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    wchar_t buffer[4096];
    bool secretAbsent = GetEnvironmentVariableW(L"NATIVE_BOUNDARY_SECRET", buffer, 4096) == 0;
    HANDLE token = nullptr;
    DWORD size = 0;
    bool low = false;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &size);
        if (size <= sizeof(buffer) && GetTokenInformation(token, TokenIntegrityLevel, buffer, sizeof(buffer), &size)) {
            PSID sid = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buffer)->Label.Sid;
            low = *GetSidSubAuthority(sid, *GetSidSubAuthorityCount(sid) - 1) == SECURITY_MANDATORY_LOW_RID;
        }
        CloseHandle(token);
    }
    std::cout << "{\"inside_write_denied\":" << (denied ? "true" : "false")
        << ",\"outside_write_denied\":" << (outsideDenied ? "true" : "false")
        << ",\"secret_absent\":" << (secretAbsent ? "true" : "false")
        << ",\"low_integrity\":" << (low ? "true" : "false") << "}\n";
    return denied && outsideDenied && secretAbsent && low ? 0 : 2;
}
