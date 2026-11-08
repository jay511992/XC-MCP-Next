#include "tools.hpp"
#include "plug_abi.h"
#include "xcgui_min.h"
#include "http.hpp"
#include "mcp_log.hpp"
#include "dialogs.hpp"

#include <windows.h>
#include <string>
#include <vector>
#include <set>
#include <map>
#include <cstdio>
#include <cstring>

using xi::Data;
using xi::HELEx;
using xi::PANE_OUTPUT_DEBUG;
using xi::PANE_OUTPUT_DEBUG_EDIT;
using xi::getHele;
using xi::getProjectRaw;
using xi::debugAddString;
using xi::logLine;
using xi::g_funSet;
using xi::g_hMainFrame;
using xi::g_hSelf;
using xi::g_ver;

namespace tools {

namespace {


class SrwGuard {
public:
    SrwGuard(SRWLOCK& lock, bool exclusive) : lock_(lock), exclusive_(exclusive) {
        if (exclusive_) AcquireSRWLockExclusive(&lock_);
        else AcquireSRWLockShared(&lock_);
    }
    ~SrwGuard() {
        if (exclusive_) ReleaseSRWLockExclusive(&lock_);
        else ReleaseSRWLockShared(&lock_);
    }
    SrwGuard(const SrwGuard&) = delete;
    SrwGuard& operator=(const SrwGuard&) = delete;
private:
    SRWLOCK& lock_;
    bool exclusive_;
};

std::string w2u(const wchar_t* w) {
    if (!w) return std::string();
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return std::string();
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
    s.resize((size_t)n - 1);
    return s;
}

std::wstring u2w(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

std::string lowerAscii(const std::string& s) {
    std::string r = s;
    for (size_t i = 0; i < r.size(); ++i) {
        if (r[i] >= 'A' && r[i] <= 'Z') r[i] = (char)(r[i] - 'A' + 'a');
    }
    return r;
}

// hadBom：磁盘原始文件是否带 UTF-8 BOM（返回值里的内容已剥离 BOM）。
bool readFileUtf8(const std::wstring& path, std::string& out, std::string& err,
                  bool* hadBom = nullptr) {
    if (hadBom) *hadBom = false;
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        err = "打开文件失败, err=" + std::to_string(GetLastError());
        return false;
    }
    LARGE_INTEGER size;
    if (!GetFileSizeEx(h, &size)) {
        CloseHandle(h);
        err = "取文件大小失败";
        return false;
    }
    if (size.QuadPart > 64ll * 1024 * 1024) {
        CloseHandle(h);
        err = "文件超过 64MB，拒绝读取";
        return false;
    }
    out.assign((size_t)size.QuadPart, '\0');
    DWORD read = 0;
    BOOL ok = out.empty() ? TRUE : ReadFile(h, &out[0], (DWORD)out.size(), &read, nullptr);
    CloseHandle(h);
    if (!ok) {
        err = "读取失败";
        return false;
    }
    out.resize(read);
    if (out.size() >= 3 && (unsigned char)out[0] == 0xEF &&
        (unsigned char)out[1] == 0xBB && (unsigned char)out[2] == 0xBF) {
        out.erase(0, 3);          // 原地前移，不再 substr 复制整份内容
        if (hadBom) *hadBom = true;
    }
    return true;
}

std::string ownDir() {
    wchar_t path[MAX_PATH * 2];
    HMODULE self = g_hSelf ? (HMODULE)g_hSelf : nullptr;
    DWORD n = GetModuleFileNameW(self, path, MAX_PATH * 2);
    if (n == 0) return std::string();
    std::wstring p(path, n);
    size_t pos = p.find_last_of(L"\\/");
    if (pos != std::wstring::npos) p = p.substr(0, pos);
    return w2u(p.c_str());
}

bool ideWritableAt(long long rva, size_t len, std::string* why) {
    HMODULE base = GetModuleHandleW(nullptr);
    if (!base) { if (why) *why = "取不到 IDE 主模块基址"; return false; }

    const IMAGE_DOS_HEADER* dos = (const IMAGE_DOS_HEADER*)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) { if (why) *why = "主模块 DOS 头无效"; return false; }
    const IMAGE_NT_HEADERS64* nt = (const IMAGE_NT_HEADERS64*)((const unsigned char*)base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) { if (why) *why = "主模块 PE 头无效"; return false; }
    if (rva < 0 || (unsigned long long)rva + len > (unsigned long long)nt->OptionalHeader.SizeOfImage) {
        if (why) *why = "目标偏移超出主模块映像范围（IDE 版本不匹配）";
        return false;
    }

    unsigned char* p = (unsigned char*)base + rva;
    unsigned char* end = p + len;
    unsigned char* cur = p;
    while (cur < end) {
        MEMORY_BASIC_INFORMATION mbi;
        if (VirtualQuery(cur, &mbi, sizeof(mbi)) == 0) { if (why) *why = "VirtualQuery 失败"; return false; }
        if (mbi.State != MEM_COMMIT) { if (why) *why = "目标内存未提交"; return false; }
        DWORD prot = mbi.Protect;
        if (prot & PAGE_GUARD) { if (why) *why = "目标内存带 PAGE_GUARD"; return false; }
        if (prot & PAGE_NOACCESS) { if (why) *why = "目标内存 PAGE_NOACCESS"; return false; }
        bool wr = (prot == PAGE_READWRITE || prot == PAGE_WRITECOPY ||
                   prot == PAGE_EXECUTE_READWRITE || prot == PAGE_EXECUTE_WRITECOPY);
        if (!wr) { if (why) *why = "目标内存不可写"; return false; }
        unsigned char* regionEnd = (unsigned char*)mbi.BaseAddress + mbi.RegionSize;
        cur = (regionEnd > cur && regionEnd < end) ? regionEnd : end;
    }
    return true;
}

std::string ideImageTag() {
    HMODULE base = GetModuleHandleW(nullptr);
    if (!base) return "";
    const IMAGE_DOS_HEADER* dos = (const IMAGE_DOS_HEADER*)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return "";
    const IMAGE_NT_HEADERS64* nt = (const IMAGE_NT_HEADERS64*)((const unsigned char*)base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return "";
    char buf[64];
    sprintf_s(buf, sizeof(buf), "ts=%08X,image=%X", nt->FileHeader.TimeDateStamp, nt->OptionalHeader.SizeOfImage);
    return buf;
}

int readParseCount() {
    std::string why;
    if (!ideWritableAt(0x4B83EC, sizeof(int), &why)) return -1;
    HMODULE base = GetModuleHandleW(nullptr);
    const unsigned char* p = (const unsigned char*)base + 0x4B83EC;
    int v = 0;
    memcpy(&v, p, sizeof(v));
    return v;
}

bool writeParseCount(int v, std::string* why) {
    if (!ideWritableAt(0x4B83EC, sizeof(int), why)) return false;
    HMODULE base = GetModuleHandleW(nullptr);
    unsigned char* p = (unsigned char*)base + 0x4B83EC;
    memcpy(p, &v, sizeof(v));
    return true;
}

std::string jsonErr(const std::string& msg) {
    js::Value v = js::Value::obj();
    v.set("ok", false);
    v.set("error", msg);
    return v.dump();
}

void putInt(js::Value& v, const char* k, int n) {
    v.set(std::string(k), js::Value((double)n));
}

void putBool(js::Value& v, const char* k, bool b) {
    v.set(std::string(k), js::Value(b));
}

void putNull(js::Value& v, const char* k) {
    v.set(std::string(k), js::Value());
}

void putStr(js::Value& v, const char* k, const std::string& s) {
    v.set(std::string(k), js::Value(s));
}

void putNum(js::Value& v, const char* k, double d) {
    v.set(std::string(k), js::Value(d));
}

}

// tools/list 里的工具数量，list() 首次构建时填充；xc_status 直接读它，避免重建整份列表。
int g_toolCount = 0;

// 模块解析缓存统计（xc_status 可见，便于确认缓存真的命中）。
LONG g_modCacheHits = 0;
LONG g_modCacheMisses = 0;

js::Value list() {
    js::Value arr = js::Value::arr();

    // Optional description: emitted only when it carries information the
    // schema cannot express (keep tools/list payload small, saves AI tokens).
    struct Def {
        const char* name;
        const char* desc;
        const char* schema;
    };

    // description 只保留 schema 无法表达的约束/枚举提示；空串表示不输出该字段。
    // 这样可以显著缩小 tools/list 载荷（每轮对话省 token），且不丢任何参数语义。
    static const Def defs[] = {
        {"xc_status", "", "{\"type\":\"object\"}"},
        {"xc_get_project", "", "{\"type\":\"object\"}"},
        {"xc_parse_status", "", "{\"type\":\"object\"}"},
        {"xc_reset_parse_count", "", "{\"type\":\"object\"}"},
        {"xc_server", "start|stop|status",
         "{\"type\":\"object\",\"properties\":{\"action\":{\"type\":\"string\",\"enum\":[\"start\",\"stop\",\"status\"]}}}"},
        {"xc_ide_log", "写一行到 IDE 面板",
         "{\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\"}},\"required\":[\"text\"]}"},
        {"xc_build", "", "{\"type\":\"object\"}"},
        {"xc_menu_cmd", "compile|recompile|compile_all",
         "{\"type\":\"object\",\"properties\":{\"command\":{\"type\":\"string\",\"enum\":[\"compile\",\"recompile\",\"compile_all\"]},\"menu_id\":{\"type\":\"integer\"}}}"},
        {"xc_get_output", "调试输出窗格",
         "{\"type\":\"object\",\"properties\":{\"tailLines\":{\"type\":\"integer\"},\"clear\":{\"type\":\"boolean\"},\"maxChars\":{\"type\":\"integer\"}}}"},
        {"xc_get_dialogs", "当前错误提示弹窗;只读,无历史",
         "{\"type\":\"object\",\"properties\":{\"maxChars\":{\"type\":\"integer\",\"default\":16000,\"minimum\":128,\"maximum\":65536}},\"additionalProperties\":false}"},
        {"xc_save_all", "", "{\"type\":\"object\"}"},
        {"xc_read_file", "读文本;限工程目录",
         "{\"type\":\"object\",\"properties\":{\"path\":{\"type\":\"string\"},\"allowOutsideProject\":{\"type\":\"boolean\"}},\"required\":[\"path\"]}"},
        {"xc_editor_get", "", "{\"type\":\"object\"}"},
        {"xc_editor_set", "写盘;留BOM/CRLF",
         "{\"type\":\"object\",\"properties\":{\"text\":{\"type\":\"string\"},\"path\":{\"type\":\"string\"},\"bom\":{\"type\":\"string\"},\"allowOutsideProject\":{\"type\":\"boolean\"}},\"required\":[\"text\"]}"},
        {"xc_new_file", "按type建模板",
         "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"type\":{\"type\":\"string\"}},\"required\":[\"name\"]}"},
        {"xc_add_file", "",
         "{\"type\":\"object\",\"properties\":{\"path\":{\"type\":\"string\"},\"open\":{\"type\":\"boolean\"}}}"},
        {"xc_add_dir_files", "按扩展名批量加",
         "{\"type\":\"object\",\"properties\":{\"dir\":{\"type\":\"string\"},\"ext\":{\"type\":\"string\"},\"open\":{\"type\":\"boolean\"}}}"},
        {"xc_list_projects", "",
         "{\"type\":\"object\",\"properties\":{\"root\":{\"type\":\"string\"}},\"required\":[\"root\"]}"},
        {"xc_open_in_ide", "",
         "{\"type\":\"object\",\"properties\":{\"path\":{\"type\":\"string\"}},\"required\":[\"path\"]}"},
        {"xc_tree_probe", "dump|select|clean",
         "{\"type\":\"object\",\"properties\":{\"action\":{\"type\":\"string\"},\"name\":{\"type\":\"string\"}}}"},
        {"xc_symbol", "",
         "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"path\":{\"type\":\"string\"},\"lineno\":{\"type\":\"integer\"}}}"},
        {"xc_goto_function", "",
         "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"root\":{\"type\":\"string\"}},\"required\":[\"name\"]}"},
        {"xc_list_functions", "",
         "{\"type\":\"object\",\"properties\":{\"filter\":{\"type\":\"string\"},\"root\":{\"type\":\"string\"}}}"},
        {"xc_module_info", "",
         "{\"type\":\"object\",\"properties\":{\"path\":{\"type\":\"string\"},\"name\":{\"type\":\"string\"}}}"},
        {"xc_module_list", "",
         "{\"type\":\"object\",\"properties\":{\"limit\":{\"type\":\"integer\"},\"all\":{\"type\":\"boolean\"},\"projectOnly\":{\"type\":\"boolean\"},\"withDeps\":{\"type\":\"boolean\"}}}"},
        {"xc_module_search", "dependsOn反查依赖",
         "{\"type\":\"object\",\"properties\":{\"query\":{\"type\":\"string\"},\"dependsOn\":{\"type\":\"string\"},\"limit\":{\"type\":\"integer\"},\"all\":{\"type\":\"boolean\"}}}"},
        {"xc_module_add", "", "{}"},
        {"xc_module_remove", "",
         "{\"type\":\"object\",\"properties\":{\"file\":{\"type\":\"string\"},\"name\":{\"type\":\"string\"},\"project\":{\"type\":\"string\"}}}"},
        {"xc_module_create", "生成module_xxx.h/.cpp",
         "{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"},\"alias\":{\"type\":\"string\"},\"author\":{\"type\":\"string\"},\"remark\":{\"type\":\"string\"},\"dir\":{\"type\":\"string\"},\"subdir\":{\"type\":\"string\"},\"location\":{\"type\":\"string\",\"enum\":[\"project\",\"dev\",\"main\"]},\"libs\":{\"type\":\"string\"},\"libs86\":{\"type\":\"string\"},\"dlls\":{\"type\":\"string\"},\"dlls86\":{\"type\":\"string\"},\"includes\":{\"type\":\"string\"},\"addToProject\":{\"type\":\"boolean\"},\"force\":{\"type\":\"boolean\"}},\"required\":[\"name\"]}"},
        {"xc_probe_cmd", "需allow_unsafe+confirm",
         "{\"type\":\"object\",\"properties\":{\"cmd_id\":{\"type\":\"integer\"},\"arg0\":{\"type\":\"integer\"},\"allow_unsafe\":{\"type\":\"boolean\"},\"confirm\":{\"type\":\"string\"}},\"required\":[\"cmd_id\"]}"},
    };
    const size_t kDefCount = sizeof(defs) / sizeof(defs[0]);

