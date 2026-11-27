use std::{io, time::Duration};

use serde_json::Value;

/// Fetch a public JSON reference dataset without following redirects or sending
/// model credentials. A timeout and a size bound apply to the download.
#[derive(Clone)]
pub(super) struct ReferenceSource {
    client: reqwest::Client,
    pub(super) url: String,
    max_bytes: usize,
}

impl ReferenceSource {
    pub(super) fn new(url: &str, max_bytes: usize) -> Self {
        Self {
            client: crate::net::apply(
                reqwest::Client::builder()
                    .redirect(reqwest::redirect::Policy::none())
                    .timeout(Duration::from_secs(8)),
                crate::net::resolve(None).as_ref(),
            )
            .build()
            .expect("reference data HTTP client"),
            url: url.to_owned(),
            max_bytes,
        }
    }

    pub(super) async fn fetch(&self) -> io::Result<Value> {
        let mut response = self
            .client
            .get(self.url.as_str())
            .send()
            .await
            .map_err(http_error)?;
        if response.status() != reqwest::StatusCode::OK {
            return Err(io::Error::other(format!(
                "reference source HTTP {}",
                response.status()
            )));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(http_error)? {
            if chunk.len() > self.max_bytes.saturating_sub(bytes.len()) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "reference data exceeds size limit",
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        tokio::task::spawn_blocking(move || serde_json::from_slice(&bytes).map_err(Into::into))
            .await
            .map_err(io::Error::other)?
    }
}

fn http_error(error: reqwest::Error) -> io::Error {
    io::Error::other(error.without_url())
}
