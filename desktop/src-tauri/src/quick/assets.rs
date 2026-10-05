use std::path::{Path,PathBuf};
use std::collections::BTreeSet;

pub fn static_root(app:&tauri::AppHandle)->Result<PathBuf,String> {
    use tauri::Manager;
    if let Ok(resources)=app.path().resource_dir(){
        let path=resources.join("runtime/backend/static");
        if path.join("quick.html").is_file(){return Ok(path);}
    }
    let path=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../static");
    path.canonicalize().map_err(|e|e.to_string())
}
fn styles(manifest:&serde_json::Value,key:&str,visited:&mut BTreeSet<String>,css:&mut Vec<String>)->Result<(),String>{
    if !visited.insert(key.into()){return Ok(());}
    let chunk=manifest.get(key).ok_or_else(||format!("Missing manifest entry: {key}"))?;
    if let Some(imports)=chunk["imports"].as_array(){for imported in imports {styles(manifest,imported.as_str().ok_or("Invalid import")?,visited,css)?;}}
    if let Some(items)=chunk["css"].as_array(){for item in items {
        let value=item.as_str().ok_or("Invalid CSS asset")?;
        if value.contains("..") || value.starts_with('/') || !value.ends_with(".css"){return Err("Invalid CSS path".into());}
        if !css.iter().any(|s|s==value){css.push(value.into());}
    }}
    Ok(())
}
pub fn serve(root:&Path,path:&str)->Result<(&'static str,Vec<u8>),String>{
    let relative=match path {
        "/"|"/quick"=>"quick.html", "/capture"=>"quick-capture/capture.html",
        value if value.starts_with("/capture/")=>&value[9..],
        value=>value.strip_prefix("/static/").unwrap_or(value.trim_start_matches('/')),
    };
    let relative=if path.starts_with("/capture/"){format!("quick-capture/{relative}")}else{relative.into()};
    if relative.contains("..") || relative.contains('%') || relative.contains('\\'){return Err("Invalid resource path".into());}
    let target=root.join(&relative).canonicalize().map_err(|e|e.to_string())?;
    if !target.starts_with(root.canonicalize().map_err(|e|e.to_string())?){return Err("Resource outside static directory".into());}
    let mime=match target.extension().and_then(|s|s.to_str()) {
        Some("html")=>"text/html; charset=utf-8",Some("js")=>"text/javascript; charset=utf-8",Some("css")=>"text/css; charset=utf-8",
        Some("svg")=>"image/svg+xml",Some("woff2")=>"font/woff2",Some("woff")=>"font/woff",Some("png")=>"image/png",_=>return Err("Unsupported resource".into()),
    };
    let mut bytes=std::fs::read(target).map_err(|e|e.to_string())?;
    if relative=="quick.html" {
        let manifest:serde_json::Value=serde_json::from_slice(&std::fs::read(root.join("dist/.vite/manifest.json")).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        let mut css=Vec::new();styles(&manifest,"static/src/quick.ts",&mut BTreeSet::new(),&mut css)?;
        let links=css.iter().map(|s|format!("<link rel=\"stylesheet\" href=\"/static/dist/{s}\"> ")).collect::<String>();
        bytes=String::from_utf8(bytes).map_err(|e|e.to_string())?.replace("<link rel=\"stylesheet\" href=\"/static/dist/assets/quick.css\">",&links).into_bytes();
    }
    Ok((mime,bytes))
}