    for (size_t i = 0; i < kDefCount; ++i) {
        js::Value t = js::Value::obj();
        putStr(t, "name", defs[i].name);
        if (defs[i].desc && defs[i].desc[0]) putStr(t, "description", defs[i].desc);
        js::Value schema;
        std::string e;
        if (js::parse(defs[i].schema, schema, &e)) {
            t.set(std::string("inputSchema"), schema);
        } else {
            js::Value empty = js::Value::obj();
            putStr(empty, "type", "object");
            t.set(std::string("inputSchema"), empty);
        }
        arr.push(t);
    }
    g_toolCount = (int)kDefCount;
    return arr;
}

namespace {
// tools/list 的定义是编译期常量，序列化结果永不变更：
// 这里只构建一次并缓存原始 JSON 文本，后续请求直接内联（省掉各工具 schema 的重复解析与序列化）。
struct ListCache {
    SRWLOCK     lock;   // SRWLOCK_INIT 是常量初始化，无需额外 init
    bool        inited;
    std::string json;   // "tools" 数组的已编码 JSON 文本
};

ListCache g_listCache = { SRWLOCK_INIT, false, std::string() };

} // namespace

js::Value listCached() {
    {
        SrwGuard guard(g_listCache.lock, false);
        if (g_listCache.inited) return js::Value::makeRaw(std::string(g_listCache.json));
    }
    SrwGuard guard(g_listCache.lock, true);
    if (!g_listCache.inited) {
        js::Value arr = list();
        g_listCache.json = arr.dump();
        g_listCache.inited = true;
    }
    return js::Value::makeRaw(std::string(g_listCache.json));
}

// 工具数量：必要时才建立缓存，保证 xc_status 在 tools/list 之前调用也拿到正确值。
int toolCount() {
    {
        SrwGuard guard(g_listCache.lock, false);
        if (g_listCache.inited) return g_toolCount;
    }
    listCached();
    SrwGuard guard(g_listCache.lock, false);
    return g_toolCount;
}

static std::string doStatus() {
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "server", "xc-mcp-next");
    putStr(v, "version", "1.2.0");
    putInt(v, "ver", g_ver);
    putNum(v, "funSet", (double)(uintptr_t)g_funSet);
    putNum(v, "hMainFrame", (double)(uintptr_t)g_hMainFrame);
    putInt(v, "port", http::port());
    putStr(v, "instanceId", http::instanceId());
    putInt(v, "pid", (int)GetCurrentProcessId());
    putBool(v, "httpRunning", http::running());
    putInt(v, "toolCount", toolCount());
    {
        mcp_log::Stats log = mcp_log::stats();
        putBool(v, "mcpLogPane", log.pane);
        putBool(v, "mcpLogVisible", log.visible);
        putInt(v, "mcpLogRows", log.rows);
        putInt(v, "mcpLogPending", (int)log.pending);
        putNum(v, "mcpLogExchanges", (double)log.exchanges);
        putNum(v, "mcpLogLines", (double)log.lines);
        putNum(v, "mcpLogDropped", (double)log.dropped);
    }
    putStr(v, "abi", "3.3.9");
    putStr(v, "pluginDir", ownDir());
    putStr(v, "ideImage", ideImageTag());
    putInt(v, "modCacheHits", (int)InterlockedCompareExchange(&g_modCacheHits, 0, 0));
    putInt(v, "modCacheMisses", (int)InterlockedCompareExchange(&g_modCacheMisses, 0, 0));
    {
        std::string gw;
        putBool(v, "parseCountWritable", ideWritableAt(0x4B83EC, sizeof(int), &gw));
        if (!gw.empty()) putStr(v, "parseCountBlockReason", gw);
    }
    return v.dump();
}

static std::string doGetProject() {
    void* p1 = nullptr;
    void* p2 = nullptr;
    void* p3 = nullptr;
    getProjectRaw(&p1, &p2, &p3);
    if (!p1 && !p2 && !p3) {
        return jsonErr("getProject 未返回数据（IDE 可能尚未完成初始化）");
    }
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "name", w2u((const wchar_t*)p1));
    putStr(v, "project_file", w2u((const wchar_t*)p2));
    putStr(v, "dir", w2u((const wchar_t*)p3));
    return v.dump();
}

static std::string doParseStatus() {
    int n = readParseCount();
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putInt(v, "count", n);
    putBool(v, "parsing", n > 0);
    if (n > 0) putStr(v, "message", "代码解析中,请稍后!");
    if (n < 0) putStr(v, "warning", "解析计数器为负值，异常漂移");
    return v.dump();
}

static std::string doResetParse() {
    int before = readParseCount();
    std::string why;
    bool wrote = writeParseCount(0, &why);
    int after = readParseCount();

    if (!wrote) {
        js::Value e = js::Value::obj();
        putBool(e, "ok", false);
        putBool(e, "wrote", false);
        putStr(e, "error", std::string("拒绝写入宿主内存: ") + why);
        putStr(e, "ideImage", ideImageTag());
        putStr(e, "note", "该工具直接写 IDE 主模块偏移 0x4B83EC，已加映像边界 + VirtualQuery 可写门禁；不匹配时不做任何写入。");
        return e.dump();
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putBool(v, "wrote", true);
    putInt(v, "before", before);
    putInt(v, "after", after);
    putStr(v, "ideImage", ideImageTag());
    return v.dump();
}

static std::string doIdeLog(const js::Value& args) {
    const js::Value* t = args.find("text");
    if (!t || !t->isStr()) return jsonErr("缺少参数 text");
    std::wstring w = u2w(t->asStr());
    debugAddString(w.c_str());
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    return v.dump();
}

static std::string doGetOutput(const js::Value& args) {
    int tailLines = 200;
    int maxChars = 60000;
    bool clear = false;
    if (const js::Value* p = args.find("tailLines")) tailLines = p->asInt(200);
    if (const js::Value* p = args.find("maxChars")) maxChars = p->asInt(60000);
    if (const js::Value* p = args.find("clear")) clear = p->asBool(false);

    HELEx h = nullptr;
    if (!getHele(PANE_OUTPUT_DEBUG_EDIT, &h) || !h) {
        h = nullptr;
        if (!getHele(PANE_OUTPUT_DEBUG, &h) || !h) {
            return jsonErr("取不到调试输出窗格句柄");
        }
    }

    int len = XEdit_GetLength(h);
    if (len < 0) len = 0;

    // 注意：不能用 XEdit_GetRowCount/GetTextRow 走"按行取尾部窗口"——实测该控件
    // 的行数在 XEdit_AddText 追加内容后不刷新（length 从 2731 涨到 7381，rows 恒为 37），
    // 按行取会改变 tailLines 的返回语义。这里保持"取全文再截断"，只把截断改成原地 erase，
    // 不再 substr 复制一份。
    std::wstring wbuf((size_t)len + 2, L'\0');
    int got = XEdit_GetText(h, &wbuf[0], (int)wbuf.size());
    if (got < 0) got = 0;
    wbuf.resize((size_t)got);
    std::string text = w2u(wbuf.c_str());

    // 从尾部反向扫描取末 N 行，不再分配整份行起点数组。
    if (tailLines > 0 && text.size()) {
        size_t end = text.size();
        int lines = 1;
        size_t cut = 0;
        size_t p = end;
        while (p > 0) {
            --p;
            if (text[p] == '\n' && p + 1 < end) {
                if (++lines > tailLines) { cut = p + 1; break; }
            }
        }
        if (cut) text.erase(0, cut);
    }
    if (maxChars > 0 && (int)text.size() > maxChars) {
        text.erase(0, text.size() - (size_t)maxChars);
    }
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putInt(v, "length", len);
    putInt(v, "rows", XEdit_GetRowCount(h));
    v.set("text", std::move(text));
    putBool(v, "cleared", false);
    if (clear) putStr(v, "clearNote", "已禁用输出窗格清空：XEdit_SetText 会脏写磁盘");
    return v.dump();
}

bool pathAllowed(const std::wstring& pathIn, bool allowOutside, std::string& err);
std::string xmlEsc(const std::string& s);
std::string xmlUnesc(const std::string& s);
std::string xmlAttr(const std::string& line, const char* key);

static std::string doReadFile(const js::Value& args) {
    const js::Value* p = args.find("path");
    if (!p || !p->isStr()) return jsonErr("缺少参数 path");
    std::string path = p->asStr();
    bool allowOutside = false;
    if (const js::Value* q = args.find("allowOutsideProject")) allowOutside = q->asBool(false);
    std::wstring wpath = u2w(path);          // 只转换一次（之前 pathAllowed/读取/stat 各转一次）
    std::string perr;
    if (!pathAllowed(wpath, allowOutside, perr)) return jsonErr(perr);
    std::string content, err;
    bool bom = false;
    if (!readFileUtf8(wpath, content, err, &bom)) return jsonErr(err);
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "path", path);
    putInt(v, "size", (int)content.size());
    WIN32_FILE_ATTRIBUTE_DATA fad;
    long long raw = 0;
    if (GetFileAttributesExW(wpath.c_str(), GetFileExInfoStandard, &fad))
        raw = ((long long)fad.nFileSizeHigh << 32) | (long long)fad.nFileSizeLow;
    putInt(v, "rawSize", (int)raw);
    // 之前这里检测的是 readFileUtf8 已经剥掉 BOM 之后的内容，所以恒为 false。
    putBool(v, "bom", bom);
    int crlf = 0;
    for (size_t k = 0; k + 1 < content.size(); ++k)
        if (content[k] == '\r' && content[k + 1] == '\n') ++crlf;
    putInt(v, "crlf", crlf);
    v.set("content", std::move(content));
    return v.dump();
}

static bool skipProjDirW(const std::wstring& nm) {
    static const wchar_t* kSkip[] = { L"obj", L"bin", L"node_modules", L".git", L".vs",
                                      L".idea", L"x64", L"x86", L"packages", L"vendor",
                                      L"Debug", L"Release", L"Debug64", L"Release64" };
    for (int i = 0; i < (int)(sizeof(kSkip) / sizeof(kSkip[0])); ++i)
        if (_wcsicmp(nm.c_str(), kSkip[i]) == 0) return true;
    return false;
}

static void scanProjectsRec(const std::wstring& dir, int depth, int maxDepth,
                            js::Value& arr, int& scanned) {
    if (depth > maxDepth) return;
    if ((int)arr.items().size() >= 500) return;
    ++scanned;

    std::wstring pattern = dir;
    pattern += L'\\';
    pattern += L'*';
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;

    std::vector<std::wstring> dirs;
    std::wstring proj;
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!skipProjDirW(fd.cFileName)) dirs.push_back(fd.cFileName);
        } else {
            std::wstring nm = fd.cFileName;
            if (proj.empty() && nm.size() > 7 && nm.substr(nm.size() - 7) == L".xcproj")
                proj = nm;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);

    if (!proj.empty()) {
        js::Value it = js::Value::obj();
        putStr(it, "dir", w2u(dir.c_str()));
        putStr(it, "project_file", w2u(proj.c_str()));
        arr.push(it);
    }
    for (size_t i = 0; i < dirs.size(); ++i) {
        std::wstring sub = dir;
        sub += L'\\';
        sub += dirs[i];
        scanProjectsRec(sub, depth + 1, maxDepth, arr, scanned);
    }
}

static std::string doListProjects(const js::Value& args) {
    const js::Value* p = args.find("root");
    if (!p || !p->isStr()) return jsonErr("缺少参数 root");
    std::string root = p->asStr();
    std::wstring wroot = u2w(root);

    int maxDepth = 6;
    const js::Value* dp = args.find("depth");
    if (dp && dp->type() == js::NUM) {
        maxDepth = (int)dp->asNum();
        if (maxDepth < 0) maxDepth = 0;
        if (maxDepth > 12) maxDepth = 12;
    }

    js::Value arr = js::Value::arr();
    int scanned = 0;
    scanProjectsRec(wroot, 0, maxDepth, arr, scanned);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "root", root);
    putInt(v, "count", (int)arr.items().size());
    putInt(v, "dirsScanned", scanned);
    putInt(v, "maxDepth", maxDepth);
    v.set(std::string("projects"), arr);
    return v.dump();
}

static bool safeW(const void* p, wchar_t* out, int cch) {
    __try {
        const wchar_t* s = (const wchar_t*)p;
        int i = 0;
        for (; i < cch - 1 && s[i]; ++i) out[i] = s[i];
        out[i] = 0;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
        return false;
    }
}

