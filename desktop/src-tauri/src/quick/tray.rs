use tauri::{AppHandle,Manager};
use tauri::menu::{Menu,MenuItem,PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;

pub fn create(app:&AppHandle)->Result<(),String>{
    let open=MenuItem::with_id(app,"quick-open","快捷对话",true,None::<&str>).map_err(|e|e.to_string())?;
    let main=MenuItem::with_id(app,"main-open","打开主窗口",true,None::<&str>).map_err(|e|e.to_string())?;
    let settings=MenuItem::with_id(app,"quick-settings","快捷对话设置",true,None::<&str>).map_err(|e|e.to_string())?;
    let separator=PredefinedMenuItem::separator(app).map_err(|e|e.to_string())?;
    let exit=MenuItem::with_id(app,"quick-exit","退出 Astrion",true,None::<&str>).map_err(|e|e.to_string())?;
    let menu=Menu::with_items(app,&[&open,&main,&settings,&separator,&exit]).map_err(|e|e.to_string())?;
    let mut builder=TrayIconBuilder::with_id("quick-tray").tooltip("Astrion").menu(&menu).on_menu_event(|app,event|{
        match event.id.as_ref(){
            "quick-open"=>{let _=super::show(app);},
            "main-open"|"quick-settings"=>{
                if let Some(window)=app.get_window("main"){let _=window.show();let _=window.unminimize();let _=window.set_focus();}
                if event.id.as_ref()=="quick-settings"{if let Some(webview)=app.get_webview("main"){let _=webview.eval("location.href='/settings/quick-chat'");}}
            },
            "quick-exit"=>{super::stop(app);app.exit(0);},_=>{}
        }
    });
    if let Some(icon)=app.default_window_icon(){builder=builder.icon(icon.clone());}
    builder.build(app).map_err(|e|e.to_string())?;Ok(())
}
