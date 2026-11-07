#pragma once

#include <string>
#include "json.hpp"

namespace tools {

js::Value list();
// tools/list 的预编码快照：只构建一次，后续请求零解析零序列化。
js::Value listCached();
std::string call(const std::string& name, const js::Value& args, bool& isError);

}