static std::string hexDump(const unsigned char* p, int n) {
    static const char* H = "0123456789ABCDEF";
    std::string s;
    for (int i = 0; i < n; ++i) {
        s += H[p[i] >> 4];
        s += H[p[i] & 15];
        if ((i + 1) % 8 == 0) s += ' ';
    }
    return s;
}
static int safeTreeCount(HELEx h) {
    __try { return XTree_GetCount(h); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return -1; }
}

static HELEx safeFirstChild(HELEx h, HELEx parent) {
    __try { return XTree_GetFirstChildItem(h, parent); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return nullptr; }
}

static HELEx safeNextSibling(HELEx h, HELEx item) {
    __try { return XTree_GetNextSiblingItem(h, item); }
    __except (EXCEPTION_EXECUTE_HANDLER) { return nullptr; }
}

static bool safeItemText(HELEx h, HELEx item, wchar_t* out, int cch) {
    __try {
        const wchar_t* t = XTree_GetItemText(h, item);
        if (!t) { out[0] = 0; return false; }
        int i = 0;
        for (; i < cch - 1 && t[i]; ++i) out[i] = t[i];
        out[i] = 0;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        out[0] = 0;
        return false;
    }
}

static bool safeSetSelect(HELEx h, HELEx item) {
    __try { return XTree_SetSelectItem(h, item) != FALSE; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

struct SymOut {
    int type;
    int lineno;
    int col1;
    int col2;
    int e1;
    int e2;
    std::wstring name;
    std::wstring alt;
    std::wstring file;
    bool ok;
};

static SymOut readSym(const Data& d, int baseSlot) {
    SymOut s;
    memset(&s, 0, sizeof(int) * 6);
    s.ok = false;
    const unsigned char* p = (const unsigned char*)&d.v[baseSlot];
    memcpy(&s.type,   p + 0x00, 4);
    memcpy(&s.lineno, p + 0x04, 4);
    memcpy(&s.col1,   p + 0x08, 4);
    memcpy(&s.col2,   p + 0x0C, 4);
    memcpy(&s.e1,     p + 0x10, 4);
    memcpy(&s.e2,     p + 0x14, 4);
    const void* pn = *(const void* const*)(p + 0x18);
    const void* pa = *(const void* const*)(p + 0x20);
    const void* pf = *(const void* const*)(p + 0x28);
    wchar_t buf[1024];
    if (pn && safeW(pn, buf, 1024)) s.name = buf;
    if (pa && safeW(pa, buf, 1024)) s.alt = buf;
    if (pf && safeW(pf, buf, 1024)) s.file = buf;
    s.ok = (s.type != 0) || !s.name.empty();
    return s;
}

static bool getCodeEditor(std::wstring* path, HELEx* hEle) {
    Data d;
    memset(&d, 0, sizeof(d));
    d.type = 16;
    xi::call(d);
    const wchar_t* p = (const wchar_t*)d.get(0);
    if (path) *path = p ? p : L"";
    if (hEle) *hEle = d.get(1);
    return d.get(1) != nullptr;
}

static js::Value putSym(const SymOut& s) {
    js::Value v = js::Value::obj();
    putBool(v, "ok", s.ok);
    putInt(v, "symbol_type", s.type);
    putInt(v, "lineno", s.lineno);
    putInt(v, "col1", s.col1);
    putInt(v, "col2", s.col2);
    putInt(v, "extra1", s.e1);
    putInt(v, "extra2", s.e2);
    putStr(v, "name", w2u(s.name.c_str()));
    putStr(v, "alias", w2u(s.alt.c_str()));
    putStr(v, "file", w2u(s.file.c_str()));
    return v;
}

static std::string doSymbol(const js::Value& args) {
    const js::Value* pn = args.find("name");
    const js::Value* pf = args.find("path");
    const js::Value* pl = args.find("lineno");

    std::wstring wname, wpath;
    if (pn && pn->isStr()) wname = u2w(pn->asStr());
    if (pf && pf->isStr()) wpath = u2w(pf->asStr());

    if (!wname.empty() && !wpath.empty()) {
        Data d;
        memset(&d, 0, sizeof(d));
        d.type = 4;
        d.id = (pl ? pl->asInt(0) : 0);
        d.put(0, (void*)wpath.c_str());
        d.put(1, (void*)wname.c_str());
        int rc = xi::call(d);
        if (rc == 0) return jsonErr("getSymbolEx 未找到匹配符号");
        return putSym(readSym(d, 2)).dump();
    }

    if (!wname.empty()) {
        Data d;
        memset(&d, 0, sizeof(d));
        d.type = 3;
        d.put(0, (void*)wname.c_str());
        int rc = xi::call(d);
        if (rc == 0) return jsonErr("getSymbol 未找到该符号");
        return putSym(readSym(d, 1)).dump();
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "hint", "不传参数时返回当前编辑器文件；查符号请传 name（可加 path）");
    std::wstring path;
    HELEx hEle = nullptr;
    if (getCodeEditor(&path, &hEle)) {
        putStr(v, "current_file", w2u(path.c_str()));
        putNum(v, "hEle", (double)(uintptr_t)hEle);
    }
    return v.dump();
}

static std::string doEditorGet(const js::Value& args) {
    (void)args;
    std::wstring path;
    HELEx h = nullptr;
    if (!getCodeEditor(&path, &h)) return jsonErr("取不到代码编辑器句柄（可能没有打开的文件）");

    int len = XEdit_GetLength(h);
    if (len < 0) len = 0;
    std::wstring wbuf((size_t)len + 2, L'\0');
    int got = XEdit_GetText(h, &wbuf[0], (int)wbuf.size());
    if (got < 0) got = 0;
    wbuf.resize((size_t)got);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "file", w2u(path.c_str()));
    putInt(v, "length", got);
    putInt(v, "rows", XEdit_GetRowCount(h));
    putStr(v, "text", w2u(wbuf.c_str()));
    return v.dump();
}

static bool writeFileKeepEnc(const std::wstring& path, const std::string& content,
                             std::string& err, int bomOverride = -1) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    bool hasBom = false;
    bool hasCrlf = false;
    bool existed = false;
    if (h != INVALID_HANDLE_VALUE) {
        existed = true;
        char head[8192] = {0};
        DWORD n = 0;
        if (ReadFile(h, head, sizeof(head) - 1, &n, nullptr) && n >= 3) {
            hasBom = ((unsigned char)head[0] == 0xEF && (unsigned char)head[1] == 0xBB &&
                      (unsigned char)head[2] == 0xBF);
            for (DWORD i = 0; i + 1 < n; ++i) {
                if (head[i] == '\r' && head[i + 1] == '\n') { hasCrlf = true; break; }
            }
        }
        CloseHandle(h);
    }
    if (!existed) {
        size_t dot = path.find_last_of(L'.');
        std::wstring ext = (dot == std::wstring::npos) ? L"" : path.substr(dot);
        if (ext == L".xc" || ext == L".xh" || ext == L".xcs" ||
            ext == L".h" || ext == L".hpp" || ext == L".cpp" || ext == L".cc") {
            hasBom = true;
            hasCrlf = true;
        }
    }
    if (bomOverride >= 0) hasBom = (bomOverride != 0);

    std::string out = content;
    if (hasCrlf) {
        std::string tmp;
        tmp.reserve(out.size() + 64);
        for (size_t i = 0; i < out.size(); ++i) {
            if (out[i] == '\r' && i + 1 < out.size() && out[i + 1] == '\n') continue;
            if (out[i] == '\n') { tmp += "\r\n"; continue; }
            tmp += out[i];
        }
        out.swap(tmp);
    }

    std::wstring tmpPath = path + L".xcmcp_tmp";
    h = CreateFileW(tmpPath.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                    FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        err = "写入打开失败, err=" + std::to_string(GetLastError());
        return false;
    }
    DWORD wrote = 0;
    BOOL ok = TRUE;
    if (hasBom) {
        const char bom[3] = {'\xEF', '\xBB', '\xBF'};
        ok = WriteFile(h, bom, 3, &wrote, nullptr);
    }
    if (ok && !out.empty()) ok = WriteFile(h, out.data(), (DWORD)out.size(), &wrote, nullptr);
    if (ok) ok = FlushFileBuffers(h);
    CloseHandle(h);
    if (!ok) {
        DeleteFileW(tmpPath.c_str());
        err = "写入失败";
        return false;
    }
    if (!MoveFileExW(tmpPath.c_str(), path.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DWORD e2 = GetLastError();
        DeleteFileW(tmpPath.c_str());
        err = "替换目标文件失败, err=" + std::to_string(e2);
        return false;
    }
    return true;
}

static std::string doEditorSet(const js::Value& args) {
    const js::Value* pt = args.find("text");
    if (!pt || !pt->isStr()) return jsonErr("缺少参数 text");
    std::string text = pt->asStr();

    std::wstring path;
    if (const js::Value* pp = args.find("path")) {
        if (pp->isStr() && !pp->asStr().empty()) path = u2w(pp->asStr());
    }
    if (path.empty()) {
        std::wstring cur;
        HELEx h = nullptr;
        if (!getCodeEditor(&cur, &h) || cur.empty()) {
            return jsonErr("未传 path 且取不到当前编辑器文件");
        }
        path = cur;
    }

    bool allowOutside = false;
    if (const js::Value* q = args.find("allowOutsideProject")) allowOutside = q->asBool(false);
    std::string perr;
    if (!pathAllowed(path, allowOutside, perr)) return jsonErr(perr);

    // schema 里一直有 bom 参数但从未生效；现在显式给值时覆盖自动检测。
    int bomOverride = -1;
    if (const js::Value* pb = args.find("bom")) {
        if (pb->isStr()) {
            std::string s = lowerAscii(pb->asStr());
            if (s == "1" || s == "true" || s == "yes" || s == "on") bomOverride = 1;
            else if (s == "0" || s == "false" || s == "no" || s == "off") bomOverride = 0;
            else return jsonErr("bom 只能是 true/false（或 1/0）");
        } else if (pb->type() == js::BOOL) {
            bomOverride = pb->asBool() ? 1 : 0;
        } else {
            return jsonErr("bom 只能是 true/false（或 1/0）");
        }
    }

    std::string err;
    if (!writeFileKeepEnc(path, text, err, bomOverride)) return jsonErr(err);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "file", w2u(path.c_str()));
    putInt(v, "length", (int)text.size());
    putStr(v, "channel", "disk");
    putStr(v, "note", "已直接写盘并保持原编码；IDE 内需重新打开该文件或重启才读到新内容。"
                     "不使用编辑器内存写，因为 XEdit_SetText 会多插入一个字符（已实测）。");
    return v.dump();
}

static std::string doGotoFunction(const js::Value& args) {
    const js::Value* pn = args.find("name");
    if (!pn || !pn->isStr()) return jsonErr("缺少参数 name");
    std::wstring wname = u2w(pn->asStr());

    Data d;
    memset(&d, 0, sizeof(d));
    d.type = 3;
    d.put(0, (void*)wname.c_str());
    int rc = xi::call(d);
    if (rc == 0) return jsonErr("未找到函数定义");
    SymOut s = readSym(d, 1);

    std::wstring path;
    HELEx h = nullptr;
    getCodeEditor(&path, &h);

    bool moved = false;
    if (h && s.lineno > 0) {
        moved = XEdit_SetCurPosEx(h, s.lineno, s.col1 > 0 ? s.col1 : 0) != FALSE;
    }

    js::Value v = putSym(s);
    putBool(v, "moved", moved);
    if (!moved) putStr(v, "note", "定位到行失败，仅返回定义位置");
    return v.dump();
}

static void walkTree(HELEx hTree, HELEx parent, int depth, js::Value& arr, int& count,
                     const std::string& filter, std::set<void*>& seen, int& visited) {
    if (depth > 4 || count > 500 || visited > 800) return;
    HELEx item = safeFirstChild(hTree, parent);
    int guard = 0;
    while (item && guard++ < 200 && visited < 800) {
        if (seen.find(item) != seen.end()) {
            item = safeNextSibling(hTree, item);
            continue;
        }
        seen.insert(item);
        ++visited;

        wchar_t tb[512];
        std::string text;
        if (safeItemText(hTree, item, tb, 512)) text = w2u(tb);
        if (filter.empty() || text.find(filter) != std::string::npos) {
            js::Value it = js::Value::obj();
            putStr(it, "text", text);
            putInt(it, "depth", depth);
            putNum(it, "item", (double)(uintptr_t)item);
            arr.push(it);
            ++count;
        }
        walkTree(hTree, item, depth + 1, arr, count, filter, seen, visited);
        item = safeNextSibling(hTree, item);
    }
}

static HELEx findTreeItem(HELEx hTree, HELEx parent, const std::string& name, int depth,
                          std::set<void*>& seen, int& visited) {
    if (depth > 4 || visited > 800) return nullptr;
    HELEx item = safeFirstChild(hTree, parent);
    int guard = 0;
    while (item && guard++ < 200 && visited < 800) {
        if (seen.find(item) != seen.end()) {
            item = safeNextSibling(hTree, item);
            continue;
        }
        seen.insert(item);
        ++visited;

        wchar_t tb[512];
        if (safeItemText(hTree, item, tb, 512) && w2u(tb) == name) return item;
        HELEx r = findTreeItem(hTree, item, name, depth + 1, seen, visited);
        if (r) return r;
        item = safeNextSibling(hTree, item);
    }
    return nullptr;
}

