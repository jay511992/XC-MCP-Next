#pragma once
#include "json.hpp"

// Every XCGUI/text operation is dispatched to the installing IDE UI thread.
// Workers only enumerate HWND metadata and wait for an owned, cancellable job.
namespace dialogs {
void install();   // Idempotent; call from plugin initialization on the UI thread.
void shutdown();  // Call on that thread before stopping/joining HTTP workers.
js::Value read(const js::Value& args);
}
