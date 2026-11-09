use super::{manager::Browser, policy, *};
use serde_json::{json, Value};
use windows::core::HSTRING;
impl Browser {
    pub fn page_dialog_snapshot(&self) -> Value {
        let tab = self.active_id();
        let Some((args, _)) = self.ai.dialogs.get(&tab) else {
            return Value::Null;
        };
        unsafe {
            let mut kind = COREWEBVIEW2_SCRIPT_DIALOG_KIND_ALERT;
            let _ = args.Kind(&mut kind);
            json!({"id":self.ai.dialog_ids.get(&tab),"tabId":tab,"kind":match kind {COREWEBVIEW2_SCRIPT_DIALOG_KIND_CONFIRM=>"confirm",COREWEBVIEW2_SCRIPT_DIALOG_KIND_PROMPT=>"prompt",COREWEBVIEW2_SCRIPT_DIALOG_KIND_BEFOREUNLOAD=>"beforeunload",_=>"alert"},"message":text(|out|args.Message(out)).unwrap_or_default().chars().take(4000).collect::<String>(),"defaultPrompt":text(|out|args.DefaultText(out)).unwrap_or_default().chars().take(20000).collect::<String>()})
        }
    }
    pub fn resolve_page_dialog(
        &mut self,
        tab: &str,
        generation: Option<&str>,
        accept: bool,
        prompt: Option<&str>,
    ) -> Result<(), String> {
        if let Some(generation) = generation {
            if self.ai.dialog_ids.get(tab).map(String::as_str) != Some(generation) {
                return Err("stale_browser_dialog".into());
            }
        }
        let (args, deferral) = self.ai.dialogs.remove(tab).ok_or("no_browser_dialog")?;
        self.ai.dialog_ids.remove(tab);
        let result = unsafe {
            (|| -> windows::core::Result<()> {
                if accept {
                    if let Some(prompt) = prompt {
                        args.SetResultText(&HSTRING::from(prompt))?;
                    }
                    args.Accept()?;
                }
                Ok(())
            })()
        };
        unsafe {
            let _ = deferral.Complete();
        }
        self.ai_event(tab, "Page.javascriptDialogClosed", json!({}));
        self.publish();
        native_error(result)
    }
    pub fn reject_page_dialog(&mut self, tab: &str) {
        self.ai.dialog_ids.remove(tab);
        if let Some((_, deferral)) = self.ai.dialogs.remove(tab) {
            unsafe {
                let _ = deferral.Complete();
            }
        }
        self.ai_event(tab, "Page.javascriptDialogClosed", json!({}));
    }
    pub fn new_dialog_id(&mut self, tab: &str) {
        self.ai.dialog_ids.insert(tab.into(), policy::id());
    }
}