static std::string doTreeProbe(const js::Value& args) {
    const js::Value* pa = args.find("action");
    std::string action = (pa && pa->isStr()) ? pa->asStr() : "dump";
    const js::Value* pnm = args.find("name");
    std::string name = (pnm && pnm->isStr()) ? pnm->asStr() : "";

    std::string root;
    void* p1 = nullptr;
    void* p2 = nullptr;
    void* p3 = nullptr;
    getProjectRaw(&p1, &p2, &p3);
    if (p3) root = w2u((const wchar_t*)p3);
    if (root.empty()) return jsonErr("取不到当前工程目录");

    js::Value arr = js::Value::arr();
    std::wstring wroot = u2w(root);
    std::wstring pattern = wroot;
    pattern += L'\\';
    pattern += L'*';
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    int count = 0;
    DWORD lastErr = 0;
    int loops = 0;
    if (h == INVALID_HANDLE_VALUE) {
        lastErr = GetLastError();
    } else {
        for (;;) {
            ++loops;
            bool skip = (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0);
            if (!skip) {
                std::string nm = w2u(fd.cFileName);
                if (name.empty() || nm.find(name) != std::string::npos) {
                    js::Value it = js::Value::obj();
                    putStr(it, "text", nm);
                    putBool(it, "dir", (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0);
                    putNum(it, "size", (double)(fd.nFileSizeLow));
                    arr.push(it);
                    ++count;
                }
            }
            if (!FindNextFileW(h, &fd)) {
                lastErr = GetLastError();
                break;
            }
            if (loops > 10000) break;
        }
        FindClose(h);
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "root", root);
    putStr(v, "action", action);
    putInt(v, "count", count);
    putInt(v, "loops", loops);
    putInt(v, "win32_err", (int)lastErr);
    putStr(v, "pattern", w2u(pattern.c_str()));
    if (action == "select") {
        putBool(v, "selected", false);
        putStr(v, "note", "本实现不支持项目树交互选择；IDE 内 XTree 遍历接口会导致访问违例，已改为文件系统视图");
    }
    v.set(std::string("entries"), arr);
    return v.dump();
}

static std::string doListFunctions(const js::Value& args) {
    std::string root;
    if (const js::Value* p = args.find("root")) {
        if (p->isStr()) root = p->asStr();
    }
    if (root.empty()) {
        void* p1 = nullptr; void* p2 = nullptr; void* p3 = nullptr;
        getProjectRaw(&p1, &p2, &p3);
        if (p3) root = w2u((const wchar_t*)p3);
    }
    if (root.empty()) return jsonErr("未指定 root 且取不到当前工程目录");

    std::string filter;
    if (const js::Value* p = args.find("filter")) {
        if (p->isStr()) filter = p->asStr();
    }

    js::Value arr = js::Value::arr();
    std::wstring wroot = u2w(root);
    WIN32_FIND_DATAW fd;
    std::wstring pattern = wroot;
    pattern += L'\\';
    pattern += L"*.xc";
    int filesScanned = 0;
    std::string firstErr;
    js::Value errs = js::Value::arr();
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            std::string content;
            std::string err;
            std::wstring full = wroot + L"\\" + fd.cFileName;
            if (!readFileUtf8(full, content, err)) {
                if (firstErr.empty()) firstErr = err;
                continue;
            }
            ++filesScanned;

            int line = 1;
            size_t pos = 0;
            while (pos <= content.size()) {
                size_t nl = content.find('\n', pos);
                std::string ln = content.substr(pos, (nl == std::string::npos ? std::string::npos : nl - pos));
                if (!ln.empty() && ln.back() == '\r') ln.pop_back();

                size_t k = ln.find("\u51fd\u6570");
                if (k != std::string::npos) {
                    size_t a = k + 6;
                    while (a < ln.size() && (ln[a] == ' ' || ln[a] == '\t')) ++a;
                    size_t b = a;
                    while (b < ln.size() && ln[b] != '(' && ln[b] != ' ' && ln[b] != '\t') ++b;

                    std::string first = ln.substr(a, b - a);
                    std::string nm = first;

                    size_t a2 = b;
                    while (a2 < ln.size() && (ln[a2] == ' ' || ln[a2] == '\t')) ++a2;
                    if (a2 < ln.size() && ln[a2] != '(') {
                        size_t b2 = a2;
                        while (b2 < ln.size() && ln[b2] != '(' && ln[b2] != ' ' && ln[b2] != '\t') ++b2;
                        nm = ln.substr(a2, b2 - a2);
                    }

                    if (!nm.empty() && (filter.empty() || nm.find(filter) != std::string::npos)) {
                        js::Value it = js::Value::obj();
                        putStr(it, "name", nm);
                        putStr(it, "file", w2u(fd.cFileName));
                        putInt(it, "line", line);
                        arr.push(it);
                    }
                }
                if (nl == std::string::npos) break;
                pos = nl + 1;
                ++line;
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "root", root);
    putInt(v, "count", (int)arr.items().size());
    putInt(v, "files_scanned", filesScanned);
    if (!firstErr.empty()) putStr(v, "first_error", firstErr);
    v.set(std::string("functions"), arr);
    return v.dump();
}


static bool importOne(const std::wstring& path, bool open) {
    Data d;
    memset(&d, 0, sizeof(d));
    d.type = 12;
    d.put(0, (void*)path.c_str());
    d.put(1, (void*)(uintptr_t)(open ? 1 : 0));
    int rc = xi::call(d);
    return rc != 0;
}

static std::string doAddFile(const js::Value& args) {
    const js::Value* pp = args.find("path");
    bool open = true;
    if (const js::Value* po = args.find("open")) open = po->asBool(true);

    std::vector<std::string> paths;
    if (pp && pp->isStr() && !pp->asStr().empty()) {
        std::string all = pp->asStr();
        size_t pos = 0;
        while (pos <= all.size()) {
            size_t semi = all.find(';', pos);
            std::string one = all.substr(pos, (semi == std::string::npos ? std::string::npos : semi - pos));
            if (!one.empty()) paths.push_back(one);
            if (semi == std::string::npos) break;
            pos = semi + 1;
        }
    }

    if (paths.empty()) return jsonErr("缺少参数 path（多个用分号分隔）");

    js::Value results = js::Value::arr();
    int okCount = 0;
    for (size_t i = 0; i < paths.size(); ++i) {
        std::wstring w = u2w(paths[i]);
        bool ok = importOne(w, open);
        if (ok) ++okCount;
        js::Value it = js::Value::obj();
        putStr(it, "path", paths[i]);
        putBool(it, "ok", ok);
        results.push(it);
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", okCount == (int)paths.size());
    putInt(v, "total", (int)paths.size());
    putInt(v, "imported", okCount);
    v.set(std::string("results"), results);
    return v.dump();
}

static std::string doOpenInIde(const js::Value& args) {
    const js::Value* pp = args.find("path");
    if (!pp || !pp->isStr()) return jsonErr("缺少参数 path");
    std::wstring w = u2w(pp->asStr());
    bool ok = importOne(w, true);
    js::Value v = js::Value::obj();
    putBool(v, "ok", ok);
    putStr(v, "path", pp->asStr());
    putStr(v, "channel", ok ? "importFile+open" : "importFile-failed");
    if (!ok) putStr(v, "note", "IDE 可能尚未加载工程，或该文件已在项目内");
    return v.dump();
}

static std::string doAddDirFiles(const js::Value& args) {
    std::string dir;
    if (const js::Value* pd = args.find("dir")) {
        if (pd->isStr()) dir = pd->asStr();
    }
    if (dir.empty()) {
        void* p1 = nullptr; void* p2 = nullptr; void* p3 = nullptr;
        getProjectRaw(&p1, &p2, &p3);
        if (p3) dir = w2u((const wchar_t*)p3);
    }
    if (dir.empty()) return jsonErr("未指定 dir 且取不到当前工程目录");

    std::string ext = "xc";
    if (const js::Value* pe = args.find("ext")) {
        if (pe->isStr()) ext = pe->asStr();
    }
    bool open = false;
    if (const js::Value* po = args.find("open")) open = po->asBool(false);

    std::wstring wdir = u2w(dir);
    std::wstring pattern = wdir;
    pattern += L'\\';
    pattern += L'*';
    pattern += u2w(ext);

    js::Value results = js::Value::arr();
    int okCount = 0;
    int total = 0;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        for (;;) {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
                std::wstring full = wdir;
                full += L'\\';
                full += fd.cFileName;
                bool ok = importOne(full, open);
                ++total;
                if (ok) ++okCount;
                js::Value it = js::Value::obj();
                putStr(it, "file", w2u(fd.cFileName));
                putBool(it, "ok", ok);
                results.push(it);
            }
            if (!FindNextFileW(h, &fd)) break;
            if (total > 2000) break;
        }
        FindClose(h);
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", total > 0 && okCount == total);
    putStr(v, "dir", dir);
    putStr(v, "ext", ext);
    putInt(v, "total", total);
    putInt(v, "imported", okCount);
    v.set(std::string("results"), results);
    return v.dump();
}


static HWND resolveMainHwnd() {
    return xi::mainHwnd();
}

static bool postKeyToIde(WORD vk, bool ctrl) {
    HWND hwnd = resolveMainHwnd();
    if (!hwnd) return false;
    if (ctrl) PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0x001D0001);
    PostMessageW(hwnd, WM_KEYDOWN, vk, 0x00000001);
    PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001);
    if (ctrl) PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0xC01D0001);
    return true;
}

static std::string doBuild(const js::Value& args) {
    (void)args;
    HWND hwnd = resolveMainHwnd();
    bool winOk = (hwnd != nullptr);
    bool sent = postKeyToIde(VK_F7, false);

    js::Value v = js::Value::obj();
    putBool(v, "ok", sent);
    putStr(v, "status", sent ? "dispatched" : "failed");
    putStr(v, "action", "F7");
    putStr(v, "channel", "PostMessage WM_KEYDOWN/UP");
    putNum(v, "hMainFrame", (double)(uintptr_t)g_hMainFrame);
    putNum(v, "hwnd", (double)(uintptr_t)hwnd);
    putBool(v, "mainFrameIsWindow", winOk);
    putNull(v, "compiled");
    if (!sent) {
        putStr(v, "error", "主窗口句柄无效，无法投递编译快捷键");
    } else {
        putStr(v, "note", "ok/status 只表示快捷键已投递，不代表编译成功。"
                         "必须用 xc_get_output 看输出里出现『错误列表』还是『跳过编译』。"
                         "同一 IDE 生命周期只有第一次编译生效。");
    }
    return v.dump();
}

