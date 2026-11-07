#include "mcp.hpp"
#include "json.hpp"
#include "tools.hpp"
#include "mcp_log.hpp"
#include "plug_abi.h"

#include <string>
#include <utility>

namespace mcp {

namespace {

const char* kProtocolVersion = "2024-11-05";
const char* kServerName = "xcide-mcp";
const char* kServerVersion = "1.2.0";

bool validUtf8(const std::string& in) {
    size_t i = 0;
    while (i < in.size()) {
        unsigned char c = (unsigned char)in[i];
        size_t len = 0;
        unsigned int cp = 0;
        if (c < 0x80) { ++i; continue; }
        else if ((c & 0xE0) == 0xC0) { len = 2; cp = c & 0x1F; }
        else if ((c & 0xF0) == 0xE0) { len = 3; cp = c & 0x0F; }
        else if ((c & 0xF8) == 0xF0) { len = 4; cp = c & 0x07; }
        else return false;

        if (i + len > in.size()) return false;

        for (size_t k = 1; k < len; ++k) {
            unsigned char cc = (unsigned char)in[i + k];
            if ((cc & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (unsigned int)(cc & 0x3F);
        }
        if ((len == 2 && cp < 0x80u) || (len == 3 && cp < 0x800u) || (len == 4 && cp < 0x10000u))
            return false;
        if (cp >= 0xD800u && cp <= 0xDFFFu) return false;
        if (cp > 0x10FFFFu) return false;
        i += len;
    }
    return true;
}

// 合法 UTF-8（绝大多数返回）直接原样返回，省掉一次整份响应复制（xc_read_file 可达数 MB）。
std::string sanitizeUtf8(std::string in) {
    if (validUtf8(in)) return in;

    static const char kRepl[] = "\xEF\xBF\xBD";
    std::string out;
    out.reserve(in.size());
    size_t i = 0;
    while (i < in.size()) {
        unsigned char c = (unsigned char)in[i];
        size_t len = 0;
        if (c < 0x80) len = 1;
        else if ((c & 0xE0) == 0xC0) len = 2;
        else if ((c & 0xF0) == 0xE0) len = 3;
        else if ((c & 0xF8) == 0xF0) len = 4;
        else { out += kRepl; ++i; continue; }

        if (i + len > in.size()) { out += kRepl; break; }

        unsigned int cp;
        if (len == 1) cp = c;
        else if (len == 2) cp = c & 0x1F;
        else if (len == 3) cp = c & 0x0F;
        else cp = c & 0x07;

        bool ok = true;
        for (size_t k = 1; k < len; ++k) {
            unsigned char cc = (unsigned char)in[i + k];
            if ((cc & 0xC0) != 0x80) { ok = false; break; }
            cp = (cp << 6) | (unsigned int)(cc & 0x3F);
        }
        if (!ok) { out += kRepl; ++i; continue; }

        if ((len == 2 && cp < 0x80u) || (len == 3 && cp < 0x800u) || (len == 4 && cp < 0x10000u)) {
            out += kRepl; ++i; continue;
        }
        if (cp >= 0xD800u && cp <= 0xDFFFu) { out += kRepl; i += len; continue; }
        if (cp > 0x10FFFFu) { out += kRepl; i += len; continue; }

        out.append(in, i, len);
        i += len;
    }
    return out;
}

std::string resultEnvelope(const js::Value& id, const js::Value& result) {
    js::Value v = js::Value::obj();
    v.set("jsonrpc", std::string("2.0"));
    v.set("id", id);
    v.set("result", result);
    return v.dump();
}

std::string errorEnvelope(const js::Value& id, int code, const std::string& message) {
    js::Value err = js::Value::obj();
    mcp_log::outcome("JSON-RPC error " + std::to_string(code));
    err.set("code", code);
    err.set("message", message);
    js::Value v = js::Value::obj();
    v.set("jsonrpc", std::string("2.0"));
    v.set("id", id);
    v.set("error", err);
    return v.dump();
}

js::Value buildInitialize() {
    js::Value tools = js::Value::obj();
    tools.set("listChanged", false);
    js::Value caps = js::Value::obj();
    caps.set("tools", tools);
    js::Value info = js::Value::obj();
    info.set("name", std::string(kServerName));
    info.set("version", std::string(kServerVersion));
    js::Value r = js::Value::obj();
    r.set("protocolVersion", std::string(kProtocolVersion));
    r.set("capabilities", caps);
    r.set("serverInfo", info);
    return r;
}

js::Value textContent(std::string&& text) {
    js::Value item = js::Value::obj();
    item.set("type", std::string("text"));
    item.set("text", std::move(text));
    js::Value arr = js::Value::arr();
    arr.push(item);
    return arr;
}

std::string doToolsCall(const js::Value& id, const js::Value& params) {
    const js::Value* pn = params.find("name");
    if (!pn || !pn->isStr()) {
        return errorEnvelope(id, -32602, "Invalid params: missing tool name");
    }
    std::string name = pn->asStr();

    js::Value emptyArgs = js::Value::obj();
    const js::Value* pa = params.find("arguments");
    const js::Value& args = (pa && pa->isObj()) ? *pa : emptyArgs;

    bool isError = false;
    std::string out = tools::call(name, args, isError);
    out = sanitizeUtf8(std::move(out));
    mcp_log::outcome(isError ? "tool error" : "tool result");

    js::Value r = js::Value::obj();
    r.set("content", textContent(std::move(out)));
    if (isError) r.set("isError", true);
    return resultEnvelope(id, r);
}

}

std::string handleOne(const js::Value& req);

std::string handle(const std::string& requestBody) {
    js::Value req;
    std::string perr;
    if (!js::parse(requestBody, req, &perr)) {
        js::Value nul;
        mcp_log::outcome("invalid JSON");
        return errorEnvelope(nul, -32700, std::string("Parse error: ") + perr);
    }

    if (req.isArr()) {
        size_t n = req.items().size();
        if (n == 0) {
            js::Value nul;
            return errorEnvelope(nul, -32600, "Invalid Request: empty batch");
        }
        std::string out = "[";
        bool first = true;
        for (size_t i = 0; i < n; ++i) {
            std::string one = handleOne(req.items()[i]);
            if (one.empty()) continue;
            if (!first) out += ",";
            out += one;
            first = false;
        }
        out += "]";
        if (first) return std::string();
        return out;
    }

    return handleOne(req);
}

std::string handleOne(const js::Value& req) {
    if (!req.isObj()) {
        js::Value nul;
        return errorEnvelope(nul, -32600, "Invalid Request");
    }

    const js::Value* pm = req.find("method");
    if (!pm || !pm->isStr()) {
        js::Value id = req.find("id") ? *req.find("id") : js::Value();
        return errorEnvelope(id, -32600, "Invalid Request: missing method");
    }
    std::string method = pm->asStr();
    js::Value idv;
    const js::Value* pid = req.find("id");
    bool hasId = (pid != nullptr);
    if (hasId) idv = *pid;

    js::Value emptyParams = js::Value::obj();
    const js::Value* pp = req.find("params");
    const js::Value& params = (pp && pp->isObj()) ? *pp : emptyParams;
    const js::Value* toolName = method == "tools/call" ? params.find("name") : nullptr;
    const std::string logId = !hasId ? std::string() :
                              (idv.isObj() || idv.isArr() ? std::string("invalid-id") : idv.dump());
    mcp_log::rpc(method, logId,
                 toolName && toolName->isStr() ? toolName->asStr() : std::string());

    if (method == "initialize") {
        return resultEnvelope(idv, buildInitialize());
    }

    if (method == "notifications/initialized" ||
        method == "notifications/cancelled" ||
        method == "notifications/roots/list_changed") {
        return std::string();
    }

    if (method == "ping") {
        return resultEnvelope(idv, js::Value::obj());
    }

    if (method == "tools/list") {
        js::Value r = js::Value::obj();
        r.set("tools", tools::listCached());
        return resultEnvelope(idv, r);
    }

    if (method == "tools/call") {
        return doToolsCall(idv, params);
    }

    if (method == "resources/list") {
        js::Value r = js::Value::obj();
        r.set("resources", js::Value::arr());
        return resultEnvelope(idv, r);
    }

    if (method == "prompts/list") {
        js::Value r = js::Value::obj();
        r.set("prompts", js::Value::arr());
        return resultEnvelope(idv, r);
    }

    if (!hasId) {
        return std::string();
    }

    return errorEnvelope(idv, -32601, std::string("Method not found: ") + method);
}

}