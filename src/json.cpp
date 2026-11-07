#include "json.hpp"

#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstring>
#include <utility>

namespace js {

Value Value::obj() {
    Value v;
    v.t_ = OBJ;
    return v;
}

Value Value::arr() {
    Value v;
    v.t_ = ARR;
    return v;
}

bool Value::asBool(bool def) const {
    if (t_ == BOOL) return b_;
    if (t_ == NUM) return n_ != 0;
    return def;
}

double Value::asNum(double def) const {
    if (t_ == NUM) return n_;
    if (t_ == BOOL) return b_ ? 1 : 0;
    if (t_ == STR) {
        char* end = nullptr;
        double d = strtod(s_.c_str(), &end);
        if (end && *end == 0) return d;
    }
    return def;
}

int Value::asInt(int def) const {
    double d = asNum((double)def);
    if (d != d) return def;
    if (d > 2147483647.0) return 2147483647;
    if (d < -2147483647.0) return -2147483647;
    return (int)d;
}

std::string Value::asStr(const std::string& def) const {
    if (t_ == STR) return s_;
    return def;
}

const Value* Value::find(const std::string& key) const {
    if (t_ != OBJ) return nullptr;
    auto it = o_.find(key);
    return it == o_.end() ? nullptr : &it->second;
}

bool Value::has(const std::string& key) const {
    return find(key) != nullptr;
}

// Serialize an already-encoded JSON text verbatim, no re-parse.
Value Value::makeRaw(std::string&& text) {
    Value v;
    v.t_ = RAW;
    v.s_ = std::move(text);
    return v;
}

bool rawJson(std::string text, Value& out) {
    if (text.empty()) return false;
    out = Value::makeRaw(std::move(text));
    return true;
}

void Value::set(const std::string& key, const Value& v) {
    if (t_ != OBJ) { t_ = OBJ; }
    o_[key] = v;
}

void Value::set(const std::string& key, Value&& v) {
    if (t_ != OBJ) { t_ = OBJ; }
    o_[key] = std::move(v);
}

void Value::set(const std::string& key, const std::string& v) {
    if (t_ != OBJ) { t_ = OBJ; }
    o_[key] = Value(v);
}

void Value::set(const std::string& key, std::string&& v) {
    if (t_ != OBJ) { t_ = OBJ; }
    Value nv;
    nv.t_ = STR;
    nv.s_ = std::move(v);
    o_[key] = std::move(nv);
}

void Value::set(const std::string& key, int v) {
    set(key, Value((double)v));
}

void Value::set(const std::string& key, bool v) {
    set(key, Value(v));
}

void Value::push(const Value& v) {
    if (t_ != ARR) { t_ = ARR; }
    a_.push_back(v);
}

std::string escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 16);
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b";  break;
            case '\f': out += "\\f";  break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    sprintf_s(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    return out;
}

static void numToStr(double d, std::string& out) {
    if (d != d || d > 1e308 || d < -1e308) {
        out += "0";
        return;
    }
    if (d == (double)(long long)d && fabs(d) < 1e15) {
        char buf[32];
        sprintf_s(buf, sizeof(buf), "%lld", (long long)d);
        out += buf;
        return;
    }
    char buf[40];
    sprintf_s(buf, sizeof(buf), "%.17g", d);
    out += buf;
}

void Value::dumpTo(std::string& out) const {
    switch (t_) {
        case NUL:  out += "null"; break;
        case BOOL: out += b_ ? "true" : "false"; break;
        case NUM:  numToStr(n_, out); break;
        case STR:  out += '"'; out += escape(s_); out += '"'; break;
        case RAW:  out += s_; break;
        case ARR: {
            out += '[';
            for (size_t i = 0; i < a_.size(); ++i) {
                if (i) out += ',';
                a_[i].dumpTo(out);
            }
            out += ']';
            break;
        }
        case OBJ: {
            out += '{';
            bool first = true;
            for (auto it = o_.begin(); it != o_.end(); ++it) {
                if (!first) out += ',';
                first = false;
                out += '"';
                out += escape(it->first);
                out += "\":";
                it->second.dumpTo(out);
            }
            out += '}';
            break;
        }
    }
}

std::string Value::dump() const {
    std::string out;
    out.reserve(256);
    dumpTo(out);
    return out;
}

namespace {

struct Parser {
    const char* p;
    const char* end;
    std::string err;

    void ws() {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) ++p;
    }

    bool fail(const char* msg) {
        if (err.empty()) err = msg;
        return false;
    }