static std::string doMenuCmd(const js::Value& args) {
    std::string command;
    if (const js::Value* pc = args.find("command")) {
        if (pc->isStr()) command = pc->asStr();
    }
    int menuId = 0;
    if (const js::Value* pm = args.find("menu_id")) menuId = pm->asInt(0);

    if (command.empty() && menuId) {
        if (menuId == 0x171) command = "compile";
        else if (menuId == 0x174) command = "recompile";
        else if (menuId == 0x172) command = "compile_all";
    }
    if (command.empty()) command = "compile";

    bool ok = false;
    std::string key;
    if (command == "compile") {
        ok = postKeyToIde(VK_F7, false);
        key = "F7";
    } else if (command == "recompile" || command == "compile_all") {
        ok = postKeyToIde(VK_F5, true);
        key = "Ctrl+F5";
    } else {
        return jsonErr("未知命令: " + command + "（可选 compile / recompile / compile_all）");
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", ok);
    putStr(v, "command", command);
    putStr(v, "key", key);
    putStr(v, "channel", "PostMessage WM_KEYDOWN/UP");
    if (ok) putStr(v, "note", "已投递，编译异步进行；用 xc_get_output 读结果");
    return v.dump();
}


static std::string doModuleInfo(const js::Value& args) {
    Data d;
    memset(&d, 0, sizeof(d));
    d.type = 17;

    std::wstring wpath, wname, wmod, wauth;
    const js::Value* p = nullptr;

    if ((p = args.find("path")) && p->isStr() && !p->asStr().empty()) {
        wpath = u2w(p->asStr());
        d.put(0, (void*)wpath.c_str());
    } else if ((p = args.find("name")) && p->isStr() && !p->asStr().empty()) {
        wname = u2w(p->asStr());
        d.put(1, (void*)wname.c_str());
    } else if ((p = args.find("module")) && p->isStr() && !p->asStr().empty()) {
        wmod = u2w(p->asStr());
        d.put(2, (void*)wmod.c_str());
        if ((p = args.find("author")) && p->isStr() && !p->asStr().empty()) {
            wauth = u2w(p->asStr());
            d.put(3, (void*)wauth.c_str());
        }
    } else {
        return jsonErr("需要 path / name / module 之一（module 建议同时给 author）");
    }

    int rc = xi::call(d);
    if (rc == 0) return jsonErr("未找到该模块");

    static const char* kNames[8] = {
        "path", "file", "module", "author", "version", "date", "remark", "extra"
    };

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    wchar_t buf[4096];
    for (int i = 0; i < 8; ++i) {
        const wchar_t* s = (const wchar_t*)d.get(i);
        if (!s) continue;
        if (!safeW(s, buf, 4096)) continue;
        std::string val = w2u(buf);
        if (val.empty()) continue;
        if (val.size() > 2000) val = val.substr(0, 2000) + "...";
        putStr(v, kNames[i], val);
    }
    return v.dump();
}


// ==================== 模块库（XC-Module）支持 ====================

static std::string attrOf(const std::string& tag, const char* key);
struct ModEntry {
    std::string file;
    std::string path;
    std::string name;
    std::string ver;
    std::string author;
    std::string remark;
    std::vector<std::string> deps;
    bool inProject;
};

// 模块头解析缓存：目录枚举照旧每次都做（保证新增/删除立刻可见），
// 只有 (路径, 大小, 最后写入时间) 都没变的文件才跳过"打开+读前 320 行+解析"。
// 本机 283 个模块时每次 xc_module_list / xc_module_search 可省掉 283 次文件读。
struct ModCacheEntry {
    FILETIME  mtime;
    ULONGLONG size;
    ModEntry  e;
};

SRWLOCK g_modCacheLock = SRWLOCK_INIT;

std::map<std::wstring, ModCacheEntry>& modCache() {
    static std::map<std::wstring, ModCacheEntry> c;
    return c;
}

bool modCacheLookup(const std::wstring& path, const FILETIME& mtime, ULONGLONG size, ModEntry& out) {
    bool hit = false;
    SrwGuard guard(g_modCacheLock, false);
    std::map<std::wstring, ModCacheEntry>& c = modCache();
    std::map<std::wstring, ModCacheEntry>::const_iterator it = c.find(path);
    if (it != c.end() && it->second.size == size &&
        CompareFileTime(&it->second.mtime, &mtime) == 0) {
        out = it->second.e;
        hit = true;
    }
    if (hit) InterlockedIncrement(&g_modCacheHits);
    else InterlockedIncrement(&g_modCacheMisses);
    return hit;
}

void modCacheStore(const std::wstring& path, const FILETIME& mtime, ULONGLONG size, const ModEntry& e) {
    ModCacheEntry ce = { mtime, size, e };
    SrwGuard guard(g_modCacheLock, true);
    std::map<std::wstring, ModCacheEntry>& c = modCache();
    if (c.size() >= 4096) c.clear();      // 目录长期增删时的兜底上限
    c.insert_or_assign(path, std::move(ce));
}

// 目录清单缓存：模块库有 2090 个子目录、1.7 万个文件项，FindFirstFile 全量枚举一次约 24ms。
// 目录的 LastWriteTime 在增删/改名条目时会变，所以缓存清单 + 目录 mtime 校验即可保持
// "新增/删除立刻可见"；文件内容是否变化则逐个 stat 判断（模块文件只有约 300 个）。
struct DirListing {
    FILETIME mtime;
    std::vector<std::wstring> subDirs;
    std::vector<std::wstring> moduleFiles;
};

SRWLOCK g_dirCacheLock = SRWLOCK_INIT;

std::map<std::wstring, DirListing>& dirCache() {
    static std::map<std::wstring, DirListing> c;
    return c;
}

bool isModuleFileName(const std::wstring& nm) {
    if (nm.size() < 10) return false;
    if (nm.compare(0, 7, L"module_") != 0) return false;
    size_t dot = nm.find_last_of(L'.');
    if (dot == std::wstring::npos) return false;
    std::wstring ext = nm.substr(dot);
    return ext == L".h" || ext == L".xc";
}

bool isSkippedSubDir(const std::wstring& nm) {
    return nm == L"obj" || nm == L".git" || nm == L"doc" || nm == L"lib" ||
           nm == L"x64" || nm == L"x86" || nm == L"vendor";
}

// 返回 false 表示目录不存在/不可读。
bool getDirListing(const std::wstring& dir, DirListing& out) {
    WIN32_FILE_ATTRIBUTE_DATA fad;
    if (!GetFileAttributesExW(dir.c_str(), GetFileExInfoStandard, &fad)) return false;
    if (!(fad.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) return false;

    {
        SrwGuard guard(g_dirCacheLock, false);
        std::map<std::wstring, DirListing>& c = dirCache();
        std::map<std::wstring, DirListing>::const_iterator it = c.find(dir);
        if (it != c.end() && CompareFileTime(&it->second.mtime, &fad.ftLastWriteTime) == 0) {
            out = it->second;
            return true;
        }
    }

    DirListing dl;
    dl.mtime = fad.ftLastWriteTime;
    std::wstring pattern = dir;
    pattern += L'\\';
    pattern += L'*';
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return false;
    do {
        std::wstring nm = fd.cFileName;
        if (nm == L"." || nm == L"..") continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!isSkippedSubDir(nm)) dl.subDirs.push_back(nm);
        } else if (isModuleFileName(nm)) {
            dl.moduleFiles.push_back(nm);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);

    {
        SrwGuard guard(g_dirCacheLock, true);
        std::map<std::wstring, DirListing>& c = dirCache();
        if (c.size() >= 8192) c.clear();
        c.insert_or_assign(dir, dl);
    }
    out = std::move(dl);
    return true;
}

static std::string trimAscii(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && (s[a] == ' ' || s[a] == '\t')) ++a;
    while (b > a) {
        char c = s[b - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') --b;
        else break;
    }
    return s.substr(a, b - a);
}

static bool startsWith(const std::string& s, const char* tag) {
    size_t n = strlen(tag);
    if (s.size() < n) return false;
    return s.compare(0, n, tag) == 0;
}

static bool parseModuleHeader(const std::wstring& wpath, ModEntry& e) {
    std::string txt, err;
    if (!readFileUtf8(wpath, txt, err)) return false;
    size_t pos = 0;
    int lines = 0;
    bool gotRemark = false;
    while (pos <= txt.size() && lines < 320) {
        size_t nl = txt.find('\n', pos);
        size_t endp = (nl == std::string::npos) ? txt.size() : nl;
        std::string line = txt.substr(pos, endp - pos);
        pos = (nl == std::string::npos) ? txt.size() + 1 : nl + 1;
        ++lines;
        if (lines == 1 && line.size() >= 3 && (unsigned char)line[0] == 0xEF &&
            (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF) {
            line = line.substr(3);
        }
        std::string t = trimAscii(line);
        if (t.empty()) continue;
        if (t.find("//@模块信息结束") != std::string::npos) break;
        size_t n;
        n = strlen("//@模块名称");
        if (startsWith(t, "//@模块名称")) { e.name = trimAscii(t.substr(n)); continue; }
        n = strlen("//@版本");
        if (startsWith(t, "//@版本") && !startsWith(t, "//@版本信息")) {
            e.ver = trimAscii(t.substr(n));
            continue;
        }
        n = strlen("//@作者");
        if (startsWith(t, "//@作者")) { e.author = trimAscii(t.substr(n)); continue; }
        n = strlen("//@模块备注");
        if (startsWith(t, "//@模块备注")) {
            std::string v = trimAscii(t.substr(n));
            if (!v.empty()) { e.remark = v; gotRemark = true; }
            continue;
        }
        n = strlen("//@依赖");
        if (startsWith(t, "//@依赖")) {
            std::string v = trimAscii(t.substr(n));
            std::string cur;
            for (size_t k = 0; k <= v.size(); ++k) {
                char ch = (k < v.size()) ? v[k] : ',';
                if (ch == ',' || ch == ';' || ch == ' ' || ch == '\t' || ch == '"') {
                    if (!cur.empty() && cur != "无") e.deps.push_back(cur);
                    cur.clear();
                } else {
                    cur += ch;
                }
            }
            continue;
        }
        if (!gotRemark && !e.name.empty() && t.size() > 2 && t[0] != '/' && e.remark.empty()) {
            // 模块备注换行续写
            if (t.compare(0, 3, "//@") != 0) e.remark = t;
        }
    }
    return true;      // 读成功就算可缓存；没有 //@ 头的文件也一样（name 由调用方回退成文件名）
}

static void scanModuleDir(const std::wstring& dir, int depth, const std::string& relDir,
                          std::vector<ModEntry>& out, size_t limit, size_t& scanned) {
    if (depth > 4 || out.size() >= limit) return;
    DirListing listing;
    if (!getDirListing(dir, listing)) return;

    for (size_t i = 0; i < listing.moduleFiles.size() && out.size() < limit; ++i) {
        const std::wstring& nm = listing.moduleFiles[i];
        std::wstring full = dir;
        full += L'\\';
        full += nm;
        WIN32_FILE_ATTRIBUTE_DATA fad;
        if (!GetFileAttributesExW(full.c_str(), GetFileExInfoStandard, &fad)) continue;
        if (fad.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        ULONGLONG fsize = ((ULONGLONG)fad.nFileSizeHigh << 32) | fad.nFileSizeLow;
        ++scanned;
        ModEntry e;
        if (!modCacheLookup(full, fad.ftLastWriteTime, fsize, e)) {
            e.file = w2u(nm.c_str());
            e.path = w2u(full.c_str());
            e.inProject = false;
            bool parsed = parseModuleHeader(full, e);
            if (e.name.empty()) e.name = e.file;
            if (parsed) modCacheStore(full, fad.ftLastWriteTime, fsize, e);
        }
        e.inProject = false;
        out.push_back(e);
    }

    for (size_t i = 0; i < listing.subDirs.size() && out.size() < limit; ++i) {
        std::wstring sub = dir;
        sub += L'\\';
        sub += listing.subDirs[i];
        std::string subRel = relDir.empty() ? w2u(listing.subDirs[i].c_str())
                                            : relDir + "/" + w2u(listing.subDirs[i].c_str());
        scanModuleDir(sub, depth + 1, subRel, out, limit, scanned);
    }
}

static std::wstring parentDirW(const std::wstring& p) {
    size_t a = p.find_last_of(L'\\');
    if (a == std::wstring::npos) return std::wstring();
    if (a < 2) return std::wstring();
    return p.substr(0, a);
}

static std::wstring baseNameOfW(const std::wstring& p) {
    size_t a = p.find_last_of(L'\\');
    if (a == std::wstring::npos) return p;
    return p.substr(a + 1);
}

static std::wstring ideRootDirW() {
    // IDE 根在进程生命周期内不会变；每次调用都做一遍 GetModuleFileName+上溯纯属浪费。
    static SRWLOCK lock = SRWLOCK_INIT;
    static std::wstring cached;
    static bool resolved = false;

    {
        SrwGuard guard(lock, false);
        if (resolved) return cached;
    }

    std::wstring p;
    HMODULE self = g_hSelf ? (HMODULE)g_hSelf : nullptr;
    if (self) {
        wchar_t tmp[MAX_PATH] = {0};
        if (GetModuleFileNameW(self, tmp, MAX_PATH)) {
            std::wstring dir = parentDirW(std::wstring(tmp));
            for (int i = 0; i < 6 && !dir.empty(); ++i) {
                if (_wcsicmp(baseNameOfW(dir).c_str(), L"data") == 0) {
                    p = parentDirW(dir);
                    break;
                }
                dir = parentDirW(dir);
            }
        }
    }
    if (p.empty()) {
        const wchar_t* env = _wgetenv(L"XC_IDE_ROOT");
        if (env && *env) {
            std::wstring e = env;
            std::wstring probe = e + L"\\data";
            if (GetFileAttributesW(probe.c_str()) != INVALID_FILE_ATTRIBUTES) p = e;
            else xi::logLine(L"I: XC_IDE_ROOT 下没有 data 目录，已忽略");
        }
    }
    if (p.empty()) xi::logLine(L"I: ideRootDirW 推导失败");

    SrwGuard guard(lock, true);
    if (!resolved && !p.empty()) {
        cached = p;
        resolved = true;
    }
    return cached;
}

static void moduleRootsW(bool includeDev, std::vector<std::wstring>& roots) {
    roots.clear();
    std::wstring root = ideRootDirW();
    if (root.empty()) return;
    std::wstring base = root + L"\\data\\";
    roots.push_back(base + L"XC-Module");
    if (includeDev) roots.push_back(base + L"XC-Module-dev");
}

static std::vector<ModEntry> scanAllModules(bool includeDev, size_t limit) {
    std::vector<ModEntry> out;
    size_t scanned = 0;
    std::vector<std::wstring> roots;
    moduleRootsW(includeDev, roots);
    for (size_t i = 0; i < roots.size() && out.size() < limit; ++i) {
        scanModuleDir(roots[i], 0, "", out, limit, scanned);
    }
    return out;
}

static bool getProjectPaths(std::string* pName, std::string* pFile, std::string* pDir) {
    void* p1 = nullptr;
    void* p2 = nullptr;
    void* p3 = nullptr;
    getProjectRaw(&p1, &p2, &p3);
    if (pName) *pName = p1 ? w2u((const wchar_t*)p1) : std::string();
    if (pFile) *pFile = p2 ? w2u((const wchar_t*)p2) : std::string();
    if (pDir) *pDir = p3 ? w2u((const wchar_t*)p3) : std::string();
    return p2 != nullptr;
}

static bool readProjectModuleItems(const std::wstring& xcproj,
                                   std::vector<std::pair<std::string, std::string>>& items,
                                   std::string& err) {
    std::string txt;
    if (!readFileUtf8(xcproj, txt, err)) return false;
    size_t a = txt.find("<modules>");
    size_t b = txt.find("</modules>");
    if (a == std::string::npos || b == std::string::npos || b < a) {
        err = "xcproj 里没有 <modules> 节点";
        return false;
    }
    size_t pos = a;
    while (true) {
        size_t it = txt.find("<item", pos);
        if (it == std::string::npos || it > b) break;
        size_t itEnd = txt.find('>', it);
        if (itEnd == std::string::npos || itEnd > b) break;
        std::string tag = txt.substr(it, itEnd - it + 1);
        std::string path = attrOf(tag, "path");
        std::string name = attrOf(tag, "name");
        items.push_back(std::make_pair(path, name));
        pos = itEnd + 1;
    }
    return true;
}

static std::string attrOf(const std::string& tag, const char* key) {
    std::string k = std::string(key) + "=\"";
    size_t p = tag.find(k);
    if (p == std::string::npos) return std::string();
    p += k.size();
    size_t e = tag.find('"', p);
    if (e == std::string::npos) return std::string();
    return tag.substr(p, e - p);
}

static std::string baseNameOf(const std::string& p) {
    size_t s = p.find_last_of("\\/");
    return (s == std::string::npos) ? p : p.substr(s + 1);
}

static bool writeProjectModuleItems(const std::wstring& xcproj,
                                    const std::vector<std::pair<std::string, std::string>>& items,
                                    std::string& err) {
    std::string txt;
    if (!readFileUtf8(xcproj, txt, err)) return false;
    size_t a = txt.find("<modules>");
    size_t b = txt.find("</modules>");
    if (a == std::string::npos || b == std::string::npos || b < a) {
        err = "xcproj 里没有 <modules> 节点";
        return false;
    }
    size_t blockStart = a + 9;
    std::string inner = txt.substr(blockStart, b - blockStart);

    std::vector<std::string> rawItems;
    {
        size_t i = 0;
        while (i < inner.size()) {
            size_t s = inner.find("<item", i);
            if (s == std::string::npos) break;
            size_t gt = inner.find('>', s);
            if (gt == std::string::npos) break;
            size_t se;
            if (gt > s && inner[gt - 1] == '/') {
                se = gt + 1;
            } else {
                size_t close = inner.find("</item>", gt);
                if (close == std::string::npos) break;
                se = close + 7;
            }
            rawItems.push_back(inner.substr(s, se - s));
            i = se;
        }
    }

    std::string head = txt.substr(0, blockStart);
    std::string tail = txt.substr(b);
    std::string block = "\n";
    for (size_t i = 0; i < items.size(); ++i) {
        const std::string& rawPath = items[i].first;
        const std::string& rawName = items[i].second;
        std::string line;
        for (size_t k = 0; k < rawItems.size(); ++k) {
            if (xmlAttr(rawItems[k], "path") == rawPath) { line = rawItems[k]; break; }
        }
        if (line.empty()) {
            line = "<item path=\"" + xmlEsc(rawPath) + "\" name=\"" + xmlEsc(rawName) + "\" />";
        } else {
            size_t np = line.find("name=\"");
            if (np == std::string::npos) {
                size_t slash = line.find_last_of('/');
                if (slash == std::string::npos) slash = line.size() - 1;
                std::string pad = (slash > 0 && line[slash - 1] == ' ') ? "" : " ";
                line.insert(slash, pad + "name=\"" + xmlEsc(rawName) + "\"");
            } else {
                size_t vs = np + 6;
                size_t ve = line.find('"', vs);
                if (ve != std::string::npos) line.replace(vs, ve - vs, xmlEsc(rawName));
            }
        }
        block += "\t\t" + line + "\n";
    }
    block += "\t";
    std::string out = head + block + tail;
    if (!writeFileKeepEnc(xcproj, out, err)) return false;
    return true;
}

bool pathAllowed(const std::wstring& pathIn, bool allowOutside, std::string& err) {
    if (pathIn.empty()) { err = "路径为空"; return false; }
    if (allowOutside) return true;

    wchar_t full[MAX_PATH * 2];
    DWORD n = GetFullPathNameW(pathIn.c_str(), MAX_PATH * 2, full, nullptr);
    if (n == 0 || n >= MAX_PATH * 2) { err = "路径无法规范化"; return false; }
    std::wstring f(full, n);
    for (size_t i = 0; i < f.size(); ++i) if (f[i] == L'/') f[i] = L'\\';

    std::vector<std::wstring> roots;
    std::string pName, pFile, pDir;
    getProjectPaths(&pName, &pFile, &pDir);
    if (!pDir.empty()) roots.push_back(u2w(pDir));

    std::vector<std::wstring> mr;
    moduleRootsW(true, mr);
    for (size_t i = 0; i < mr.size(); ++i) roots.push_back(mr[i]);

    wchar_t plug[MAX_PATH * 2];
    if (g_hSelf && GetModuleFileNameW(g_hSelf, plug, MAX_PATH * 2) > 0) {
        std::wstring pd = parentDirW(std::wstring(plug));
        if (!pd.empty()) roots.push_back(pd);
    }

    std::vector<std::string> shown;
    for (size_t i = 0; i < roots.size(); ++i) {
        std::wstring r = roots[i];
        for (size_t k = 0; k < r.size(); ++k) if (r[k] == L'/') r[k] = L'\\';
        while (!r.empty() && r[r.size() - 1] == L'\\') r.erase(r.size() - 1);
        if (r.empty()) continue;
        if (_wcsnicmp(f.c_str(), r.c_str(), r.size()) != 0) continue;
        if (f.size() != r.size() && f[r.size()] != L'\\') continue;
        return true;
    }
    for (size_t i = 0; i < roots.size(); ++i) shown.push_back(w2u(roots[i].c_str()));
    err = "路径不在允许范围内（工程目录 / 模块库 / 插件目录）；确需访问请传 allowOutsideProject=true。允许的根: ";
    for (size_t i = 0; i < shown.size(); ++i) {
        if (i) err += " | ";
        err += shown[i];
    }
    return false;
}

std::string xmlEsc(const std::string& s) {
    std::string r;
    r.reserve(s.size() + 8);
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == '&') r += "&amp;";
        else if (c == '<') r += "&lt;";
        else if (c == '>') r += "&gt;";
        else if (c == '"') r += "&quot;";
        else if (c == '\'') r += "&apos;";
        else r += c;
    }
    return r;
}

std::string xmlUnesc(const std::string& s) {
    std::string r;
    r.reserve(s.size());
    size_t i = 0;
    while (i < s.size()) {
        if (s.compare(i, 5, "&amp;") == 0) { r += '&'; i += 5; }
        else if (s.compare(i, 4, "&lt;") == 0) { r += '<'; i += 4; }
        else if (s.compare(i, 4, "&gt;") == 0) { r += '>'; i += 4; }
        else if (s.compare(i, 6, "&quot;") == 0) { r += '"'; i += 6; }
        else if (s.compare(i, 6, "&apos;") == 0) { r += '\''; i += 6; }
        else { r += s[i]; ++i; }
    }
    return r;
}

std::string xmlAttr(const std::string& line, const char* key) {
    std::string pat = std::string(key) + "=\"";
    size_t p = line.find(pat);
    if (p == std::string::npos) return "";
    size_t s = p + pat.size();
    size_t e = line.find('"', s);
    if (e == std::string::npos) return "";
    return xmlUnesc(line.substr(s, e - s));
}

static std::string doModuleList(const js::Value& args) {
    int limit = 100;
    if (const js::Value* p = args.find("limit")) {
        if (p->type() == js::NUM) limit = (int)p->asNum();
    }
    if (limit <= 0 || limit > 500) limit = 100;
    bool includeDev = false;
    if (const js::Value* p = args.find("all")) includeDev = p->asBool();
    bool projectOnly = false;
    if (const js::Value* p = args.find("projectOnly")) projectOnly = p->asBool();
    bool withDeps = false;
    if (const js::Value* p = args.find("withDeps")) withDeps = p->asBool();

    std::vector<ModEntry> mods = scanAllModules(includeDev, (size_t)limit * 4);

    std::string pFile, pDir;
    getProjectPaths(nullptr, &pFile, &pDir);
    std::vector<std::pair<std::string, std::string>> items;
    std::string err;
    bool hasProject = false;
    if (!pFile.empty()) {
        hasProject = readProjectModuleItems(u2w(pFile), items, err);
    }

    js::Value arr = js::Value::arr();
    int count = 0;
    std::set<std::string> projFiles;      // 之前每个模块都线性扫一遍工程模块列表
    for (size_t j = 0; j < items.size(); ++j) projFiles.insert(baseNameOf(items[j].first));
    for (size_t i = 0; i < mods.size(); ++i) {
        bool inProj = projFiles.find(mods[i].file) != projFiles.end();
        if (projectOnly && !inProj) continue;
        if (count >= limit) break;
        js::Value o = js::Value::obj();
        putStr(o, "file", mods[i].file);
        putStr(o, "name", mods[i].name);
        putStr(o, "version", mods[i].ver);
        putStr(o, "author", mods[i].author);
        if (!mods[i].remark.empty()) putStr(o, "remark", mods[i].remark);
        putStr(o, "path", mods[i].path);
        putBool(o, "inProject", inProj);
        if (withDeps && !mods[i].deps.empty()) {
            js::Value da = js::Value::arr();
            for (size_t d = 0; d < mods[i].deps.size(); ++d) {
                da.push(js::Value(mods[i].deps[d]));
            }
            o.set("deps", da);
        }
        arr.push(o);
        ++count;
    }

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putInt(v, "totalScanned", (int)mods.size());
    putInt(v, "count", count);
    std::vector<std::wstring> roots;
    moduleRootsW(includeDev, roots);
    if (!roots.empty()) putStr(v, "rootDir", w2u(roots[0].c_str()));
    if (roots.size() > 1) putStr(v, "devDir", w2u(roots[1].c_str()));
    putStr(v, "ideRoot", w2u(ideRootDirW().c_str()));
    putBool(v, "hasProject", hasProject);
    if (!pFile.empty()) putStr(v, "projectFile", pFile);
    js::Value pj = js::Value::arr();
    for (size_t j = 0; j < items.size(); ++j) {
        js::Value o = js::Value::obj();
        putStr(o, "path", items[j].first);
        putStr(o, "name", items[j].second);
        pj.push(o);
    }
    v.set("projectModules", pj);
    v.set("modules", arr);
    return v.dump();
}

static std::string doModuleSearch(const js::Value& args) {
    std::string q;
    std::string dep;
    if (const js::Value* pq = args.find("query")) {
        if (pq->isStr()) q = pq->asStr();
    }
    if (const js::Value* pd = args.find("dependsOn")) {
        if (pd->isStr()) dep = pd->asStr();
    }
    if (q.empty() && dep.empty()) return jsonErr("缺少参数 query 或 dependsOn");
    int limit = 30;
    if (const js::Value* p = args.find("limit")) {
        if (p->type() == js::NUM) limit = (int)p->asNum();
    }
    if (limit <= 0 || limit > 500) limit = 30;
    bool includeDev = true;
    if (const js::Value* p = args.find("all")) includeDev = p->asBool();

    std::vector<ModEntry> mods = scanAllModules(includeDev, 1200);
    js::Value arr = js::Value::arr();
    int count = 0;
    for (size_t i = 0; i < mods.size(); ++i) {
        int score = 0;
        std::string where;
        if (!q.empty()) {
            if (mods[i].name.find(q) != std::string::npos) { score += 100; where += "name;"; }
            if (mods[i].file.find(q) != std::string::npos) { score += 80; where += "file;"; }
            if (mods[i].remark.find(q) != std::string::npos) { score += 40; where += "remark;"; }
            if (mods[i].author.find(q) != std::string::npos) { score += 10; where += "author;"; }
            for (size_t d = 0; d < mods[i].deps.size(); ++d) {
                if (mods[i].deps[d].find(q) != std::string::npos) { score += 60; where += "deps;"; break; }
            }
        }
        if (!dep.empty()) {
            bool hit = false;
            for (size_t d = 0; d < mods[i].deps.size(); ++d) {
                if (mods[i].deps[d] == dep ||
                    mods[i].deps[d].find(dep) != std::string::npos) { hit = true; break; }
            }
            if (!hit) continue;
            score += 200;
            where += "dependsOn;";
        }
        if (score == 0) continue;
        if (count >= limit) break;
        js::Value o = js::Value::obj();
        putStr(o, "file", mods[i].file);
        putStr(o, "name", mods[i].name);
        putStr(o, "version", mods[i].ver);
        putStr(o, "author", mods[i].author);
        if (!mods[i].remark.empty()) putStr(o, "remark", mods[i].remark);
        putStr(o, "path", mods[i].path);
        putInt(o, "score", score);
        putStr(o, "match", where);
        if (!mods[i].deps.empty()) {
            js::Value da = js::Value::arr();
            for (size_t d = 0; d < mods[i].deps.size(); ++d) {
                da.push(js::Value(mods[i].deps[d]));
            }
            o.set("deps", da);
        }
        arr.push(o);
        ++count;
    }
    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "query", q);
    if (!dep.empty()) putStr(v, "dependsOn", dep);
    putInt(v, "scanned", (int)mods.size());
    putInt(v, "count", count);
    v.set("hits", arr);
    return v.dump();
}

