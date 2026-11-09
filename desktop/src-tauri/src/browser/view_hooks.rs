//! WebView2 callbacks only enqueue events on the STA thread.
use super::{manager::Browser, policy, *};
use windows::core::{Interface, HSTRING};

impl Browser {
    pub fn attach_hooks(
        &self,
        id: &str,
        wc: &ICoreWebView2,
        controller: &ICoreWebView2Controller,
    ) -> Result<(), String> {
        let generation = self
            .tabs
            .iter()
            .find(|tab| tab.id == id)
            .ok_or("unknown_browser_tab")?
            .generation;
        unsafe {
            let mut token = 0;
            let app = self.app.clone();
            let tab = id.to_owned();
            let local_target = self
                .tabs
                .iter()
                .find(|tab| tab.id == id)
                .ok_or("unknown_browser_tab")?
                .local_target
                .clone();
            native_error(wc.add_NavigationStarting(
                &NavigationStartingEventHandler::create(Box::new(move |sender, args| {
                    if let Some(args) = args {
                        let url = text(|out| args.Uri(out))?;
                        let current = sender
                            .and_then(|view| text(|out| view.Source(out)).ok())
                            .unwrap_or_default();
                        let mut redirected = windows::core::BOOL::default();
                        args.IsRedirected(&mut redirected)?;
                        let manual = local_target
                            .borrow_mut()
                            .take()
                            .is_some_and(|target| target == url);
                        // History can return only to previously visited documents, not arbitrary new file URLs.
                        let history = args
                            .cast::<ICoreWebView2NavigationStartingEventArgs3>()
                            .ok()
                            .is_some_and(|args| {
                                let mut kind = COREWEBVIEW2_NAVIGATION_KIND_NEW_DOCUMENT;
                                args.NavigationKind(&mut kind).is_ok()
                                    && kind == COREWEBVIEW2_NAVIGATION_KIND_BACK_OR_FORWARD
                            });
                        let local = policy::local_file(&url)
                            && !redirected.as_bool()
                            && (manual || history || policy::local_file(&current));
                        if !local && policy::navigation(&url, false).is_err() {
                            args.SetCancel(true)?;
                        } else {
                            enqueue_page(&app, &tab, generation, Event::Navigate(tab.clone(), url));
                        }
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_NavigationCompleted(
                &NavigationCompletedEventHandler::create(Box::new(move |_, args| {
                    if let Some(args) = args {
                        let mut success = windows::core::BOOL::default();
                        let mut status = COREWEBVIEW2_WEB_ERROR_STATUS_UNKNOWN;
                        args.IsSuccess(&mut success)?;
                        args.WebErrorStatus(&mut status)?;
                        enqueue_page(
                            &app,
                            &tab,
                            generation,
                            Event::Loaded(tab.clone(), success.as_bool(), status.0),
                        );
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_DocumentTitleChanged(
                &DocumentTitleChangedEventHandler::create(Box::new(move |_, _| {
                    enqueue_page(&app, &tab, generation, Event::Update(tab.clone()));
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_HistoryChanged(
                &HistoryChangedEventHandler::create(Box::new(move |_, _| {
                    enqueue_page(&app, &tab, generation, Event::Update(tab.clone()));
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_NewWindowRequested(
                &NewWindowRequestedEventHandler::create(Box::new(move |_, args| {
                    if let Some(args) = args {
                        let deferral = args.GetDeferral()?;
                        args.SetHandled(true)?;
                        enqueue_page(
                            &app,
                            &tab,
                            generation,
                            Event::Popup(tab.clone(), args.clone(), deferral),
                        );
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_WindowCloseRequested(
                &WindowCloseRequestedEventHandler::create(Box::new(move |_, _| {
                    enqueue_page(&app, &tab, generation, Event::Close(tab.clone()));
                    Ok(())
                })),
                &mut token,
            ))?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(wc.add_PermissionRequested(
                &PermissionRequestedEventHandler::create(Box::new(move |_, args| {
                    if let Some(args) = args {
                        enqueue_page(
                            &app,
                            &tab,
                            generation,
                            Event::Permission(tab.clone(), args.clone(), args.GetDeferral()?),
                        );
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
            if let Ok(view) = wc.cast::<ICoreWebView2_4>() {
                let app = self.app.clone();
                let tab = id.to_owned();
                native_error(view.add_DownloadStarting(
                    &DownloadStartingEventHandler::create(Box::new(move |_, args| {
                        if let Some(args) = args {
                            enqueue_page(
                                &app,
                                &tab,
                                generation,
                                Event::Download(tab.clone(), args.clone(), args.GetDeferral()?),
                            );
                        }
                        Ok(())
                    })),
                    &mut token,
                ))?;
            }
            // Install the adapter only after the document commits.
            if let Ok(view) = wc.cast::<ICoreWebView2_11>() {
                let app = self.app.clone();
                let tab = id.to_owned();
                native_error(view.add_ContextMenuRequested(
                    &ContextMenuRequestedEventHandler::create(Box::new(move |_, args| {
                        if let Some(args) = args {
                            enqueue_page(
                                &app,
                                &tab,
                                generation,
                                Event::Context(tab.clone(), args.clone(), args.GetDeferral()?),
                            );
                        }
                        Ok(())
                    })),
                    &mut token,
                ))?;
            }
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(controller.add_AcceleratorKeyPressed(
                &AcceleratorKeyPressedEventHandler::create(Box::new(move |_, args| {
                    use windows::Win32::UI::Input::KeyboardAndMouse::{GetKeyState, VK_CONTROL};
                    if let Some(args) = args {
                        let mut kind = COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN;
                        let mut key = 0;
                        args.KeyEventKind(&mut kind)?;
                        args.VirtualKey(&mut key)?;
                        if [
                            COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN,
                            COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN,
                        ]
                        .contains(&kind)
                            && GetKeyState(VK_CONTROL.0 as i32) < 0
                        {
                            let name = match key {
                                76 => "l",
                                70 => "f",
                                84 => "t",
                                87 => "w",
                                48 | 96 => "0",
                                187 | 107 => "+",
                                189 | 109 => "-",
                                _ => "",
                            };
                            if !name.is_empty() {
                                args.SetHandled(true)?;
                                enqueue_page(
                                    &app,
                                    &tab,
                                    generation,
                                    Event::Shortcut(tab.clone(), name.into()),
                                );
                            }
                        }
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
            let receiver = native_error(
                wc.GetDevToolsProtocolEventReceiver(&HSTRING::from("Runtime.bindingCalled")),
            )?;
            let app = self.app.clone();
            let tab = id.to_owned();
            native_error(receiver.add_DevToolsProtocolEventReceived(
                &DevToolsProtocolEventReceivedEventHandler::create(Box::new(move |_, args| {
                    if let Some(args) = args {
                        let raw = text(|out| args.ParameterObjectAsJson(out))?;
                        if raw.len() < 100000 {
                            if let Ok(value) = serde_json::from_str(&raw) {
                                enqueue_page(
                                    &app,
                                    &tab,
                                    generation,
                                    Event::Cdp(tab.clone(), "Runtime.bindingCalled".into(), value),
                                );
                            }
                        }
                    }
                    Ok(())
                })),
                &mut token,
            ))?;
        }
        Ok(())
    }
}
