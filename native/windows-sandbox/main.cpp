#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <aclapi.h>
#include <algorithm>
#include <atomic>
#include <filesystem>
#include <iostream>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace fs = std::filesystem;
struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE h) : value(h) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    void reset(HANDLE h = nullptr) { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); value = h; }
};
static void check(BOOL ok, const char* label) {
    if (!ok) throw std::runtime_error(std::string(label) + ": Win32 " + std::to_string(GetLastError()));
}
static std::vector<BYTE> tokenInfo(HANDLE token, TOKEN_INFORMATION_CLASS kind) {
    DWORD size = 0;
    GetTokenInformation(token, kind, nullptr, 0, &size);
    if (!size) throw std::runtime_error("GetTokenInformation size failed");
    std::vector<BYTE> data(size);
    check(GetTokenInformation(token, kind, data.data(), size, &size), "GetTokenInformation");
    return data;
}
static void restrictToken(Handle& restricted) {
    Handle original;
    check(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE |
        TOKEN_ADJUST_DEFAULT | TOKEN_ASSIGN_PRIMARY, &original.value), "OpenProcessToken");
    auto groupsData = tokenInfo(original.value, TokenGroups);
    auto groups = reinterpret_cast<TOKEN_GROUPS*>(groupsData.data());
    PSID logon = nullptr;
    for (DWORD i = 0; i < groups->GroupCount; ++i)
        if ((groups->Groups[i].Attributes & SE_GROUP_LOGON_ID) == SE_GROUP_LOGON_ID)
            logon = groups->Groups[i].Sid;
    if (!logon) throw std::runtime_error("No logon SID; restricted token unavailable");
    BYTE everyone[SECURITY_MAX_SID_SIZE], low[SECURITY_MAX_SID_SIZE];
    DWORD size = sizeof(everyone);
    check(CreateWellKnownSid(WinWorldSid, nullptr, everyone, &size), "CreateWellKnownSid Everyone");
    size = sizeof(low);
    check(CreateWellKnownSid(WinLowLabelSid, nullptr, low, &size), "CreateWellKnownSid Low");
    SID_AND_ATTRIBUTES restricting[] = {{logon, 0}, {everyone, 0}};
    check(CreateRestrictedToken(original.value, DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED,
        0, nullptr, 0, nullptr, 2, restricting, &restricted.value), "CreateRestrictedToken");
    TOKEN_MANDATORY_LABEL label{};
    label.Label.Sid = low;
    label.Label.Attributes = SE_GROUP_INTEGRITY;
    check(SetTokenInformation(restricted.value, TokenIntegrityLevel, &label,
        sizeof(label) + GetLengthSid(low)), "SetTokenInformation Low");
    auto defaultData = tokenInfo(restricted.value, TokenDefaultDacl);
    auto defaults = reinterpret_cast<TOKEN_DEFAULT_DACL*>(defaultData.data());
    if (!defaults->DefaultDacl) throw std::runtime_error("Token default DACL absent");
    EXPLICIT_ACCESSW access{};
    access.grfAccessPermissions = FILE_ALL_ACCESS;
    access.grfAccessMode = GRANT_ACCESS;
    access.grfInheritance = NO_INHERITANCE;
    BuildTrusteeWithSidW(&access.Trustee, everyone);
    PACL acl = nullptr;
    DWORD error = SetEntriesInAclW(1, &access, defaults->DefaultDacl, &acl);
    if (error) throw std::runtime_error("SetEntriesInAclW: Win32 " + std::to_string(error));
    TOKEN_DEFAULT_DACL newDefault{acl};
    BOOL ok = SetTokenInformation(restricted.value, TokenDefaultDacl, &newDefault, sizeof(newDefault));
    DWORD last = GetLastError();
    LocalFree(acl);
    SetLastError(last);
    check(ok, "SetTokenInformation default DACL");
}
static void createJob(Handle& job) {
    job.reset(CreateJobObjectW(nullptr, nullptr));
    check(job.value != nullptr, "CreateJobObjectW");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY;
    limits.BasicLimitInformation.ActiveProcessLimit = 4;
    limits.JobMemoryLimit = 256ull * 1024 * 1024;
    check(SetInformationJobObject(job.value, JobObjectExtendedLimitInformation,
        &limits, sizeof(limits)), "SetInformationJobObject");
}
static std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (!size) throw std::runtime_error("Invalid Unicode argument");
    std::string output(size, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
        output.data(), size, nullptr, nullptr);
    return output;
}
// Keep valid complete UTF-8 sequences only; invalid/incomplete bytes cannot break JSON.
static std::string validUtf8(const std::string& value) {
    std::string output;
    for (size_t i = 0; i < value.size();) {
        unsigned char first = value[i];
        size_t n = first < 0x80 ? 1 : first >= 0xC2 && first <= 0xDF ? 2 :
            first >= 0xE0 && first <= 0xEF ? 3 : first >= 0xF0 && first <= 0xF4 ? 4 : 0;
        bool valid = n && i + n <= value.size();
        for (size_t j = 1; valid && j < n; ++j) valid = (static_cast<unsigned char>(value[i+j]) & 0xC0) == 0x80;
        if (valid && n >= 3) {
            unsigned char second = value[i+1];
            valid = !(first == 0xE0 && second < 0xA0) && !(first == 0xED && second >= 0xA0) &&
                !(first == 0xF0 && second < 0x90) && !(first == 0xF4 && second >= 0x90);
        }
        if (valid) { output.append(value, i, n); i += n; } else ++i;
    }
    return output;
}
static std::string jsonString(const std::string& input) {
    std::string output = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char ch : validUtf8(input)) {
        if (ch == '"' || ch == '\\') { output += '\\'; output += ch; }
        else if (ch < 32) { output += "\\u00"; output += hex[ch >> 4]; output += hex[ch & 15]; }
        else output += ch;
    }
    return output + "\"";
}
struct Result {
    std::string status = "failed", out, err, error;
    bool hasExit = false, truncated = false;
    DWORD exit = 0;
    void emit() const {
        std::cout << "{\"protocol\":1,\"backend\":\"windows-acl\",\"mode\":\"read-only\","
            "\"enforcement\":\"partial\",\"file_read\":\"not_isolated\",\"network\":\"not_isolated\","
            "\"status\":" << jsonString(status) << ",\"exit_code\":" <<
            (hasExit ? std::to_string(exit) : "null") << ",\"stdout\":" << jsonString(out) <<
            ",\"stderr\":" << jsonString(err) << ",\"truncated\":" << (truncated ? "true" : "false") <<
            ",\"error\":" << (error.empty() ? "null" : jsonString(error)) << "}\n";
    }
};
static std::wstring quote(const std::wstring& arg) {
    std::wstring output = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : arg) {
        if (ch == L'\\') { ++slashes; continue; }
        output.append(ch == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        output += ch;
        slashes = 0;
    }
    output.append(slashes * 2, L'\\');
    return output + L'"';
}
static int number(const std::wstring& value, int low, int high) {
    if (value.empty() || value.find_first_not_of(L"0123456789") != std::wstring::npos || value.size() > 8)
        throw std::runtime_error("Invalid numeric limit");
    int result = std::stoi(value);
    if (result < low || result > high) throw std::runtime_error("Numeric limit outside allowed range");
    return result;
}
static void rejectReparse(const fs::path& path) {
    DWORD attr = GetFileAttributesW(path.c_str());
    if (attr == INVALID_FILE_ATTRIBUTES) throw std::runtime_error("Input path inaccessible");
    if (attr & FILE_ATTRIBUTE_REPARSE_POINT) throw std::runtime_error("Reparse input paths prohibited");
}
static fs::path checkedPath(const std::wstring& value, bool directory) {
    fs::path path(value);
    if (!path.is_absolute() || value.find(L'\0') != std::wstring::npos || value.find(L':', 2) != std::wstring::npos ||
        value.rfind(L"\\\\", 0) == 0) throw std::runtime_error("Expected absolute local path");
    path = path.lexically_normal();
    for (fs::path ancestor = path; !ancestor.empty();) {
        rejectReparse(ancestor);
        fs::path parent = ancestor.parent_path();
        if (parent == ancestor) break;
        ancestor = parent;
    }
    if (directory ? !fs::is_directory(path) : !fs::is_regular_file(path))
        throw std::runtime_error("Wrong path kind");
    if (directory) for (const auto& entry : fs::recursive_directory_iterator(path)) rejectReparse(entry.path());
    return path;
}
static std::vector<wchar_t> environment() {
    // Complete explicit whitelist. No inherited PATH, profile, temp, proxy, or API secrets.
    wchar_t system[MAX_PATH];
    UINT n = GetWindowsDirectoryW(system, MAX_PATH);
    if (!n || n >= MAX_PATH) throw std::runtime_error("GetWindowsDirectoryW failed");
    std::wstring block = L"SystemRoot=" + std::wstring(system) + L'\0' + L"WINDIR=" + system + L'\0';
    block += L'\0';
    return std::vector<wchar_t>(block.begin(), block.end());
}
static void pipe(Handle& read, Handle& write) {
    SECURITY_ATTRIBUTES attrs{sizeof(attrs), nullptr, TRUE};
    check(CreatePipe(&read.value, &write.value, &attrs, 0), "CreatePipe");
    check(SetHandleInformation(read.value, HANDLE_FLAG_INHERIT, 0), "SetHandleInformation read");
}
static void search(const fs::path& rg, const fs::path& input, const std::wstring& query,
                   int limit, int timeout, int outputLimit, Handle& token, Handle& job, Result& result) {
    Handle stdoutRead, stdoutWrite, stderrRead, stderrWrite, stdinRead, stdinWrite;
    pipe(stdoutRead, stdoutWrite); pipe(stderrRead, stderrWrite); pipe(stdinRead, stdinWrite);
    check(SetHandleInformation(stdinRead.value, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT), "stdin inheritance");
    check(SetHandleInformation(stdinWrite.value, HANDLE_FLAG_INHERIT, 0), "stdin writer inheritance");
    stdinWrite.reset();  // Target stdin is EOF; helper cancellation stdin stays private.
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.StartupInfo.wShowWindow = SW_HIDE;
    startup.StartupInfo.hStdInput = stdinRead.value;
    startup.StartupInfo.hStdOutput = stdoutWrite.value;
    startup.StartupInfo.hStdError = stderrWrite.value;
    SIZE_T attrSize = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &attrSize);
    std::vector<BYTE> attrData(attrSize);
    startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attrData.data());
    check(InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &attrSize), "InitializeProcThreadAttributeList");
    struct AttributeGuard { LPPROC_THREAD_ATTRIBUTE_LIST list; ~AttributeGuard() { DeleteProcThreadAttributeList(list); } } guard{startup.lpAttributeList};
    HANDLE inherited[] = {stdinRead.value, stdoutWrite.value, stderrWrite.value};
    check(UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        inherited, sizeof(inherited), nullptr, nullptr), "UpdateProcThreadAttribute handle list");
    std::wstring command;
    for (const auto& arg : std::vector<std::wstring>{rg.wstring(), L"--no-config", L"--json", L"--fixed-strings",
            L"--max-count", std::to_wstring(limit), L"--", query, L"."}) {
        if (!command.empty()) command += L' ';
        command += quote(arg);
    }
    auto env = environment();
    PROCESS_INFORMATION info{};
    check(CreateProcessAsUserW(token.value, rg.c_str(), command.data(), nullptr, nullptr, TRUE,
        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
        env.data(), input.c_str(), &startup.StartupInfo, &info), "CreateProcessAsUserW");
    Handle process(info.hProcess), primaryThread(info.hThread);
    if (!AssignProcessToJobObject(job.value, process.value)) {
        DWORD last = GetLastError();
        TerminateProcess(process.value, 1);
        WaitForSingleObject(process.value, INFINITE);
        SetLastError(last); check(FALSE, "AssignProcessToJobObject");
    }
    stdoutWrite.reset(); stderrWrite.reset(); stdinRead.reset();
    // Thread construction / allocation failure must still kill the suspended process.
    std::mutex capture;
    std::atomic<bool> done{false}, cancelled{false}, readerFailed{false};
    std::thread outThread, errThread, cancelThread;
    auto drain = [&](HANDLE handle, std::string& dest) {
        char buffer[4096]; DWORD count = 0;
        while (ReadFile(handle, buffer, sizeof(buffer), &count, nullptr) && count) {
            std::lock_guard<std::mutex> lock(capture);
            size_t used = result.out.size() + result.err.size();
            size_t keep = std::min<size_t>(count, outputLimit - used);
            dest.append(buffer, keep);
            if (keep < count) result.truncated = true;
        }
        DWORD last = GetLastError();
        if (last != ERROR_BROKEN_PIPE && last != ERROR_SUCCESS) readerFailed = true;
    };
    auto joinAll = [&]() {
        done = true;
        if (cancelThread.joinable()) cancelThread.join();
        if (outThread.joinable()) outThread.join();
        if (errThread.joinable()) errThread.join();
    };
    try {
        outThread = std::thread(drain, stdoutRead.value, std::ref(result.out));
        errThread = std::thread(drain, stderrRead.value, std::ref(result.err));
        HANDLE cancellation = GetStdHandle(STD_INPUT_HANDLE);
        if (!cancellation || cancellation == INVALID_HANDLE_VALUE || GetFileType(cancellation) != FILE_TYPE_PIPE)
            throw std::runtime_error("Parent liveness stdin must be a pipe");
        cancelThread = std::thread([&]() {
            while (!done) {
                DWORD available = 0;
                if (!PeekNamedPipe(cancellation, nullptr, 0, nullptr, &available, nullptr)) {
                    cancelled = true; TerminateJobObject(job.value, 1); break;
                }
                // This is a liveness-only pipe. Receiving data is a protocol violation.
                if (available) { cancelled = true; TerminateJobObject(job.value, 1); break; }
                Sleep(10);
            }
        });
        if (ResumeThread(primaryThread.value) == static_cast<DWORD>(-1)) check(FALSE, "ResumeThread");
        DWORD wait = WaitForSingleObject(process.value, timeout);
        if (wait == WAIT_TIMEOUT) { result.status = "timeout"; result.error = "Search timeout"; }
        else if (wait != WAIT_OBJECT_0) check(FALSE, "WaitForSingleObject");
        // Reap any descendants before waiting for pipe EOF; no writer can keep us blocked.
        check(TerminateJobObject(job.value, 1), "TerminateJobObject cleanup");
        check(WaitForSingleObject(process.value, INFINITE) == WAIT_OBJECT_0, "WaitForSingleObject cleanup");
        check(GetExitCodeProcess(process.value, &result.exit), "GetExitCodeProcess");
        result.hasExit = true;
        joinAll();
        if (cancelled) { result.status = "failed"; result.error = "Parent liveness pipe closed or sent data"; }
        else if (readerFailed) { result.status = "failed"; result.error = "Output pipe read failed"; }
        else if (result.status != "timeout") {
            result.status = result.exit <= 1 ? "succeeded" : "failed";
            if (result.exit > 1) result.error = "rg search failed";
        }
    } catch (...) {
        TerminateJobObject(job.value, 1);
        WaitForSingleObject(process.value, INFINITE);
        joinAll();
        throw;
    }
}
int wmain(int argc, wchar_t** argv) {
    Result result;
    try {
        if (argc == 2 && std::wstring(argv[1]) == L"--probe") {
            result.status = "unavailable";
            Handle token, job;
            restrictToken(token); createJob(job);
            result.status = "succeeded";
        } else {
            if (argc != 13 || std::wstring(argv[1]) != L"--search")
                throw std::runtime_error("Expected --probe or the fixed --search protocol");
            std::map<std::wstring, std::wstring> values;
            for (int i = 3; i < argc; i += 2) {
                std::wstring key = argv[i];
                if (key != L"--input" && key != L"--query" && key != L"--limit" &&
                    key != L"--timeout-ms" && key != L"--output-limit") throw std::runtime_error("Unknown field");
                if (!values.emplace(key, argv[i+1]).second) throw std::runtime_error("Duplicate field");
            }
            fs::path rg = checkedPath(argv[2], false);
            if (_wcsicmp(rg.filename().c_str(), L"rg.exe")) throw std::runtime_error("Only trusted rg.exe supported");
            fs::path input = checkedPath(values.at(L"--input"), true);
            std::wstring inputPrefix = input.wstring();
            if (inputPrefix.back() != L'\\') inputPrefix += L'\\';
            if (_wcsnicmp(rg.c_str(), inputPrefix.c_str(), inputPrefix.size()) == 0)
                throw std::runtime_error("rg may not reside in inputs");
            const auto& query = values.at(L"--query");
            utf8(query);  // Validate Unicode before launching.
            int limit = number(values.at(L"--limit"), 1, 50);
            int timeout = number(values.at(L"--timeout-ms"), 1, 15000);
            int outputLimit = number(values.at(L"--output-limit"), 1024, 16384);
            Handle token, job;
            result.status = "unavailable";
            restrictToken(token); createJob(job);
            search(rg, input, query, limit, timeout, outputLimit, token, job, result);
        }
    } catch (const std::exception& error) { result.error = error.what(); }
    try { result.emit(); return std::cout.good() ? 0 : 2; } catch (...) { return 2; }
}