static std::string doModuleAdd(const js::Value& args) {
    const js::Value* pf = args.find("file");
    const js::Value* pn = args.find("name");
    std::string file;
    std::string dispName;
    if (pf && pf->isStr() && !pf->asStr().empty()) {
        file = baseNameOf(pf->asStr());
    } else if (pn && pn->isStr() && !pn->asStr().empty()) {
        std::vector<ModEntry> mods = scanAllModules(true, 1200);
        for (size_t i = 0; i < mods.size(); ++i) {
            if (mods[i].name == pn->asStr() || mods[i].file == pn->asStr()) {
                file = mods[i].file;
                dispName = mods[i].name;
                break;
            }
        }
        if (file.empty()) return jsonErr("找不到该模块: " + pn->asStr());
    } else {
        return jsonErr("需要 file（模块文件名）或 name（模块名）之一");
    }
    if (dispName.empty() && pn && pn->isStr()) dispName = pn->asStr();
    if (dispName.empty()) {
        std::vector<ModEntry> mods = scanAllModules(true, 1200);
        for (size_t i = 0; i < mods.size(); ++i) {
            if (mods[i].file == file) { dispName = mods[i].name; break; }
        }
        if (dispName.empty()) dispName = file;
    }

    std::string pFile, pDir;
    std::string projArg;
    if (const js::Value* p = args.find("project")) {
        if (p->isStr() && !p->asStr().empty()) projArg = p->asStr();
    }
    if (!projArg.empty()) pFile = projArg;
    else getProjectPaths(nullptr, &pFile, &pDir);

    if (pFile.empty()) return jsonErr("取不到工程文件（IDE 未打开工程，或未传 project 参数）");
    if (pFile.size() < 7 || pFile.substr(pFile.size() - 7) != ".xcproj") {
        if (!pDir.empty()) {
            std::string cand = pDir;
            size_t s = cand.find_last_of("\\/");
            std::string leaf = (s == std::string::npos) ? cand : cand.substr(s + 1);
            pFile = cand + "\\" + leaf + ".xcproj";
        }
    }

    std::wstring wx = u2w(pFile);
    std::vector<std::pair<std::string, std::string>> items;
    std::string err;
    if (!readProjectModuleItems(wx, items, err)) return jsonErr(err);

    for (size_t i = 0; i < items.size(); ++i) {
        if (baseNameOf(items[i].first) == file) {
            js::Value v = js::Value::obj();
            putBool(v, "ok", true);
            putBool(v, "added", false);
            putStr(v, "reason", "工程已引用该模块");
            putStr(v, "file", file);
            putStr(v, "projectFile", pFile);
            return v.dump();
        }
    }
    items.push_back(std::make_pair(file, dispName));
    if (!writeProjectModuleItems(wx, items, err)) return jsonErr(err);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putBool(v, "added", true);
    putStr(v, "file", file);
    putStr(v, "name", dispName);
    putStr(v, "projectFile", pFile);
    putInt(v, "moduleCount", (int)items.size());
    putStr(v, "note", "改动已写入 .xcproj；需重启 IDE 或在 IDE 内重新加载工程才会生效");
    return v.dump();
}

