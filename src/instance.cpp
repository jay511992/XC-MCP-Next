#define _CRT_RAND_S
#include "instance.hpp"
#include "plug_abi.h"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <vector>

namespace instance {

std::string utf8(const wchar_t* text) {
    if (!text || !*text) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return {};
    std::string result((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, &result[0], n, nullptr, nullptr);
    result.resize((size_t)n - 1);
    return result;
}

std::wstring wide(const std::string& text) {
    if (text.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), (int)text.size(), nullptr, 0);
    if (n <= 0) throw std::runtime_error("invalid UTF-8 path");
    std::wstring result((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), (int)text.size(), &result[0], n);
    return result;
}

std::wstring environment(const wchar_t* name) {
    DWORD n = GetEnvironmentVariableW(name, nullptr, 0);
    if (!n) return {};
    std::wstring result(n, L'\0');
    DWORD got = GetEnvironmentVariableW(name, &result[0], n);
    if (!got || got >= n) return {};
    result.resize(got);
    return result;
}

namespace {

std::string trim(const std::string& s) {
    size_t first = s.find_first_not_of(" \r\n\t");
    if (first == std::string::npos) return {};
    return s.substr(first, s.find_last_not_of(" \r\n\t") - first + 1);
}

std::wstring fullPath(std::wstring path) {
    for (auto& c : path) if (c == L'/') c = L'\\';
    DWORD n = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
    if (!n) throw std::runtime_error("cannot normalize project path");
    std::wstring result(n, L'\0');
    DWORD got = GetFullPathNameW(path.c_str(), n, &result[0], nullptr);
    if (!got || got >= n) throw std::runtime_error("cannot normalize project path");
    result.resize(got);
    return result;
}

std::string randomHex(size_t bytes) {
    std::string result;
    result.reserve(bytes * 2);
    unsigned int value = 0;
    for (size_t i = 0; i < bytes; ++i) {
        if (i % 4 == 0 && rand_s(&value) != 0)
            throw std::runtime_error("secure random generator failed");
        char digits[3];
        sprintf_s(digits, "%02x", (value >> ((i % 4) * 8)) & 255u);
        result += digits;
    }
    return result;
}

std::string readText(const std::wstring& path) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                          nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        if (GetLastError() == ERROR_FILE_NOT_FOUND) return {};
        throw std::runtime_error("cannot read token file");
    }
    char data[4096];
    DWORD n = 0;
    BOOL ok = ReadFile(h, data, sizeof(data), &n, nullptr);
    CloseHandle(h);
    if (!ok || n == sizeof(data)) throw std::runtime_error("invalid token file");
    return trim(std::string(data, n));
}

// Same-directory rename publishes a complete file. Without REPLACE_EXISTING only
// one concurrently starting process wins; losers read the winner's token.
bool writeAtomic(const std::wstring& path, const std::string& data, bool replace) {
    std::wstring tmp = path + L"." + wide(randomHex(8)) + L".tmp";
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                          FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) throw std::runtime_error("cannot create instance metadata");
    DWORD written = 0;
    BOOL ok = WriteFile(h, data.data(), (DWORD)data.size(), &written, nullptr);
    if (ok && written == data.size()) ok = FlushFileBuffers(h);
    CloseHandle(h);
    if (!ok || written != data.size()) {
        DeleteFileW(tmp.c_str());
        throw std::runtime_error("cannot write instance metadata");
    }
    DWORD flags = MOVEFILE_WRITE_THROUGH | (replace ? MOVEFILE_REPLACE_EXISTING : 0);
    if (MoveFileExW(tmp.c_str(), path.c_str(), flags)) return true;
    DWORD error = GetLastError();
    DeleteFileW(tmp.c_str());
    if (!replace && (error == ERROR_ALREADY_EXISTS || error == ERROR_FILE_EXISTS)) return false;
    throw std::runtime_error("cannot publish instance metadata");
}

std::string loadToken() {
    std::string overrideToken = trim(utf8(environment(L"XC_MCP_TOKEN").c_str()));
    if (!overrideToken.empty()) return overrideToken;
    std::wstring base = environment(L"LOCALAPPDATA");
    if (base.empty()) throw std::runtime_error("LOCALAPPDATA is missing; set XC_MCP_TOKEN");
    std::filesystem::path dir = std::filesystem::path(base) / L"xc-mcp";
    std::filesystem::create_directories(dir);
    std::wstring path = (dir / L"token").wstring();
    std::string existing = readText(path);
    if (!existing.empty()) return existing;
    std::string candidate = randomHex(32);
    if (writeAtomic(path, candidate, false)) return candidate;
    existing = readText(path);
    if (existing.empty()) throw std::runtime_error("existing token file is empty; refusing to overwrite it");
    return existing;
}