    static void utf8Append(std::string& out, unsigned cp) {
        if (cp < 0x80) {
            out += (char)cp;
        } else if (cp < 0x800) {
            out += (char)(0xC0 | (cp >> 6));
            out += (char)(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += (char)(0xE0 | (cp >> 12));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        } else {
            out += (char)(0xF0 | (cp >> 18));
            out += (char)(0x80 | ((cp >> 12) & 0x3F));
            out += (char)(0x80 | ((cp >> 6) & 0x3F));
            out += (char)(0x80 | (cp & 0x3F));
        }
    }

    bool hex4(unsigned& out) {
        if (end - p < 4) return fail("bad \\u");
        out = 0;
        for (int i = 0; i < 4; ++i) {
            char c = *p++;
            unsigned d;
            if (c >= '0' && c <= '9') d = c - '0';
            else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
            else return fail("bad hex");
            out = out * 16 + d;
        }
        return true;
    }

    bool str(std::string& out) {
        if (p >= end || *p != '"') return fail("expect quote");
        ++p;
        out.clear();
        while (p < end) {
            char c = *p++;
            if (c == '"') return true;
            if (c == '\\') {
                if (p >= end) return fail("bad escape");
                char e = *p++;
                switch (e) {
                    case '"':  out += '"';  break;
                    case '\\': out += '\\'; break;
                    case '/':  out += '/';  break;
                    case 'b':  out += '\b'; break;
                    case 'f':  out += '\f'; break;
                    case 'n':  out += '\n'; break;
                    case 'r':  out += '\r'; break;
                    case 't':  out += '\t'; break;
                    case 'u': {
                        unsigned cp = 0;
                        if (!hex4(cp)) return false;
                        if (cp >= 0xD800 && cp <= 0xDBFF && end - p >= 6 && p[0] == '\\' && p[1] == 'u') {
                            const char* save = p;
                            p += 2;
                            unsigned lo = 0;
                            if (hex4(lo) && lo >= 0xDC00 && lo <= 0xDFFF) {
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            } else {
                                p = save;
                            }
                        }
                        utf8Append(out, cp);
                        break;
                    }
                    default: return fail("bad escape char");
                }
            } else {
                out += c;
            }
        }
        return fail("unterminated string");
    }

    bool value(Value& out, int depth) {
        if (depth > 64) return fail("too deep");
        ws();
        if (p >= end) return fail("eof");
        char c = *p;
        if (c == '{') return object(out, depth);
        if (c == '[') return array(out, depth);
        if (c == '"') {
            std::string s;
            if (!str(s)) return false;
            out = Value(s);
            return true;
        }
        if (c == 't') {
            if (end - p >= 4 && !strncmp(p, "true", 4)) { p += 4; out = Value(true); return true; }
            return fail("bad literal");
        }
        if (c == 'f') {
            if (end - p >= 5 && !strncmp(p, "false", 5)) { p += 5; out = Value(false); return true; }
            return fail("bad literal");
        }
        if (c == 'n') {
            if (end - p >= 4 && !strncmp(p, "null", 4)) { p += 4; out = Value(); return true; }
            return fail("bad literal");
        }
        {
            char* e = nullptr;
            double d = strtod(p, &e);
            if (e == p) return fail("bad number");
            p = e;
            out = Value(d);
            return true;
        }
    }

    bool object(Value& out, int depth) {
        ++p;
        out = Value::obj();
        ws();
        if (p < end && *p == '}') { ++p; return true; }
        while (p < end) {
            ws();
            std::string key;
            if (!str(key)) return false;
            ws();
            if (p >= end || *p != ':') return fail("expect colon");
            ++p;
            Value v;
            if (!value(v, depth + 1)) return false;
            out.set(key, v);
            ws();
            if (p < end && *p == ',') { ++p; continue; }
            if (p < end && *p == '}') { ++p; return true; }
            return fail("expect , or }");
        }
        return fail("unterminated object");
    }

    bool array(Value& out, int depth) {
        ++p;
        out = Value::arr();
        ws();
        if (p < end && *p == ']') { ++p; return true; }
        while (p < end) {
            Value v;
            if (!value(v, depth + 1)) return false;
            out.push(v);
            ws();
            if (p < end && *p == ',') { ++p; continue; }
            if (p < end && *p == ']') { ++p; return true; }
            return fail("expect , or ]");
        }
        return fail("unterminated array");
    }
};

}

bool parse(const std::string& text, Value& out, std::string* err) {
    Parser ps;
    ps.p = text.c_str();
    ps.end = text.c_str() + text.size();
    if (text.size() >= 3 &&
        (unsigned char)text[0] == 0xEF &&
        (unsigned char)text[1] == 0xBB &&
        (unsigned char)text[2] == 0xBF) {
        ps.p += 3;
    }
    if (!ps.value(out, 0)) {
        if (err) *err = ps.err;
        return false;
    }
    ps.ws();
    if (ps.p != ps.end) {
        if (err) *err = "trailing content";
        return false;
    }
    return true;
}

}