static std::string doModuleRemove(const js::Value& args) {
    const js::Value* pf = args.find("file");
    const js::Value* pn = args.find("name");
    std::string target;
    if (pf && pf->isStr() && !pf->asStr().empty()) target = baseNameOf(pf->asStr());
    else if (pn && pn->isStr() && !pn->asStr().empty()) target = pn->asStr();
    else return jsonErr("需要 file 或 name 之一");

    std::string pFile, pDir;
    if (const js::Value* pp = args.find("project")) {
        if (pp->isStr() && !pp->asStr().empty()) pFile = pp->asStr();
    }
    if (pFile.empty()) getProjectPaths(nullptr, &pFile, &pDir);
    if (pFile.empty()) return jsonErr("取不到工程文件（IDE 未打开工程，且未传 project）");

    std::vector<std::pair<std::string, std::string>> items;
    std::string err;
    if (!readProjectModuleItems(u2w(pFile), items, err)) return jsonErr(err);
    for (size_t i = 0; i < items.size(); ++i) {
        items[i].first = xmlUnesc(items[i].first);
        items[i].second = xmlUnesc(items[i].second);
    }

    std::vector<std::pair<std::string, std::string>> keep;
    std::string removed;
    for (size_t i = 0; i < items.size(); ++i) {
        bool hit = (baseNameOf(items[i].first) == target) || (items[i].second == target);
        if (hit && removed.empty()) { removed = items[i].first; continue; }
        keep.push_back(items[i]);
    }
    if (removed.empty()) {
        js::Value v = js::Value::obj();
        putBool(v, "ok", true);
        putBool(v, "removed", false);
        putStr(v, "reason", "工程未引用该模块");
        return v.dump();
    }
    if (!writeProjectModuleItems(u2w(pFile), keep, err)) return jsonErr(err);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putBool(v, "removed", true);
    putStr(v, "file", baseNameOf(removed));
    putStr(v, "projectFile", pFile);
    putInt(v, "moduleCount", (int)keep.size());
    return v.dump();
}

static bool pathExistsW(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static bool dirExistsW(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static void splitList(const std::string& s, std::vector<std::string>& out) {
    std::string cur;
    for (size_t i = 0; i <= s.size(); ++i) {
        char c = (i < s.size()) ? s[i] : ';';
        if (c == ';' || c == ',' || c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            if (!cur.empty()) { out.push_back(cur); cur.clear(); }
        } else {
            cur += c;
        }
    }
}

static std::string joinRel(const std::string& dir, const std::string& rel) {
    if (dir.empty()) return rel;
    char last = dir[dir.size() - 1];
    if (last == '\\' || last == '/') return dir + rel;
    return dir + "\\" + rel;
}

static bool hasDirPrefix(const std::string& rel) {
    return rel.find_first_of("\\/") != std::string::npos;
}

static std::string swapPlatformDir(const std::string& rel, const char* plat) {
    size_t p = rel.find_first_of("\\/");
    std::string rest = (p == std::string::npos) ? rel : rel.substr(p + 1);
    return std::string(plat) + "\\" + rest;
}

static void expandPlatformList(const std::string& base, const std::string& extra,
                               std::vector<std::string>& out64,
                               std::vector<std::string>& out86) {
    std::vector<std::string> items;
    splitList(base, items);
    for (size_t i = 0; i < items.size(); ++i) {
        const std::string& it = items[i];
        if (hasDirPrefix(it)) {
            out64.push_back(it);
            out86.push_back(swapPlatformDir(it, "x86"));
        } else {
            out64.push_back(std::string("x64\\") + it);
            out86.push_back(std::string("x86\\") + it);
        }
    }
    std::vector<std::string> more;
    splitList(extra, more);
    if (!more.empty()) {
        out86.clear();
        for (size_t i = 0; i < more.size(); ++i) {
            const std::string& it = more[i];
            if (hasDirPrefix(it)) out86.push_back(it);
            else out86.push_back(std::string("x86\\") + it);
        }
    }
}

static void checkMissing(const std::string& dir, const std::vector<std::string>& list,
                         std::vector<std::string>& miss) {
    for (size_t i = 0; i < list.size(); ++i) {
        std::wstring full = u2w(joinRel(dir, list[i]));
        if (!pathExistsW(full)) miss.push_back(list[i]);
    }
}

static std::string todayText() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    char buf[32];
    sprintf_s(buf, sizeof(buf), "%04d-%02d-%02d", (int)st.wYear, (int)st.wMonth, (int)st.wDay);
    return std::string(buf);
}

static bool ensureDirW(const std::wstring& dir) {
    if (dir.empty()) return false;
    if (dirExistsW(dir)) return true;
    std::wstring p = dir;
    for (size_t i = 0; i < p.size(); ++i) {
        if (p[i] == L'/') p[i] = L'\\';
    }
    std::wstring cur;
    size_t i = 0;
    if (p.size() >= 2 && p[1] == L':') {
        cur = p.substr(0, 2);
        i = 2;
    }
    while (i < p.size()) {
        if (p[i] == L'\\') { ++i; continue; }
        size_t j = i;
        while (j < p.size() && p[j] != L'\\') ++j;
        cur += L"\\";
        cur += p.substr(i, j - i);
        CreateDirectoryW(cur.c_str(), nullptr);
        i = j;
    }
    return dirExistsW(dir);
}

static std::string doModuleCreate(const js::Value& args) {
    const js::Value* pn = args.find("name");
    if (!pn || !pn->isStr() || pn->asStr().empty()) return jsonErr("缺少参数 name（模块英文标识，如 mytool）");
    std::string eng = pn->asStr();
    for (size_t i = 0; i < eng.size(); ++i) {
        char c = eng[i];
        bool okc = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
        if (!okc) return jsonErr("name 只能是字母/数字/下划线");
    }
    std::string alias = eng;
    if (const js::Value* p = args.find("alias")) {
        if (p->isStr() && !p->asStr().empty()) alias = p->asStr();
    }
    std::string author = "XC-MCP-Next";
    if (const js::Value* p = args.find("author")) {
        if (p->isStr() && !p->asStr().empty()) author = p->asStr();
    }
    std::string remark;
    if (const js::Value* p = args.find("remark")) {
        if (p->isStr()) remark = p->asStr();
    }
    std::string libs;
    std::string libs86;
    std::string dlls;
    std::string dlls86;
    std::string includes;
    if (const js::Value* p = args.find("libs")) {
        if (p->isStr()) libs = p->asStr();
    }
    if (const js::Value* p = args.find("libs86")) {
        if (p->isStr()) libs86 = p->asStr();
    }
    if (const js::Value* p = args.find("dlls")) {
        if (p->isStr()) dlls = p->asStr();
    }
    if (const js::Value* p = args.find("dlls86")) {
        if (p->isStr()) dlls86 = p->asStr();
    }
    if (const js::Value* p = args.find("includes")) {
        if (p->isStr()) includes = p->asStr();
    }
    bool addToProject = true;
    if (const js::Value* p = args.find("addToProject")) addToProject = p->asBool();
    bool force = false;
    if (const js::Value* p = args.find("force")) force = p->asBool();

    std::string location = "project";
    if (const js::Value* p = args.find("location")) {
        if (p->isStr() && !p->asStr().empty()) location = p->asStr();
    }
    if (location != "project" && location != "dev" && location != "main") {
        return jsonErr("location 只能是 project / dev / main");
    }

    std::string sub;
    if (const js::Value* pd = args.find("dir")) {
        if (pd->isStr()) sub = pd->asStr();
    }
    if (sub.empty()) {
        if (const js::Value* ps = args.find("subdir")) {
            if (ps->isStr()) sub = ps->asStr();
        }
    }

    std::string dir;
    std::string moduleRoot;
    if (location == "project") {
        if (!sub.empty()) dir = sub;
        if (dir.empty()) {
            std::string pDir;
            getProjectPaths(nullptr, nullptr, &pDir);
            dir = pDir;
        }
        if (dir.empty()) return jsonErr("未传 dir 且取不到当前工程目录");
    } else {
        std::vector<std::wstring> mroots;
        moduleRootsW(true, mroots);
        size_t idx = (location == "dev") ? 1 : 0;
        if (mroots.size() <= idx) {
            return jsonErr(location == "dev" ? "找不到 XC-Module-dev 目录" : "找不到 XC-Module 目录");
        }
        std::wstring base = mroots[idx];
        if (!sub.empty()) {
            std::wstring sw = u2w(sub);
            for (size_t i = 0; i < sw.size(); ++i) {
                if (sw[i] == L'/') sw[i] = L'\\';
            }
            while (!sw.empty() && (sw[0] == L'\\' || sw[0] == L'/')) sw.erase(0, 1);
            size_t segStart = 0;
            while (segStart <= sw.size()) {
                size_t segEnd = sw.find(L'\\', segStart);
                if (segEnd == std::wstring::npos) segEnd = sw.size();
                std::wstring seg = sw.substr(segStart, segEnd - segStart);
                if (seg == L"..") return jsonErr("dir 不允许包含 .. 段");
                if (seg.find(L':') != std::wstring::npos) return jsonErr("dir 不允许包含冒号或盘符");
                if (segEnd >= sw.size()) break;
                segStart = segEnd + 1;
            }
            if (!sw.empty()) base = base + L"\\" + sw;
        }
        wchar_t fb[MAX_PATH * 2];
        DWORD fl = GetFullPathNameW(base.c_str(), MAX_PATH * 2, fb, nullptr);
        if (fl == 0 || fl >= MAX_PATH * 2) return jsonErr("模块目录规范化失败");
        std::wstring fullBase(fb, fl);
        std::wstring rootExact = mroots[idx];
        std::wstring allowPrefix = rootExact + L"\\";
        bool insideRoot = (_wcsicmp(fullBase.c_str(), rootExact.c_str()) == 0) ||
                          (_wcsnicmp(fullBase.c_str(), allowPrefix.c_str(), allowPrefix.size()) == 0);
        if (!insideRoot) {
            return jsonErr("dir 越出模块库根目录");
        }
        base = fullBase;
        if (!ensureDirW(base)) {
            return jsonErr("创建模块目录失败: " + w2u(base.c_str()));
        }
        dir = w2u(base.c_str());
        moduleRoot = w2u(mroots[idx].c_str());
    }

    std::string hName = "module_" + eng + ".h";
    std::string cName = "module_" + eng + ".cpp";
    std::string sep = (dir.find_last_of("\\/") == dir.size() - 1) ? "" : "\\";

    std::vector<std::string> vLibs64, vLibs86, vDlls64, vDlls86, vInc, vMiss, vWarn;
    expandPlatformList(libs, libs86, vLibs64, vLibs86);
    expandPlatformList(dlls, dlls86, vDlls64, vDlls86);
    splitList(includes, vInc);
    checkMissing(dir, vLibs64, vMiss);
    checkMissing(dir, vLibs86, vMiss);
    checkMissing(dir, vDlls64, vMiss);
    checkMissing(dir, vDlls86, vMiss);
    bool has86 = !vLibs86.empty() || !vDlls86.empty();
    if (has86) {
        if (dirExistsW(u2w(joinRel(dir, "x64"))) == false) {
            vWarn.push_back("模块目录下没有 x64\\ 子目录");
        }
        if (dirExistsW(u2w(joinRel(dir, "x86"))) == false) {
            vWarn.push_back("模块目录下没有 x86\\ 子目录，生成的 32 位分支会链接失败");
        }
    }

    std::string h, c;
    h += "#pragma once\n";
    h += "//@模块名称  " + alias + "\n";
    h += "//@版本  1.0.0\n";
    h += "//@日期  " + todayText() + "\n";
    h += "//@作者  " + author + "\n";
    if (!remark.empty()) h += "//@模块备注  " + remark + "\n";
    h += "//@依赖  module_base.h\n";
    if (!vLibs64.empty() || !vDlls64.empty() || has86) {
        h += "#ifdef _WIN64\n";
        for (size_t i = 0; i < vLibs64.size(); ++i) {
            h += "//@lib  \"" + vLibs64[i] + "\"\n";
        }
        for (size_t i = 0; i < vDlls64.size(); ++i) {
            h += "//@复制文件 @当前模块路径 \"" + vDlls64[i] + "\"\n";
        }
        if (has86) {
            h += "#else\n";
            for (size_t i = 0; i < vLibs86.size(); ++i) {
                h += "//@lib  \"" + vLibs86[i] + "\"\n";
            }
            for (size_t i = 0; i < vDlls86.size(); ++i) {
                h += "//@复制文件 @当前模块路径 \"" + vDlls86[i] + "\"\n";
            }
        }
        h += "#endif\n";
    }
    for (size_t i = 0; i < vInc.size(); ++i) {
        h += "//@包含目录 @当前模块路径 \"" + vInc[i] + "\"\n";
    }
    h += "//@src \"" + cName + "\"\n";
    h += "//@模块信息结束\n";
    h += "#if defined(XC_MOUDLE) || defined(XC_MODULE)\n";
    h += "#include \"module_base.h\"\n";
    h += "#endif\n";
    h += "\n";
    h += "//@分组{ " + alias + "\n";
    h += "//@别名  " + alias + "_初始化()\n";
    h += "//@备注  模块初始化占位函数\n";
    h += "//@返回  成功返回真\n";
    h += "int xcl_" + eng + "_init();\n";
    h += "//@分组}\n";

    c += "#include \"" + hName + "\"\n";
    c += "\n";
    c += "int xcl_" + eng + "_init() {\n";
    c += "    return 1;\n";
    c += "}\n";

    std::string hPath = dir + sep + hName;
    std::string cPath = dir + sep + cName;
    std::string err;
    bool hExisted = (GetFileAttributesW(u2w(hPath).c_str()) != INVALID_FILE_ATTRIBUTES);
    bool cExisted = (GetFileAttributesW(u2w(cPath).c_str()) != INVALID_FILE_ATTRIBUTES);
    if ((hExisted || cExisted) && !force) {
        js::Value ev = js::Value::obj();
        putBool(ev, "ok", false);
        putStr(ev, "error", "目标模块文件已存在，未覆盖（加 force=true 可强制）");
        putStr(ev, "header", hPath);
        putStr(ev, "source", cPath);
        putBool(ev, "headerExisted", hExisted);
        putBool(ev, "needForce", true);
        return ev.dump();
    }
    if (!writeFileKeepEnc(u2w(hPath), h, err)) {
        err = "写头文件失败: " + err;
        return jsonErr(err);
    }
    if (!writeFileKeepEnc(u2w(cPath), c, err)) return jsonErr(err);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "header", hPath);
    putStr(v, "source", cPath);
    putStr(v, "module", alias);
    putBool(v, "headerExisted", hExisted);
    putStr(v, "location", location);
    if (!moduleRoot.empty()) putStr(v, "moduleRoot", moduleRoot);
    putStr(v, "moduleDir", dir);
    if (!vLibs64.empty() || !vDlls64.empty() || has86) {
        js::Value l64 = js::Value::arr();
        for (size_t i = 0; i < vLibs64.size(); ++i) l64.push(js::Value(vLibs64[i]));
        js::Value d64 = js::Value::arr();
        for (size_t i = 0; i < vDlls64.size(); ++i) d64.push(js::Value(vDlls64[i]));
        v.set("libsX64", l64);
        v.set("dllsX64", d64);
        if (has86) {
            js::Value l86 = js::Value::arr();
            for (size_t i = 0; i < vLibs86.size(); ++i) l86.push(js::Value(vLibs86[i]));
            js::Value d86 = js::Value::arr();
            for (size_t i = 0; i < vDlls86.size(); ++i) d86.push(js::Value(vDlls86[i]));
            v.set("libsX86", l86);
            v.set("dllsX86", d86);
        }
    }
    if (!vInc.empty()) {
        js::Value ia = js::Value::arr();
        for (size_t i = 0; i < vInc.size(); ++i) ia.push(js::Value(vInc[i]));
        v.set("includes", ia);
    }
    if (!vMiss.empty()) {
        js::Value ma = js::Value::arr();
        for (size_t i = 0; i < vMiss.size(); ++i) ma.push(js::Value(vMiss[i]));
        v.set("missing", ma);
        vWarn.push_back("以下库文件在模块目录下不存在，编译会 LNK1104");
    }
    if (!vWarn.empty()) {
        js::Value wa = js::Value::arr();
        for (size_t i = 0; i < vWarn.size(); ++i) wa.push(js::Value(vWarn[i]));
        v.set("warnings", wa);
    }

    if (addToProject) {
        js::Value a = js::Value::obj();
        a.set("file", js::Value(hName));
        a.set("name", js::Value(alias));
        std::string r = doModuleAdd(a);
        if (r.find("\"ok\":true") != std::string::npos) {
            putBool(v, "addedToProject", true);
            putStr(v, "addResult", r.size() > 900 ? r.substr(0, 900) : r);
        } else {
            putBool(v, "addedToProject", false);
            putStr(v, "addError", r.size() > 900 ? r.substr(0, 900) : r);
        }
    } else {
        putBool(v, "addedToProject", false);
    }
    return v.dump();
}