struct Project {
    std::string name;
    std::string file;
    std::string dir;
};

Project project() {
    void* name = nullptr;
    void* file = nullptr;
    void* dir = nullptr;
    xi::getProjectRaw(&name, &file, &dir);
    Project p;
    p.name = utf8((const wchar_t*)name);
    p.dir = utf8((const wchar_t*)dir);
    std::wstring f = file ? (const wchar_t*)file : L"";
    if (!f.empty()) {
        std::filesystem::path path(f);
        if (path.is_relative() && !p.dir.empty()) path = std::filesystem::path(wide(p.dir)) / path;
        p.file = utf8(fullPath(path.wstring()).c_str());
    }
    return p;
}

int unhex(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

}

std::string currentProjectFile() { return project().file; }

bool matchesProject(const std::string& encoded, const std::string& actual) {
    if (encoded.empty() || actual.empty()) return false;
    std::string decoded;
    for (size_t i = 0; i < encoded.size(); ++i) {
        unsigned char c = (unsigned char)encoded[i];
        if (c == '%') {
            if (i + 2 >= encoded.size()) return false;
            int high = unhex(encoded[i + 1]), low = unhex(encoded[i + 2]);
            if (high < 0 || low < 0) return false;
            c = (unsigned char)((high << 4) | low);
            i += 2;
        }
        if (c == 0 || c == '\r' || c == '\n') return false;
        decoded += (char)c;
    }
    try {
        std::wstring expected = wide(decoded);
        if (!std::filesystem::path(expected).is_absolute()) return false;
        expected = fullPath(expected);
        std::wstring current = fullPath(wide(actual));
        return CompareStringOrdinal(expected.c_str(), (int)expected.size(), current.c_str(),
                                    (int)current.size(), TRUE) == CSTR_EQUAL;
    } catch (...) {
        return false;
    }
}

Identity::Identity(int actualPort) : port(actualPort), pid(GetCurrentProcessId()) {
    FILETIME created, exited, kernel, user;
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user))
        throw std::runtime_error("cannot identify IDE process creation time");
    unsigned long long stamp = ((unsigned long long)created.dwHighDateTime << 32) | created.dwLowDateTime;
    char text[32];
    sprintf_s(text, "%016llx", stamp);
    startedAt = text;
    id = std::to_string(pid) + "-" + startedAt;
    std::vector<wchar_t> image(32768);
    DWORD n = GetModuleFileNameW(nullptr, image.data(), (DWORD)image.size());
    if (!n || n >= image.size()) throw std::runtime_error("cannot identify IDE executable");
    ideExe = utf8(image.data());
    token = loadToken();
    registryDir_ = environment(L"XC_MCP_INSTANCE_DIR");
    if (registryDir_.empty()) {
        std::wstring base = environment(L"LOCALAPPDATA");
        if (base.empty()) throw std::runtime_error("LOCALAPPDATA is missing; set XC_MCP_INSTANCE_DIR");
        registryDir_ = (std::filesystem::path(base) / L"xc-mcp" / L"instances").wstring();
    }
    registryDir_ = fullPath(registryDir_);
    recordPath_ = (std::filesystem::path(registryDir_) / wide(id + ".json")).wstring();
    tokenPath_ = (std::filesystem::path(registryDir_) / wide(id + ".token")).wstring();
}

js::Value Identity::info() const {
    Project p = project();
    js::Value v = js::Value::obj();
    v.set("schemaVersion", 1);
    v.set("status", std::string("ok"));
    v.set("server", std::string("xc-mcp-next"));
    v.set("instanceId", id);
    v.set("pid", (int)pid);
    v.set("startedAt", startedAt);
    v.set("port", port);
    v.set("url", std::string("http://127.0.0.1:") + std::to_string(port) + "/mcp");
    v.set("ideExe", ideExe);
    v.set("hwnd", js::Value((double)(uintptr_t)xi::mainHwnd()));
    v.set("projectFile", p.file);
    v.set("projectDir", p.dir);
    v.set("projectName", p.name);
    v.set("tokenFile", utf8(tokenPath_.c_str()));
    return v;
}

void Identity::publish() {
    std::filesystem::create_directories(registryDir_);
    writeAtomic(tokenPath_, token, true);
    try {
        writeAtomic(recordPath_, info().dump(), true);
        published_ = true;
    } catch (...) {
        DeleteFileW(tokenPath_.c_str());
        throw;
    }
}

void Identity::unpublish() {
    if (!published_) return;
    DeleteFileW(recordPath_.c_str());
    DeleteFileW(tokenPath_.c_str());
    published_ = false;
}

}
