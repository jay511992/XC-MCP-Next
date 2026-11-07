#pragma once

#include "json.hpp"
#include <string>

// One identity per IDE process, not one identity per HTTP connection.
namespace instance {

std::string utf8(const wchar_t* text);
std::wstring wide(const std::string& text);
std::wstring environment(const wchar_t* name);
std::string currentProjectFile();
// X-XC-Project is a UTF-8, percent-encoded absolute Windows path.
bool matchesProject(const std::string& encoded, const std::string& actual);

class Identity {
public:
    explicit Identity(int actualPort);
    js::Value info() const;  // Project fields are read from the IDE on every call.
    void publish();
    void unpublish();       // Called under HTTP lifecycle lock, never by a destructor.

    int port;
    unsigned long pid;
    std::string id;
    std::string startedAt;
    std::string ideExe;
    std::string token;

private:
    std::wstring registryDir_;
    std::wstring recordPath_;
    std::wstring tokenPath_;
    bool published_ = false;
};

}