static std::string doSaveAll(const js::Value& args) {
    (void)args;
    bool ok = postKeyToIde('S', true);
    js::Value v = js::Value::obj();
    putBool(v, "ok", ok);
    putStr(v, "channel", "PostMessage Ctrl+S");
    if (!ok) putStr(v, "error", "主窗口句柄无效");
    else putStr(v, "note", "已投递保存快捷键；IDE 内「保存全部」可能绑定其他快捷键，请核对一次");
    return v.dump();
}


static std::string doNewFile(const js::Value& args) {
    const js::Value* pn = args.find("name");
    if (!pn || !pn->isStr() || pn->asStr().empty()) return jsonErr("缺少参数 name（不含扩展名）");
    std::string name = pn->asStr();

    std::string ext = "xc";
    if (const js::Value* pe = args.find("type")) {
        if (pe->isStr() && !pe->asStr().empty()) ext = pe->asStr();
    }
    if (ext != "xc" && ext != "xh") return jsonErr("type 只能是 xc 或 xh");

    if (name.find('\\') != std::string::npos || name.find('/') != std::string::npos) {
        return jsonErr("name 不能包含路径分隔符");
    }

    std::string dir;
    void* p1 = nullptr; void* p2 = nullptr; void* p3 = nullptr;
    getProjectRaw(&p1, &p2, &p3);
    if (p3) dir = w2u((const wchar_t*)p3);
    if (dir.empty()) return jsonErr("取不到当前工程目录");

    std::string fullName = name + "." + ext;
    std::wstring wdir = u2w(dir);
    std::wstring wpath = wdir;
    wpath += L'\\';
    wpath += u2w(fullName);

    if (GetFileAttributesW(wpath.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return jsonErr("文件已存在: " + dir + "\\" + fullName);
    }

    std::string body;
    if (ext == "xh") {
        body = "//预编译头文件\r\n";
    } else {
        body = "\r\n";
    }

    std::string err;
    if (!writeFileKeepEnc(wpath, body, err)) return jsonErr("创建失败: " + err);

    bool imported = importOne(wpath, true);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putStr(v, "file", dir + "\\" + fullName);
    putBool(v, "imported", imported);
    putStr(v, "channel", "disk+importFile");
    if (!imported) putStr(v, "note", "文件已创建但未加入项目树，可再调 xc_add_file");
    return v.dump();
}

static std::string doProbeCmd(const js::Value& args) {
    const js::Value* pc = args.find("cmd_id");
    if (!pc) return jsonErr("缺少参数 cmd_id");
    int cmdId = pc->asInt(0);
    int arg0 = 0;
    if (const js::Value* pa = args.find("arg0")) arg0 = pa->asInt(0);
    bool allowUnsafe = false;
    if (const js::Value* pu = args.find("allow_unsafe")) allowUnsafe = pu->asBool(false);

    if (cmdId < 1 || cmdId > 21) {
        return jsonErr("cmd_id 必须在 1..21 之间（对应 funSet 的 type 编号）");
    }

    static const int kSafe[] = {20, 21};
    bool safe = false;
    for (size_t i = 0; i < sizeof(kSafe) / sizeof(kSafe[0]); ++i) {
        if (cmdId == kSafe[i]) safe = true;
    }

    std::string confirm;
    if (const js::Value* pcv = args.find("confirm")) {
        if (pcv->isStr()) confirm = pcv->asStr();
    }
    static const char* kConfirm = "I-UNDERSTAND-THE-RISK";

    if (!safe && !allowUnsafe) {
        js::Value v = js::Value::obj();
        putBool(v, "ok", false);
        putInt(v, "cmd_id", cmdId);
        putStr(v, "error", "该 cmd_id 未经验证，可能使 IDE 崩溃");
        putStr(v, "hint", std::string("确认风险后同时传 allow_unsafe=true 与 confirm=") + kConfirm);
        putStr(v, "verified_ids", "20, 21");
        return v.dump();
    }
    if (!safe && confirm != kConfirm) {
        js::Value v = js::Value::obj();
        putBool(v, "ok", false);
        putInt(v, "cmd_id", cmdId);
        putStr(v, "error", "allow_unsafe 已给但缺少二次确认");
        putStr(v, "hint", std::string("请同时传 confirm=") + kConfirm);
        return v.dump();
    }

    Data d;
    memset(&d, 0, sizeof(d));
    d.type = cmdId;
    d.id = arg0;
    int rc = xi::call(d);

    js::Value v = js::Value::obj();
    putBool(v, "ok", true);
    putInt(v, "cmd_id", cmdId);
    putInt(v, "arg0", arg0);
    putInt(v, "rc", rc);
    putBool(v, "unsafe", !safe);
    putStr(v, "dump", hexDump((const unsigned char*)&d, 128));
    return v.dump();
}

static std::string notImpl(const char* name) {
    js::Value v = js::Value::obj();
    putBool(v, "ok", false);
    putStr(v, "error", std::string("工具尚未实现: ") + name);
    return v.dump();
}

static std::string callImpl(const std::string& name, const js::Value& args, bool& isError) {
    isError = false;
    if (name == "xc_status") return doStatus();
    if (name == "xc_get_project") return doGetProject();
    if (name == "xc_parse_status") return doParseStatus();
    if (name == "xc_reset_parse_count") return doResetParse();
    if (name == "xc_ide_log") return doIdeLog(args);
    if (name == "xc_get_output") return doGetOutput(args);
    if (name == "xc_get_dialogs") {
        js::Value result = dialogs::read(args);
        isError = !result.find("ok")->asBool();
        return result.dump();
    }
    if (name == "xc_read_file") return doReadFile(args);
    if (name == "xc_list_projects") return doListProjects(args);
    if (name == "xc_server") {
        const js::Value* p = args.find("action");
        std::string a = (p && p->isStr()) ? p->asStr() : "status";
        js::Value v = js::Value::obj();
        if (a == "start") {
            bool ok = http::running() || http::start(http::port(), nullptr);
            putBool(v, "ok", ok);
            putBool(v, "running", http::running());
        } else if (a == "stop") {
            http::stop(true);      // 由工具线程发起：不能等自己这条连接
            putBool(v, "ok", true);
            putBool(v, "running", false);
        } else {
            putBool(v, "ok", true);
            putBool(v, "running", http::running());
            putInt(v, "port", http::port());
            putStr(v, "instanceId", http::instanceId());
            putInt(v, "pid", (int)GetCurrentProcessId());
        }
        return v.dump();
    }
    if (name == "xc_editor_get") return doEditorGet(args);
    if (name == "xc_editor_set") return doEditorSet(args);
    if (name == "xc_symbol") return doSymbol(args);
    if (name == "xc_goto_function") return doGotoFunction(args);
    if (name == "xc_tree_probe") return doTreeProbe(args);
    if (name == "xc_list_functions") return doListFunctions(args);
    if (name == "xc_add_file") return doAddFile(args);
    if (name == "xc_open_in_ide") return doOpenInIde(args);
    if (name == "xc_add_dir_files") return doAddDirFiles(args);
    if (name == "xc_new_file") return doNewFile(args);
    if (name == "xc_probe_cmd") return doProbeCmd(args);
    if (name == "xc_module_info") return doModuleInfo(args);
    if (name == "xc_module_list") return doModuleList(args);
    if (name == "xc_module_search") return doModuleSearch(args);
    if (name == "xc_module_add") return doModuleAdd(args);
    if (name == "xc_module_remove") return doModuleRemove(args);
    if (name == "xc_module_create") return doModuleCreate(args);
    if (name == "xc_save_all") return doSaveAll(args);
    if (name == "xc_build") return doBuild(args);
    if (name == "xc_menu_cmd") return doMenuCmd(args);
    isError = true;
    return notImpl(name.c_str());
}

std::string call(const std::string& name, const js::Value& args, bool& isError) {
    try {
        return callImpl(name, args, isError);
    } catch (const std::exception& e) {
        isError = true;
        return jsonErr(std::string("工具内部异常: ") + e.what());
    } catch (...) {
        isError = true;
        return jsonErr("工具内部异常: 未知错误");
    }
}

}
