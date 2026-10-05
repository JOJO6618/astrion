use serde::{Deserialize,Serialize};
use serde_json::Value;
use std::path::PathBuf;

#[derive(Clone,Deserialize,Serialize)]
#[serde(default)]
pub struct Config {pub enabled:bool,pub modifier:String,pub workspace:String,pub model:String}
impl Default for Config {
    fn default()->Self {Self{enabled:false,modifier:"alt".into(),workspace:String::new(),model:String::new()}}
}
pub fn path()->PathBuf {
    std::env::var_os("ASTRION_QUICK_SETTINGS_FILE").map(PathBuf::from).unwrap_or_else(||
        PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default()).join(".astrion/quick-entry-windows.json"))
}
pub fn load()->Result<Config,String> {
    match std::fs::read(path()) {
        Ok(bytes)=>{let config:Config=serde_json::from_slice(&bytes).map_err(|e|e.to_string())?;super::keyboard::Modifier::parse(&config.modifier)?;Ok(config)},
        Err(e) if e.kind()==std::io::ErrorKind::NotFound=>Ok(Config::default()),
        Err(e)=>Err(e.to_string()),
    }
}
pub fn patched(config:&Config,patch:&Value)->Result<Config,String> {
    let mut next=config.clone();
    let object=patch.as_object().ok_or("Invalid configuration")?;
    for (key,value) in object {
        match key.as_str() {
            "enabled"=>next.enabled=value.as_bool().ok_or("Invalid enabled")?,
            "modifier"=>{next.modifier=value.as_str().ok_or("Invalid modifier")?.into();super::keyboard::Modifier::parse(&next.modifier)?;},
            "workspace"=>next.workspace=value.as_str().filter(|s|s.len()<512).ok_or("Invalid workspace")?.into(),
            "model"=>next.model=value.as_str().filter(|s|s.len()<1024).ok_or("Invalid model")?.into(),
            _=>return Err("Unsupported configuration field".into()),
        }
    }
    Ok(next)
}
pub fn save(config:&Config)->Result<(),String> {
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::{MoveFileExW,MOVEFILE_REPLACE_EXISTING,MOVEFILE_WRITE_THROUGH};
    use std::os::windows::ffi::OsStrExt;
    let path=path();
    std::fs::create_dir_all(path.parent().ok_or("Invalid settings path")?).map_err(|e|e.to_string())?;
    let temporary=path.with_extension(format!("{}.tmp",std::process::id()));
    std::fs::write(&temporary,serde_json::to_vec_pretty(config).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    let wide=|p:&std::path::Path|p.as_os_str().encode_wide().chain(Some(0)).collect::<Vec<_>>();
    unsafe {MoveFileExW(PCWSTR(wide(&temporary).as_ptr()),PCWSTR(wide(&path).as_ptr()),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)}.map_err(|e|e.to_string())
}
