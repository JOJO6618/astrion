//! Modifier double-tap recognition. The hook never consumes or records keystrokes.
use std::collections::HashSet;
use std::sync::{Arc, OnceLock, atomic::{AtomicBool, AtomicIsize, AtomicU64, Ordering}};
use std::time::{Duration, Instant};
use windows::Win32::Foundation::{HINSTANCE, LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::*;
use windows::Win32::UI::Input::KeyboardAndMouse::*;

static ALT_RELEASE_UNTIL: AtomicU64 = AtomicU64::new(0);
static ALT_RELEASE_WINDOW: AtomicIsize = AtomicIsize::new(0);
static INPUT_CLOCK: OnceLock<Instant> = OnceLock::new();
fn input_time_ms() -> u64 { INPUT_CLOCK.get_or_init(Instant::now).elapsed().as_millis() as u64 + 1 }
pub(super) fn take_bare_alt_menu(hwnd:isize) -> bool {
    let until=ALT_RELEASE_UNTIL.load(Ordering::Acquire);
    until!=0 && input_time_ms()<=until && ALT_RELEASE_WINDOW.load(Ordering::Relaxed)==hwnd
        && ALT_RELEASE_UNTIL.compare_exchange(until,0,Ordering::AcqRel,Ordering::Relaxed).is_ok()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Modifier { LeftControl, RightControl, Shift, Alt }
impl Modifier {
    pub fn parse(value:&str) -> Result<Self,String> {
        match value {
            "left-control" => Ok(Self::LeftControl), "right-control" => Ok(Self::RightControl),
            "shift" => Ok(Self::Shift), "alt" => Ok(Self::Alt), _=>Err("Unsupported modifier".into()),
        }
    }
    fn matches(self,key:u32)->bool {
        match self {
            Self::LeftControl => key==VK_LCONTROL.0 as u32,
            Self::RightControl => key==VK_RCONTROL.0 as u32,
            Self::Shift => key==VK_LSHIFT.0 as u32 || key==VK_RSHIFT.0 as u32,
            Self::Alt => key==VK_LMENU.0 as u32 || key==VK_RMENU.0 as u32,
        }
    }
}

pub struct DoubleTap {
    modifier:Modifier,
    keys:HashSet<u32>,
    clean:bool,
    pressed:bool,
    last:Option<Duration>,
}
impl DoubleTap {
    pub fn new(modifier:Modifier)->Self { Self{modifier,keys:HashSet::new(),clean:false,pressed:false,last:None} }
    pub fn cancel(&mut self) { self.clean=false; self.last=None; }
    pub fn key(&mut self,key:u32,down:bool,now:Duration)->bool {
        if down && !self.keys.insert(key) { return false; }
        if !down { self.keys.remove(&key); }
        if !self.modifier.matches(key) {
            self.cancel();
            return false;
        }
        if down {
            if self.pressed { self.cancel(); return false; }
            self.pressed=true;
            self.clean=self.keys.len()==1;
            if !self.clean { self.last=None; }
            return false;
        }
        if !self.pressed { return false; }
        self.pressed=false;
        if !self.clean || !self.keys.is_empty() { self.cancel(); return false; }
        self.clean=false;
        if self.last.is_some_and(|last| now.saturating_sub(last)<Duration::from_millis(400)) {
            self.last=None; true
        } else { self.last=Some(now); false }
    }
}

pub struct Listener { running:Arc<AtomicBool>, thread_id:u32 }
impl Drop for Listener {
    fn drop(&mut self) {
        self.running.store(false,Ordering::Release);
        unsafe { let _=PostThreadMessageW(self.thread_id,WM_QUIT,WPARAM(0),LPARAM(0)); }
    }
}

pub enum KeyEvent { Toggle, Escape }

struct HookState {
    recognizer:DoubleTap,
    start:Instant,
    notify:std::sync::mpsc::Sender<KeyEvent>,
}
thread_local! { static HOOK:std::cell::RefCell<Option<HookState>>=const {std::cell::RefCell::new(None)}; }
unsafe extern "system" fn keyboard_hook(code:i32,w:WPARAM,l:LPARAM)->LRESULT {
    if code>=0 {
        let info=&*(l.0 as *const KBDLLHOOKSTRUCT);
        if !info.flags.contains(LLKHF_INJECTED) {
            let down=w.0 as u32==WM_KEYDOWN || w.0 as u32==WM_SYSKEYDOWN;
            let up=w.0 as u32==WM_KEYUP || w.0 as u32==WM_SYSKEYUP;
            if down || up {
                HOOK.with(|slot| {
                    if let Some(state)=slot.borrow_mut().as_mut() {
                        let recognizer=&state.recognizer;
                        let bare_alt=up && recognizer.modifier==Modifier::Alt
                            && recognizer.modifier.matches(info.vkCode) && recognizer.pressed
                            && recognizer.clean && recognizer.keys.len()==1;
                        ALT_RELEASE_UNTIL.store(0,Ordering::Release);
                        if bare_alt {
                            ALT_RELEASE_WINDOW.store(GetForegroundWindow().0 as isize,Ordering::Relaxed);
                            ALT_RELEASE_UNTIL.store(input_time_ms()+150,Ordering::Release);
                        }
                        if down && info.vkCode == 0x1b { let _=state.notify.send(KeyEvent::Escape); }
                        if state.recognizer.key(info.vkCode,down,state.start.elapsed()) { let _=state.notify.send(KeyEvent::Toggle); }
                    }
                });
            }
        }
    }
    CallNextHookEx(None,code,w,l)
}
unsafe extern "system" fn mouse_hook(code:i32,w:WPARAM,l:LPARAM)->LRESULT {
    if code>=0 && [WM_LBUTTONDOWN,WM_RBUTTONDOWN,WM_MBUTTONDOWN].contains(&(w.0 as u32)) {
        ALT_RELEASE_UNTIL.store(0,Ordering::Release);
        HOOK.with(|slot| { if let Some(state)=slot.borrow_mut().as_mut() { state.recognizer.cancel(); } });
    }
    CallNextHookEx(None,code,w,l)
}

pub fn install(modifier:Modifier, notify:std::sync::mpsc::Sender<KeyEvent>)->Result<Listener,String> {
    let (ready_tx,ready_rx)=std::sync::mpsc::channel();
    let running=Arc::new(AtomicBool::new(true));
    let active=running.clone();
    std::thread::spawn(move || unsafe {
        let mut message=MSG::default();
        let _=PeekMessageW(&mut message,None,0,0,PM_NOREMOVE);
        HOOK.with(|slot| *slot.borrow_mut()=Some(HookState{recognizer:DoubleTap::new(modifier),start:Instant::now(),notify}));
        let key=match SetWindowsHookExW(WH_KEYBOARD_LL,Some(keyboard_hook),Some(HINSTANCE::default()),0) {
            Ok(hook)=>hook,Err(error)=>{let _=ready_tx.send(Err(error.to_string()));return;}
        };
        let mouse=match SetWindowsHookExW(WH_MOUSE_LL,Some(mouse_hook),Some(HINSTANCE::default()),0) {
            Ok(hook)=>hook,Err(error)=>{let _=UnhookWindowsHookEx(key);let _=ready_tx.send(Err(error.to_string()));return;}
        };
        let thread_id=windows::Win32::System::Threading::GetCurrentThreadId();
        let _=ready_tx.send(Ok(thread_id));
        while active.load(Ordering::Acquire) && GetMessageW(&mut message,None,0,0).0>0 {
            let _=TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        let _=UnhookWindowsHookEx(key); let _=UnhookWindowsHookEx(mouse);
        HOOK.with(|slot| *slot.borrow_mut()=None);
    });
    let thread_id=ready_rx.recv_timeout(Duration::from_secs(3)).map_err(|e|e.to_string())??;
    Ok(Listener{running,thread_id})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn tap(state:&mut DoubleTap,key:u32,at:u64)->bool {
        assert!(!state.key(key,true,Duration::from_millis(at)));
        state.key(key,false,Duration::from_millis(at+50))
    }
    #[test] fn clean_double_tap() {
        let mut s=DoubleTap::new(Modifier::LeftControl);
        assert!(!tap(&mut s,0xa2,0));assert!(tap(&mut s,0xa2,200));assert!(!tap(&mut s,0xa2,300));
    }
    #[test] fn right_control_is_independent() {
        let mut s=DoubleTap::new(Modifier::LeftControl);
        assert!(!tap(&mut s,0xa3,0));assert!(!tap(&mut s,0xa3,100));
        assert!(!tap(&mut s,0xa2,200));assert!(tap(&mut s,0xa2,300));
    }
    #[test] fn chords_clicks_and_timeout_cancel() {
        let mut s=DoubleTap::new(Modifier::LeftControl);
        assert!(!tap(&mut s,0xa2,0)); s.key(0x43,true,Duration::from_millis(100));s.key(0x43,false,Duration::from_millis(110));
        assert!(!tap(&mut s,0xa2,200)); s.cancel();assert!(!tap(&mut s,0xa2,300));assert!(!tap(&mut s,0xa2,900));
    }
    #[test] fn repeat_is_not_a_tap() {
        let mut s=DoubleTap::new(Modifier::Shift);
        s.key(0xa0,true,Duration::ZERO);
        assert!(!s.key(0xa0,true,Duration::from_millis(20)));
        assert!(!s.key(0xa0,false,Duration::from_millis(50)));
        assert!(tap(&mut s,0xa0,150));
    }
}
