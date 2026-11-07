#pragma once

#include <string>
#include <vector>
#include <map>
#include <utility>

namespace js {

// RAW holds pre-encoded JSON text; it is emitted verbatim by dump().
enum Type { NUL, BOOL, NUM, STR, ARR, OBJ, RAW };

class Value {
public:
    Value() : t_(NUL), b_(false), n_(0) {}
    explicit Value(bool v) : t_(BOOL), b_(v), n_(0) {}
    explicit Value(double v) : t_(NUM), b_(false), n_(v) {}
    explicit Value(const std::string& v) : t_(STR), b_(false), n_(0), s_(v) {}

    static Value obj();
    static Value arr();

    Type type() const { return t_; }
    bool isNull() const { return t_ == NUL; }
    bool isStr() const { return t_ == STR; }
    bool isObj() const { return t_ == OBJ; }
    bool isArr() const { return t_ == ARR; }

    bool        asBool(bool def = false) const;
    double      asNum(double def = 0) const;
    int         asInt(int def = 0) const;
    std::string asStr(const std::string& def = "") const;

    const std::vector<Value>& items() const { return a_; }
    std::vector<Value>&       items() { return a_; }

    const std::map<std::string, Value>& fields() const { return o_; }
    std::map<std::string, Value>&       fields() { return o_; }

    const Value* find(const std::string& key) const;
    bool  has(const std::string& key) const;

    void  set(const std::string& key, const Value& v);
    void  set(const std::string& key, const std::string& v);
    void  set(const std::string& key, std::string&& v);
    void  set(const std::string& key, Value&& v);
    void  set(const std::string& key, int v);
    void  set(const std::string& key, bool v);
    void  push(const Value& v);

    // 直接构造一个 RAW 值（内部用，供 rawJson 包装预编码文本）。
    static Value makeRaw(std::string&& text);

    std::string dump() const;

private:
    Type        t_;
    bool        b_;
    double      n_;
    std::string s_;
    std::vector<Value>            a_;
    std::map<std::string, Value>  o_;

    void dumpTo(std::string& out) const;
};

// Wrap already-encoded JSON text so dump() emits it verbatim (no re-parse).
bool rawJson(std::string text, Value& out);

bool parse(const std::string& text, Value& out, std::string* err = nullptr);

std::string escape(const std::string& s);

}
