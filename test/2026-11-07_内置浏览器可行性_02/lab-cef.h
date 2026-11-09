#pragma once
#include <atomic>
#include <cstring>
#include <string>
#include "include/capi/cef_app_capi.h"
#include "include/capi/cef_browser_process_handler_capi.h"
#include "include/capi/cef_client_capi.h"
#include "include/capi/cef_frame_capi.h"
#include "include/capi/cef_request_context_capi.h"
#include "include/capi/cef_request_context_handler_capi.h"
#include "include/capi/cef_task_capi.h"
#include "include/capi/views/cef_browser_view_capi.h"
#include "include/capi/views/cef_browser_view_delegate_capi.h"
#include "include/capi/views/cef_box_layout_capi.h"
#include "include/capi/views/cef_panel_capi.h"
#include "include/capi/views/cef_window_capi.h"
#include "include/capi/views/cef_window_delegate_capi.h"
#include "include/cef_api_hash.h"
#include "include/cef_application_mac.h"

template<class T> struct CallbackBox { T api{}; std::atomic<int> refs{1}; };
template<class T> T* callback() {
  auto* box = new CallbackBox<T>();
  auto* base = reinterpret_cast<cef_base_ref_counted_t*>(&box->api);
  base->size = sizeof(T);
  base->add_ref = [](cef_base_ref_counted_t* self) { ++reinterpret_cast<CallbackBox<T>*>(self)->refs; };
  base->release = [](cef_base_ref_counted_t* self) -> int {
    auto* box = reinterpret_cast<CallbackBox<T>*>(self);
    if (--box->refs == 0) { delete box; return 1; }
    return 0;
  };
  base->has_one_ref = [](cef_base_ref_counted_t* self) -> int { return reinterpret_cast<CallbackBox<T>*>(self)->refs == 1; };
  base->has_at_least_one_ref = [](cef_base_ref_counted_t* self) -> int { return reinterpret_cast<CallbackBox<T>*>(self)->refs > 0; };
  return &box->api;
}
template<class T> void release(T*& object) {
  if (object) { auto* base = reinterpret_cast<cef_base_ref_counted_t*>(object); base->release(base); object = nullptr; }
}
template<class T> cef_view_t* view(T* object) { return reinterpret_cast<cef_view_t*>(object); }
template<class T> T* pass(T* object) { if (object) { auto* base=reinterpret_cast<cef_base_ref_counted_t*>(object); base->add_ref(base); } return object; }
inline void text(cef_string_t* destination, const std::string& value) { cef_string_utf8_to_utf16(value.c_str(), value.size(), destination); }
inline cef_box_layout_t* box_layout(cef_panel_t* panel, bool horizontal) {
  cef_box_layout_settings_t settings{};
  settings.size = sizeof(settings);
  settings.horizontal = horizontal;
  settings.cross_axis_alignment = CEF_AXIS_ALIGNMENT_STRETCH;
  return panel->set_to_box_layout(panel, &settings);
}
