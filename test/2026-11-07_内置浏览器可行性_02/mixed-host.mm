#import <Cocoa/Cocoa.h>
#include <fstream>
#include <sstream>
#include <cstdio>
#include <csignal>
#include <execinfo.h>
#include <unistd.h>
#include "lab-cef.h"
#include "include/wrapper/cef_library_loader.h"
#include "include/capi/cef_life_span_handler_capi.h"
#include "include/capi/cef_load_handler_capi.h"

@interface LabApplication : NSApplication<CefAppProtocol> { BOOL handling_; }
@end
@implementation LabApplication
- (BOOL)isHandlingSendEvent { return handling_; }
- (void)setHandlingSendEvent:(BOOL)value { handling_ = value; }
- (void)sendEvent:(NSEvent*)event { CefScopedSendingEvent guard; [super sendEvent:event]; }
@end

static cef_window_t* window_;
static cef_browser_view_t *left_, *right_;
static cef_panel_t* right_panel_;
static cef_view_t* toolbar_;
static cef_request_context_t *left_context_, *right_context_;
static std::string output_, origin_, sequence_;
static bool closing_ = false;
static int contexts_ready_ = 0;
static void create_window();
static bool navigation_sent_ = false;
static NSString* frame_url(cef_browser_t* browser) {
  if (!browser) return @"";
  auto* frame=browser->get_main_frame(browser);
  auto raw=frame ? frame->get_url(frame) : nullptr;
  NSString* value=raw ? [[NSString alloc] initWithCharacters:reinterpret_cast<const unichar*>(raw->str) length:raw->length] : @"";
  if (raw) cef_string_userfree_utf16_free(raw);
  release(frame); return value;
}
static void navigate(cef_browser_view_t* browser_view, const char* route) {
  auto* browser=browser_view->get_browser(browser_view);
  if (!browser) return;
  auto* frame=browser->get_main_frame(browser);
  if (frame) { cef_string_t url{}; text(&url,origin_+route); frame->load_url(frame,&url); cef_string_utf16_clear(&url); }
  release(frame); release(browser);
}

