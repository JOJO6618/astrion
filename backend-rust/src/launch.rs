use astrion_headless::runtime::ModelConfig;
use std::{
    env, fs,
    io::{self, Write},
    net::{IpAddr, Ipv4Addr},
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub const USAGE: &str = "astrion-headless --workspace DIR --data-dir DATA_DIR [--web --static-root DIR --bind IP] [--endpoint MODEL_URL --model NAME] [--port PORT]\nDATA_DIR is the existing Astrion host/data directory. Optional provider credential: ASTRION_MODEL_API_KEY.";

pub fn configure_password() -> io::Result<()> {
    let mut args = env::args_os().skip(2);
    let mut directory = None;
    let mut password = None;
    let usage = || io::Error::other("host-password --password VALUE [--data-dir DIR]");
    while let Some(arg) = args.next() {
        match arg.to_str() {
            Some("--data-dir") if directory.is_none() => directory = Some(PathBuf::from(args.next().ok_or_else(usage)?)),
            Some("--password") if password.is_none() => password = args.next().and_then(|p| p.into_string().ok()),
            _ => return Err(usage()),
        }
    }
    let password = password.ok_or_else(usage)?;
    let directory = match directory {
        Some(dir) => dir,
        None => astrion_headless::catalog::CatalogSource::from_environment(&env::current_dir()?)
            .map_err(io::Error::other)?.host_data_dir().to_owned(),
    };
    astrion_headless::web_auth::set_host_password(&directory, &password)
}

pub struct Launch {
    pub workspace: PathBuf,
    pub data: PathBuf,
    pub port: u16,
    pub bind: IpAddr,
    pub web: bool,
    pub static_root: Option<PathBuf>,
    pub model: Option<ModelConfig>,
}

impl Launch {
    pub fn parse() -> Result<Self, String> {
        let mut args = env::args_os().skip(1);
        let (mut workspace, mut data, mut endpoint, mut model, mut static_root) =
            (None, None, None, None, None);
        let mut port = 8092;
        let mut bind = IpAddr::V4(Ipv4Addr::LOCALHOST);
        let mut web = false;
        while let Some(flag) = args.next() {
            let flag = flag.to_str().ok_or("Invalid option")?;
            if flag == "--web" {
                web = true;
                continue;
            }
            let value = args
                .next()
                .ok_or_else(|| format!("Missing value for {flag}"))?;
            match flag {
                "--workspace" => workspace = Some(PathBuf::from(value)),
                "--data-dir" => data = Some(PathBuf::from(value)),
                "--static-root" => static_root = Some(PathBuf::from(value)),
                "--port" => {
                    port = value
                        .to_str()
                        .ok_or("Invalid port")?
                        .parse()
                        .map_err(|_| "Invalid port")?
                }
                "--bind" => {
                    bind = value
                        .to_str()
                        .ok_or("Invalid bind address")?
                        .parse()
                        .map_err(|_| "Bind address must be an IPv4 or IPv6 address")?;
                }
                "--endpoint" => {
                    endpoint = Some(value.into_string().map_err(|_| "Invalid model endpoint")?)
                }
                "--model" => model = Some(value.into_string().map_err(|_| "Invalid model name")?),
                _ => return Err(format!("Unknown option {flag}")),
            }
        }
        let workspace = std::path::absolute(workspace.ok_or("Missing --workspace")?)
            .map_err(|e| e.to_string())?;
        if !workspace.is_dir() {
            return Err("Workspace must be a directory".into());
        }
        let data =
            std::path::absolute(data.ok_or("Missing --data-dir")?).map_err(|e| e.to_string())?;
        if !web && (static_root.is_some() || !bind.is_loopback()) {
            return Err("A static root or non-loopback bind address requires --web".into());
        }
        let model = match (endpoint, model) {
            (None, None) => None,
            (Some(endpoint), Some(model)) if !model.trim().is_empty() => {
                let url = reqwest::Url::parse(&endpoint).map_err(|_| "Invalid model endpoint")?;
                if !matches!(url.scheme(), "http" | "https")
                    || url.host().is_none()
                    || !url.username().is_empty()
                    || url.password().is_some()
                {
                    return Err("Invalid HTTP(S) model endpoint".into());
                }
                Some(ModelConfig::fixed(
                    endpoint,
                    env::var("ASTRION_MODEL_API_KEY")
                        .ok()
                        .filter(|s| !s.is_empty()),
                    model,
                ))
            }
            _ => return Err("Specify both --endpoint and --model".into()),
        };
        Ok(Self {
            workspace,
            data,
            port,
            bind,
            web,
            static_root,
            model,
        })
    }
    pub fn address(&self) -> (IpAddr, u16) {
        (self.bind, self.port)
    }
}

pub fn read_or_create_token(data: &Path) -> io::Result<String> {
    let path = data.join("host_api_token");
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    match options.open(&path) {
        Ok(mut file) => {
            let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
            writeln!(file, "{token}")?;
            file.sync_all()?;
            Ok(token)
        }
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
            let token = fs::read_to_string(path)?.trim().to_owned();
            if token.is_empty() {
                return Err(io::Error::other("Empty host API credential"));
            }
            Ok(token)
        }
        Err(e) => Err(e),
    }
}
