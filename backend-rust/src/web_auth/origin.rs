use super::WebAuthConfig;
use axum::extract::{ConnectInfo, Request};
use axum::http::header::{HOST, ORIGIN, REFERER};
use axum::http::uri::Authority;
use axum::http::{HeaderMap, HeaderName};
use reqwest::Url;
use std::collections::HashSet;
use std::io;
use std::net::{IpAddr, SocketAddr};

pub(super) struct OriginPolicy {
    local: Vec<Url>,
    public: Vec<Url>,
    trusted_proxies: HashSet<IpAddr>,
}

pub(super) struct Source {
    pub client: Option<IpAddr>,
    pub direct_loopback: bool,
}

impl OriginPolicy {
    pub fn new(config: &WebAuthConfig) -> io::Result<Self> {
        let scheme = if config.secure_cookie {
            "https"
        } else {
            "http"
        };
        let local = ["127.0.0.1", "localhost", "[::1]"]
            .iter()
            .map(|host| parse_origin(&format!("{scheme}://{host}:{}", config.port)))
            .collect::<io::Result<_>>()?;
        let public = config
            .allowed_origins
            .iter()
            .map(|origin| parse_origin(origin))
            .collect::<io::Result<_>>()?;
        Ok(Self {
            local,
            public,
            trusted_proxies: config.trusted_proxies.iter().copied().collect(),
        })
    }

    pub fn allows(&self, req: &Request, sensitive: bool) -> bool {
        let Ok(authority) = request_authority(req) else {
            return false;
        };
        let candidates = self
            .local
            .iter()
            .chain(&self.public)
            .filter(|url| matches_authority(url, &authority));
        let Ok(origin) = single_header(req.headers(), ORIGIN) else {
            return false;
        };
        if sensitive {
            let Ok(site) = single_header(req.headers(), HeaderName::from_static("sec-fetch-site"))
            else {
                return false;
            };
            if !matches!(site, None | Some("none" | "same-origin")) {
                return false;
            }
        }
        if let Some(origin) = origin {
            return candidates
                .into_iter()
                .any(|url| url.origin().ascii_serialization() == origin);
        }
        if sensitive {
            let Ok(referer) = single_header(req.headers(), REFERER) else {
                return false;
            };
            if let Some(referer) = referer {
                let Ok(referer) = Url::parse(referer) else {
                    return false;
                };
                return candidates
                    .into_iter()
                    .any(|url| url.origin() == referer.origin());
            }
        }
        candidates.into_iter().next().is_some()
    }

    pub fn source(&self, req: &Request) -> Source {
        let peer = req
            .extensions()
            .get::<ConnectInfo<SocketAddr>>()
            .map(|peer| peer.0.ip());
        let trusted_proxy = peer.is_some_and(|ip| self.trusted_proxies.contains(&ip));
        let headers = req.headers();
        let client = if trusted_proxy {
            single_header(headers, HeaderName::from_static("x-forwarded-for"))
                .ok()
                .flatten()
                .and_then(|value| self.forwarded_client(value))
                .or(peer)
        } else {
            peer
        };
        let forwarded = [
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-forwarded-port",
        ]
        .iter()
        .any(|name| headers.contains_key(*name));
        let local_host = request_authority(req).is_ok_and(|authority| {
            self.local
                .iter()
                .any(|url| matches_authority(url, &authority))
        });
        Source {
            client,
            direct_loopback: peer.is_some_and(|ip| ip.is_loopback())
                && !trusted_proxy
                && !forwarded
                && local_host,
        }
    }

    fn forwarded_client(&self, value: &str) -> Option<IpAddr> {
        // Proxies append their actual peer on the right. Ignore entries to the
        // left of the first untrusted hop because that client can supply them.
        for address in value.rsplit(',') {
            let address = address.trim().parse().ok()?;
            if !self.trusted_proxies.contains(&address) {
                return Some(address);
            }
        }
        None
    }
}

fn request_authority(req: &Request) -> Result<Authority, ()> {
    let host = single_header(req.headers(), HOST)?;
    if let Some(authority) = req.uri().authority() {
        if host.is_some_and(|host| !host.eq_ignore_ascii_case(authority.as_str())) {
            return Err(());
        }
        return Ok(authority.clone());
    }
    host.ok_or(())?.parse().map_err(|_| ())
}

fn matches_authority(url: &Url, authority: &Authority) -> bool {
    // Compare the HTTP authority directly. URL hostname repair would admit aliases
    // that the browser or a proxy could interpret differently.
    let Some(host) = url.host_str() else {
        return false;
    };
    host.eq_ignore_ascii_case(authority.host())
        && url.port_or_known_default()
            == authority.port_u16().or_else(|| match url.scheme() {
                "http" => Some(80),
                "https" => Some(443),
                _ => None,
            })
        && !authority.as_str().contains('@')
        && (authority.port().is_none() || authority.port_u16().is_some())
}

pub(super) fn single_header(headers: &HeaderMap, name: HeaderName) -> Result<Option<&str>, ()> {
    let all = headers.get_all(name);
    let mut values = all.iter();
    let value = values
        .next()
        .map(|value| value.to_str().map_err(|_| ()))
        .transpose()?;
    if values.next().is_some() {
        return Err(());
    }
    Ok(value)
}

fn parse_origin(value: &str) -> io::Result<Url> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidInput, "Invalid Host web origin");
    let url = Url::parse(value).map_err(|_| invalid())?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid());
    }
    Ok(url)
}