static NSDictionary* rect(cef_rect_t r) { return @{ @"x":@(r.x), @"y":@(r.y), @"width":@(r.width), @"height":@(r.height) }; }
static NSDictionary* bounds(cef_view_t* v) {
  if (!v) return @{};
  auto* parent = v->get_parent_view(v);
  auto* owner = v->get_window(v);
  NSDictionary* result = @{ @"relative":rect(v->get_bounds(v)), @"screen":rect(v->get_bounds_in_screen(v)),
    @"visible":@(v->is_visible(v)), @"parentId":@(parent ? parent->get_id(parent) : -1),
    @"sameWindow":@(owner && view(owner)->is_same(view(owner), pass(view(window_)))) };
  release(parent); release(owner);
  return result;
}
static void snapshot() {
  if (!window_ || closing_) return;
  auto* browser_left = left_->get_browser(left_);
  auto* browser_right = right_->get_browser(right_);
  auto raw_tree=view(window_)->to_string(view(window_), 1);
  NSString* tree=raw_tree ? [[NSString alloc] initWithCharacters:reinterpret_cast<const unichar*>(raw_tree->str) length:raw_tree->length] : @"";
  if (raw_tree) cef_string_userfree_utf16_free(raw_tree);
  NSDictionary* state = @{ @"viewTree":tree, @"sequence":[NSString stringWithUTF8String:sequence_.c_str()],
    @"windowStyle":@(window_->get_runtime_style(window_)), @"leftStyle":@(left_->get_runtime_style(left_)),
    @"rightStyle":@(right_->get_runtime_style(right_)), @"window":bounds(view(window_)),
    @"left":bounds(view(left_)), @"right":bounds(view(right_)), @"toolbar":bounds(toolbar_),
    @"rightPanel":bounds(getenv("BROWSER_LAB_DIRECT_CHROME") ? view(right_) : view(right_panel_)), @"fullscreen":@(window_->is_fullscreen(window_)),
    @"directChromeChild":@(getenv("BROWSER_LAB_DIRECT_CHROME") != nullptr),
    @"leftBrowserId":@(browser_left ? browser_left->get_identifier(browser_left) : -1),
    @"rightBrowserId":@(browser_right ? browser_right->get_identifier(browser_right) : -1),
    @"leftUrl":frame_url(browser_left), @"rightUrl":frame_url(browser_right) };
  NSData* data = [NSJSONSerialization dataWithJSONObject:state options:NSJSONWritingPrettyPrinted error:nil];
  [data writeToFile:[NSString stringWithUTF8String:(output_+"/state.json").c_str()] atomically:YES];
  release(browser_left); release(browser_right);
}
static void poll();
static void schedule() {
  auto* task = callback<cef_task_t>();
  task->execute = [](cef_task_t*) { poll(); };
  cef_post_delayed_task(TID_UI, pass(task), 150);
  release(task);
}
static void poll() {
  if (closing_ || !window_) return;
  if (!navigation_sent_ && getenv("BROWSER_LAB_EXPLICIT_NAV")) {
    navigation_sent_=true; navigate(left_,"/trusted"); navigate(right_,"/fixture");
  }
  std::ifstream input(output_+"/command.txt");
  std::string seq, action; input >> seq >> action;
  if (!seq.empty() && seq != sequence_) {
    sequence_ = seq;
    if (action == "resize") { int w, h; input >> w >> h; cef_size_t size{w,h}; window_->center_window(window_, &size); }
    else if (action == "hide-right") { auto* target=getenv("BROWSER_LAB_DIRECT_CHROME") ? view(right_) : view(right_panel_); target->set_visible(target,0); }
    else if (action == "show-right") { auto* target=getenv("BROWSER_LAB_DIRECT_CHROME") ? view(right_) : view(right_panel_); target->set_visible(target,1); }
    else if (action == "fullscreen") { int enabled; input >> enabled; window_->set_fullscreen(window_, enabled); }
    else if (action == "focus-left") view(left_)->request_focus(view(left_));
    else if (action == "focus-right") view(right_)->request_focus(view(right_));
    else if (action == "close") { window_->close(window_); return; }
    window_->base.layout(&window_->base);
  }
  snapshot(); schedule();
}
static cef_browser_view_delegate_t* browser_delegate(bool chrome) {
  auto* delegate = callback<cef_browser_view_delegate_t>();
  delegate->get_browser_runtime_style = chrome
    ? +[](cef_browser_view_delegate_t*) { return CEF_RUNTIME_STYLE_CHROME; }
    : +[](cef_browser_view_delegate_t*) { return CEF_RUNTIME_STYLE_ALLOY; };
  delegate->get_chrome_toolbar_type = +[](cef_browser_view_delegate_t*, cef_browser_view_t*) { return CEF_CTT_NORMAL; };
  delegate->base.get_preferred_size = chrome
    ? +[](cef_view_delegate_t*, cef_view_t*) { return cef_size_t{800,700}; }
    : +[](cef_view_delegate_t*, cef_view_t*) { return cef_size_t{400,700}; };
  return delegate;
}
static cef_request_context_t* context(const char* name) {
  cef_request_context_settings_t settings{}; settings.size=sizeof(settings);
  text(&settings.cache_path, output_+"/profile/"+name);
  auto* handler=callback<cef_request_context_handler_t>();
  handler->on_request_context_initialized=[](cef_request_context_handler_t*, cef_request_context_t*) {
    fprintf(stderr, "[mixed-lab] request-context-ready %d\n", contexts_ready_+1); fflush(stderr);
    if (++contexts_ready_ == 2) {
      auto* task=callback<cef_task_t>();
      task->execute=[](cef_task_t*) { create_window(); };
      cef_post_delayed_task(TID_UI, pass(task), 1); release(task);
    }
  };
  auto* result = cef_request_context_create_context(&settings, pass(handler));
  release(handler);
  cef_string_utf16_clear(&settings.cache_path);
  return result;
}
static void on_window_created(cef_window_delegate_t*, cef_window_t* window) {
  signal(SIGSEGV, [](int signal_number) { void* frames[64]; int count=backtrace(frames,64); backtrace_symbols_fd(frames,count,STDERR_FILENO); _exit(128+signal_number); });
  fprintf(stderr, "[mixed-lab] window-created\n"); fflush(stderr);
  window_ = pass(window);
  view(window)->set_id(view(window), 1);
  auto* horizontal = box_layout(&window->base, true);
  cef_browser_settings_t settings{}; settings.size=sizeof(settings);
  cef_string_t url{};
  auto* client = callback<cef_client_t>();
  client->get_life_span_handler=[](cef_client_t*) {
    auto* handler=callback<cef_life_span_handler_t>();
    handler->on_after_created=[](cef_life_span_handler_t*, cef_browser_t* browser) {
      fprintf(stderr,"[mixed-lab] browser-after-created id=%d\n",browser->get_identifier(browser)); fflush(stderr);
    };
    return handler;
  };
  client->get_load_handler=[](cef_client_t*) {
    auto* handler=callback<cef_load_handler_t>();
    handler->on_loading_state_change=[](cef_load_handler_t*, cef_browser_t* browser,int loading,int,int) {
      fprintf(stderr,"[mixed-lab] browser-loading id=%d loading=%d url=%s\n",browser->get_identifier(browser),loading,[frame_url(browser) UTF8String]); fflush(stderr);
    };
    handler->on_load_error=[](cef_load_handler_t*,cef_browser_t* browser,cef_frame_t*,cef_errorcode_t code,const cef_string_t*,const cef_string_t*) {
      fprintf(stderr,"[mixed-lab] browser-load-error id=%d code=%d\n",browser->get_identifier(browser),code); fflush(stderr);
    };
    return handler;
  };
  auto* ld = browser_delegate(false); auto* rd = browser_delegate(true);
  fprintf(stderr, "[mixed-lab] contexts-already-initialized\n"); fflush(stderr);
  text(&url, origin_+"/trusted"); left_=cef_browser_view_create(pass(client), &url, &settings, nullptr, pass(left_context_), pass(ld)); cef_string_utf16_clear(&url);
  fprintf(stderr, "[mixed-lab] left-created\n"); fflush(stderr);
  text(&url, origin_+"/fixture"); right_=cef_browser_view_create(pass(client), &url, &settings, nullptr, pass(right_context_), pass(rd)); cef_string_utf16_clear(&url);
  fprintf(stderr, "[mixed-lab] right-created left=%p right=%p contexts=%p,%p client=%p delegates=%p,%p\n", left_, right_, left_context_, right_context_, client, ld, rd); fflush(stderr);
  if (!left_ || !right_) { fprintf(stderr, "[mixed-lab] BrowserView creation returned null\n"); fflush(stderr); _exit(7); }
  release(client); release(ld); release(rd);
  fprintf(stderr, "[mixed-lab] callbacks-released leftSize=%zu rightSize=%zu\n", reinterpret_cast<cef_base_ref_counted_t*>(left_)->size, reinterpret_cast<cef_base_ref_counted_t*>(right_)->size); fflush(stderr);
  view(left_)->set_id(view(left_), 10); view(right_)->set_id(view(right_), 20);
  right_panel_=cef_panel_create(nullptr); view(right_panel_)->set_id(view(right_panel_), 30);
  auto* vertical = box_layout(right_panel_, false);
  // Compare a direct Chrome child with the extra-panel nesting variant.
  if (getenv("BROWSER_LAB_DIRECT_CHROME")) {
    window->base.add_child_view(&window->base, pass(view(right_)));
  } else {
    window->base.add_child_view(&window->base, pass(view(right_panel_)));
    right_panel_->add_child_view(right_panel_, pass(view(right_)));
  }
  fprintf(stderr, "[mixed-lab] right-added\n"); fflush(stderr);
  window->base.add_child_view_at(&window->base, pass(view(left_)), 0);
  fprintf(stderr, "[mixed-lab] left-added\n"); fflush(stderr);
  toolbar_=right_->get_chrome_toolbar(right_);
  if (toolbar_) {
    // Match official cefclient ViewsWindow::AddControls: attach the toolbar
    // to the host layout after the BrowserView has joined the Window.
    toolbar_->set_id(toolbar_, 40);
    if (!getenv("BROWSER_LAB_DIRECT_CHROME")) {
      right_panel_->add_child_view_at(right_panel_,pass(toolbar_),0);
      toolbar_->set_visible(toolbar_,1);
    }
    fprintf(stderr, "[mixed-lab] native-toolbar-available\n"); fflush(stderr);
  }
  if (!getenv("BROWSER_LAB_DIRECT_CHROME")) vertical->set_flex_for_view(vertical, pass(view(right_)), 1);
  horizontal->set_flex_for_view(horizontal, pass(view(left_)), 1);
  horizontal->set_flex_for_view(horizontal, pass(getenv("BROWSER_LAB_DIRECT_CHROME") ? view(right_) : view(right_panel_)), 2);
  release(vertical); release(horizontal);
  cef_string_t title{}; text(&title, "Astrion CEF mixed Views feasibility lab"); window->set_title(window, &title); cef_string_utf16_clear(&title);
  cef_size_t size{1200,760}; window->center_window(window, &size);
  window->show(window); window->base.layout(&window->base);
  schedule();
}
static int can_close(cef_window_delegate_t*, cef_window_t*) {
  bool ready=true;
  for (auto* bv : {left_, right_}) {
    if (!bv) continue;
    auto* browser=bv->get_browser(bv);
    if (browser) { auto* host=browser->get_host(browser); if (!host->try_close_browser(host)) ready=false; release(host); release(browser); }
  }
  return ready;
}
static void destroyed(cef_window_delegate_t*, cef_window_t*) {
  closing_=true;
  release(toolbar_); release(left_); release(right_); release(right_panel_); release(left_context_); release(right_context_); release(window_);
  cef_quit_message_loop();
}
static void create_window() {
  auto* delegate=callback<cef_window_delegate_t>();
  delegate->get_window_runtime_style=[](cef_window_delegate_t*) { return CEF_RUNTIME_STYLE_CHROME; };
  delegate->on_window_created=on_window_created;
  delegate->can_close=can_close; delegate->on_window_destroyed=destroyed;
  auto* created=cef_window_create_top_level(pass(delegate));
  release(created); release(delegate);
}
static cef_browser_process_handler_t* process_handler(cef_app_t*) {
  auto* handler=callback<cef_browser_process_handler_t>();
  handler->on_context_initialized=[](cef_browser_process_handler_t*) {
    fprintf(stderr, "[mixed-lab] context-initialized\n"); fflush(stderr);
    if (getenv("BROWSER_LAB_GLOBAL_CONTEXT")) {
      left_context_=cef_request_context_get_global_context();
      right_context_=cef_request_context_get_global_context();
      auto* task=callback<cef_task_t>(); task->execute=[](cef_task_t*) { create_window(); };
      cef_post_delayed_task(TID_UI,pass(task),1); release(task);
    } else {
      left_context_=context("trusted"); right_context_=context("browser");
    }
  };
  return handler;
}
int main(int argc, char* argv[]) {
  @autoreleasepool {
    output_=getenv("BROWSER_LAB_OUTPUT"); origin_=getenv("BROWSER_LAB_ORIGIN");
    void* library=cef_scoped_library_loader_create(0);
    if (!library) return 3;
    [LabApplication sharedApplication]; [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    cef_api_hash(CEF_API_VERSION, 0);
    cef_main_args_t args{argc,argv};
    auto* app=callback<cef_app_t>(); app->get_browser_process_handler=process_handler;
    // The official separate Helper handles CefExecuteProcess.
    cef_settings_t settings{}; settings.size=sizeof(settings);
    text(&settings.root_cache_path, output_+"/profile");
    text(&settings.framework_dir_path, getenv("BROWSER_LAB_FRAMEWORK"));
    text(&settings.browser_subprocess_path, getenv("BROWSER_LAB_HELPER"));
    text(&settings.log_file, output_+"/cef.log");
    settings.remote_debugging_port=std::stoi(getenv("BROWSER_LAB_CDP_PORT"));
    int ok=cef_initialize(&args, &settings, pass(app), nullptr);
    fprintf(stderr, "[mixed-lab] initialize-return %d\n", ok); fflush(stderr);
    cef_string_utf16_clear(&settings.root_cache_path); cef_string_utf16_clear(&settings.framework_dir_path);
    cef_string_utf16_clear(&settings.browser_subprocess_path); cef_string_utf16_clear(&settings.log_file);
    if (!ok) { release(app); cef_scoped_library_loader_free(library); return 2; }
    if (getenv("BROWSER_LAB_FINISH_LAUNCH")) [NSApp finishLaunching];
    cef_run_message_loop(); cef_shutdown(); release(app);
    cef_scoped_library_loader_free(library);
  }
  return 0;
}
