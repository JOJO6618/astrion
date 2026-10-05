use serde::Serialize;
use windows::core::BOOL;
use windows::Win32::Foundation::{HWND,LPARAM,RECT};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute,DWMWA_CLOAKED,DWMWA_EXTENDED_FRAME_BOUNDS};
use windows::Win32::UI::WindowsAndMessaging::*;

#[derive(Clone,Serialize)]
pub struct WindowInfo {pub id:String,pub pid:u32,pub app:String,pub candidate:bool,pub x:i32,pub y:i32,pub width:i32,pub height:i32}
struct Collection {excluded:Vec<isize>,windows:Vec<WindowInfo>}
unsafe extern "system" fn collect(hwnd:HWND,param:LPARAM)->BOOL {
    let collection=&mut *(param.0 as *mut Collection);
    if collection.excluded.contains(&(hwnd.0 as isize)) || !IsWindowVisible(hwnd).as_bool() || IsIconic(hwnd).as_bool() {return BOOL(1);}
    let mut cloaked=0u32;
    let _=DwmGetWindowAttribute(hwnd,DWMWA_CLOAKED,&mut cloaked as *mut _ as _,4);
    if cloaked!=0 {return BOOL(1);}
    let mut rect=RECT::default();
    if DwmGetWindowAttribute(hwnd,DWMWA_EXTENDED_FRAME_BOUNDS,&mut rect as *mut _ as _,std::mem::size_of::<RECT>() as u32).is_err() {
        if GetWindowRect(hwnd,&mut rect).is_err(){return BOOL(1);}
    }
    if rect.right<=rect.left || rect.bottom<=rect.top {return BOOL(1);}
    let mut title=[0u16;512];let length=GetWindowTextW(hwnd,&mut title);
    let title=String::from_utf16_lossy(&title[..length.max(0) as usize]);
    let mut class=[0u16;128];let length=GetClassNameW(hwnd,&mut class);
    let class=String::from_utf16_lossy(&class[..length.max(0) as usize]);
    if ["Progman","WorkerW"].contains(&class.as_str()){return BOOL(1);}
    let mut pid=0;GetWindowThreadProcessId(hwnd,Some(&mut pid));
    let candidate=!title.is_empty() && !["Shell_TrayWnd","Shell_SecondaryTrayWnd"].contains(&class.as_str())
        && rect.right-rect.left>=140 && rect.bottom-rect.top>=80;
    collection.windows.push(WindowInfo{id:format!("{}:{}",hwnd.0 as isize,pid),pid,app:title,candidate,
        x:rect.left,y:rect.top,width:rect.right-rect.left,height:rect.bottom-rect.top});
    BOOL(1)
}
pub fn list(excluded:Vec<isize>)->Result<Vec<WindowInfo>,String> {
    let mut collection=Collection{excluded,windows:Vec::new()};
    unsafe{EnumWindows(Some(collect),LPARAM(&mut collection as *mut _ as isize))}.map_err(|e|e.to_string())?;
    Ok(collection.windows)
}
pub fn validate(id:&str)->Result<isize,String> {
    let (handle,pid)=id.split_once(':').ok_or("Invalid window id")?;
    let handle:isize=handle.parse().map_err(|_|"Invalid handle")?;
    let expected:u32=pid.parse().map_err(|_|"Invalid process id")?;
    let hwnd=HWND(handle as _);let mut pid=0;
    unsafe{GetWindowThreadProcessId(hwnd,Some(&mut pid));}
    if handle==0 || pid!=expected || !unsafe{IsWindow(Some(hwnd))}.as_bool() || !unsafe{IsWindowVisible(hwnd)}.as_bool(){return Err("Selected window is no longer available".into());}
    Ok(handle)
}
