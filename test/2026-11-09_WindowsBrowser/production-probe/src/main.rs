//! The production module is compiled verbatim; no browser implementation is mocked.
#[path="../../../../desktop/src-tauri/src/browser/mod.rs"]
mod browser;
#[path="../../../../desktop/src-tauri/src/quick/capture/wgc.rs"]
mod wgc;
mod fixture;
mod ai_probe;
use serde_json::{json,Value};
use std::{path::PathBuf,sync::{Arc,Mutex,atomic::Ordering},time::{Duration,Instant}};
use tauri::{Manager,WebviewWindowBuilder,WebviewUrl};
fn main(){
    if std::env::args().any(|arg|arg=="--ai") {ai_probe::run();return;}
    let root=PathBuf::from(std::env::var_os("ASTRION_BROWSER_PRODUCTION_PROBE_ROOT").expect("isolated root required"));std::fs::create_dir_all(&root).unwrap();
    let write=std::env::args().any(|s|s=="--write");let phase=if write{"write"}else{"read"};
    let port=if write{0}else{let previous:Value=serde_json::from_slice(&std::fs::read(root.join("write.json")).unwrap()).unwrap();previous["origin"].as_str().unwrap().rsplit(':').next().unwrap().parse().unwrap()};
    let fixture=fixture::start(phase,port);let origin=fixture.origin.clone();
    let final_report=Arc::new(Mutex::new(json!({"origin":origin,"phase":phase,"root":root})));let output=root.join(format!("{phase}.json"));
    let python=std::env::var_os("ASTRION_BROWSER_PROBE_PYTHON").map(PathBuf::from).expect("explicit python");
    tauri::Builder::default().setup(move|app|{
        let view=WebviewWindowBuilder::new(app,"main",WebviewUrl::External(format!("{origin}/new").parse()?)).data_directory(root.join("main-webview2")).initialization_script(browser::BRIDGE_SCRIPT).title("Astrion production browser regression").inner_size(1000.,700.).build()?;
        let main=app.get_webview("main").unwrap();main.set_auto_resize(false)?;
        main.set_bounds(tauri::Rect{position:tauri::LogicalPosition::new(0.,46.).into(),size:tauri::LogicalSize::new(1000.,654.).into()})?;
        browser::initialize(app.handle(),origin.clone(),root.clone(),python,PathBuf::new()).map_err(std::io::Error::other)?;
        let parent=view.hwnd()?.0 as isize;let scale=view.scale_factor()?;let handle=app.handle().clone();let state=fixture.state.clone();let done=fixture.done.clone();let measure=fixture.measure.clone();let report=final_report.clone();
        std::thread::spawn(move||{
            let start=Instant::now();
            while !done.load(Ordering::Relaxed) && start.elapsed()<Duration::from_secs(85){
                if measure.swap(false,Ordering::Relaxed){
                    std::thread::sleep(Duration::from_millis(250));
                    match wgc::capture(wgc::Target::Window(parent)){
                        Ok(pixels)=>{
                            use windows::Win32::{Foundation::{HWND,POINT,RECT},Graphics::{Dwm::{DwmGetWindowAttribute,DWMWA_EXTENDED_FRAME_BOUNDS},Gdi::ClientToScreen}};
                            let mut frame=RECT::default();let mut client=POINT::default();unsafe{let _=DwmGetWindowAttribute(HWND(parent as _),DWMWA_EXTENDED_FRAME_BOUNDS,(&mut frame as *mut RECT).cast(),std::mem::size_of::<RECT>() as u32);let _=ClientToScreen(HWND(parent as _),&mut client);}
                            let sample=|x:f64,y:f64|{let x=(client.x-frame.left)as f64+x*scale;let y=(client.y-frame.top)as f64+y*scale;let offset=(y.round()as usize*pixels.width as usize+x.round()as usize)*4;pixels.bgra.get(offset..offset+4).unwrap_or(&[]).to_vec()};
                            let hole=sample(670.,196.);let outside=sample(800.,346.);
                            let green=pixels.bgra.chunks_exact(4).filter(|p|p[1]>150 && p[0]<150 && p[2]<90).count();let blue=pixels.bgra.chunks_exact(4).filter(|p|p[0]>180 && p[1]>80 && p[1]<170 && p[2]<110).count();report.lock().unwrap()["pixels"]=json!({"green":green,"blue":blue,"width":pixels.width,"height":pixels.height,"holeBGRA":hole,"outsideBGRA":outside,"holeShowsMain":hole.len()==4 && hole[1]>150 && hole[2]<90,"outsideShowsPage":outside.len()==4 && outside[0]>180 && outside[2]<110});let _=pixels.save_bmp(&root.join(format!("{phase}-regions.bmp")));
                        },
                        Err(error)=>{report.lock().unwrap()["captureError"]=json!(error);}
                    }
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            let mut report=report.lock().unwrap();report["suite"]=state.lock().unwrap()["result"].clone();report["events"]=state.lock().unwrap()["events"].clone();
            let _=std::fs::write(output,serde_json::to_vec_pretty(&*report).unwrap());drop(report);
            done.store(true,Ordering::Relaxed);let exit=handle.clone();let _=handle.run_on_main_thread(move||{browser::dispose();exit.exit(0);});
        });Ok(())
    }).run(tauri::generate_context!()).expect("production native probe");
}
