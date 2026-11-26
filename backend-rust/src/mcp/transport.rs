use std::{process::Stdio, time::Duration};

use reqwest::{
    Client as HttpClient, Method, Response, StatusCode, Url,
    header::{HeaderMap, HeaderName, HeaderValue},
};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
};

use super::registry::Server;

const MAX_MESSAGE: usize = 16 * 1024 * 1024;

pub(super) struct Client {
    transport: Transport,
    sequence: u64,
    version: String,
    negotiated: String,
    timeout: Duration,
}

enum Transport {
    Stdio(StdioClient),
    Http(Http),
}

impl Client {
    pub async fn connect(server: &Server, version: &str) -> Result<Self, String> {
        let timeout = Duration::from_secs(server.timeout_seconds.max(3));
        let transport = match server.transport.as_str() {
            "stdio" => Transport::Stdio(StdioClient::start(server)?),
            "streamable_http" => Transport::Http(Http::new(server)?),
            _ => return Err("Unsupported MCP transport".into()),
        };
        let mut client = Self {
            transport,
            sequence: 0,
            timeout,
            version: version.into(),
            negotiated: version.into(),
        };
        client.initialize().await?;
        Ok(client)
    }

    pub fn set_timeout(&mut self, seconds: u64) {
        self.timeout = Duration::from_secs(seconds.max(3));
    }

    pub fn alive(&mut self) -> bool {
        match &mut self.transport {
            Transport::Stdio(stdio) => matches!(stdio.process.try_wait(), Ok(None)),
            Transport::Http(_) => true,
        }
    }

    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        match self.exchange(method, params.clone(), false).await {
            Ok(result) => Ok(result),
            Err(Failure::Expired) => {
                if let Transport::Http(http) = &mut self.transport {
                    http.session = None;
                }
                self.negotiated.clone_from(&self.version);
                self.initialize().await?;
                self.exchange(method, params, false)
                    .await
                    .map_err(Failure::message)
            }
            Err(error) => Err(error.message()),
        }
    }

    async fn initialize(&mut self) -> Result<(), String> {
        let result = self
            .exchange(
                "initialize",
                json!({
                    "protocolVersion": self.version,
                    "capabilities": {"roots": {"listChanged": false}},
                    "clientInfo": {"name": "agents-mcp-bridge", "version": "1.0.0"},
                }),
                false,
            )
            .await
            .map_err(Failure::message)?;
        if let Some(version) = result["protocolVersion"].as_str() {
            self.negotiated = version.into();
        }
        self.exchange("notifications/initialized", json!({}), true)
            .await
            .map_err(Failure::message)?;
        Ok(())
    }

    async fn exchange(
        &mut self,
        method: &str,
        params: Value,
        notification: bool,
    ) -> Result<Value, Failure> {
        let mut payload = json!({"jsonrpc": "2.0", "method": method, "params": params});
        let id = if notification {
            None
        } else {
            self.sequence += 1;
            payload["id"] = json!(self.sequence);
            Some(self.sequence)
        };
        let exchange = async {
            match &mut self.transport {
                Transport::Stdio(stdio) => stdio.exchange(payload, id).await,
                Transport::Http(http) => http.exchange(&self.negotiated, payload, id).await,
            }
        };
        tokio::time::timeout(self.timeout, exchange)
            .await
            .map_err(|_| Failure::text("MCP request timed out"))?
    }

    pub async fn close(mut self) {
        if let Transport::Http(http) = &mut self.transport {
            if let Some(session) = http.session.take() {
                let _ = tokio::time::timeout(
                    self.timeout,
                    http.send(Method::DELETE, &self.negotiated, None, Some(&session)),
                )
                .await;
            }
        }
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        if let Transport::Http(http) = &mut self.transport
            && let Some(session) = http.session.take()
            && let Ok(runtime) = tokio::runtime::Handle::try_current()
        {
            let http = http.clone();
            let version = self.negotiated.clone();
            let timeout = self.timeout;
            runtime.spawn(async move {
                let _ = tokio::time::timeout(
                    timeout,
                    http.send(Method::DELETE, &version, None, Some(&session)),
                )
                .await;
            });
        }
    }
}

enum Failure {
    Expired,
    Other(String),
}

impl Failure {
    fn text(message: impl Into<String>) -> Self {
        Self::Other(message.into())
    }
    fn message(self) -> String {
        match self {
            Self::Expired => "MCP session expired".into(),
            Self::Other(message) => message,
        }
    }
}

fn response(message: Value, id: u64) -> Option<Result<Value, Failure>> {
    if message["id"].as_u64() != Some(id) || message.get("method").is_some() {
        return None;
    }
    if let Some(error) = message.get("error") {
        let detail = error["message"]
            .as_str()
            .unwrap_or("unknown error")
            .chars()
            .take(400)
            .collect::<String>();
        return Some(Err(Failure::text(format!("MCP JSON-RPC error: {detail}"))));
    }
    Some(Ok(message
        .get("result")
        .cloned()
        .unwrap_or_else(|| json!({}))))
}

fn server_request(message: &Value) -> Option<Value> {
    let method = message["method"].as_str()?;
    let id = message.get("id")?;
    Some(match method {
        "ping" => json!({"jsonrpc": "2.0", "id": id, "result": {}}),
        "roots/list" => json!({"jsonrpc": "2.0", "id": id, "result": {"roots": []}}),
        _ => {
            json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32601, "message": "Client method not implemented"}})
        }
    })
}

struct StdioClient {
    process: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl StdioClient {
    fn start(server: &Server) -> Result<Self, String> {
        let mut command = Command::new(&server.command);
        command
            .args(&server.args)
            .envs(&server.env)
            .kill_on_drop(true)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if !server.cwd.is_empty() {
            command.current_dir(&server.cwd);
        }
        let mut process = command
            .spawn()
            .map_err(|error| format!("MCP server launch failed: {error}"))?;
        let stdin = process.stdin.take().ok_or("Missing MCP stdin")?;
        let stdout = BufReader::new(process.stdout.take().ok_or("Missing MCP stdout")?);
        Ok(Self {
            process,
            stdin,
            stdout,
        })
    }

    async fn send(&mut self, payload: Value) -> Result<(), Failure> {
        let mut bytes = serde_json::to_vec(&payload)
            .map_err(|_| Failure::text("Invalid MCP JSON-RPC payload"))?;
        bytes.push(b'\n');
        self.stdin
            .write_all(&bytes)
            .await
            .map_err(|_| Failure::text("MCP stdio write failed"))?;
        self.stdin
            .flush()
            .await
            .map_err(|_| Failure::text("MCP stdio flush failed"))
    }

    async fn exchange(&mut self, request: Value, id: Option<u64>) -> Result<Value, Failure> {
        self.send(request).await?;
        let Some(id) = id else {
            return Ok(json!({}));
        };
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            let mut bounded = (&mut self.stdout).take((MAX_MESSAGE + 1) as u64);
            let size = bounded
                .read_until(b'\n', &mut bytes)
                .await
                .map_err(|error| Failure::text(format!("MCP stdio read failed: {error}")))?;
            match size {
                0 => return Err(Failure::text("MCP server closed stdout")),
                count if count > MAX_MESSAGE => {
                    return Err(Failure::text("MCP response exceeds size limit"));
                }
                _ => {}
            }
            let Ok(message) = serde_json::from_slice::<Value>(&bytes) else {
                continue;
            };
            if let Some(reply) = server_request(&message) {
                self.send(reply).await?;
            } else if let Some(result) = response(message, id) {
                return result;
            }
        }
    }
}

#[derive(Clone)]
struct Http {
    client: HttpClient,
    url: Url,
    headers: HeaderMap,
    session: Option<HeaderValue>,
}

impl Http {
    fn new(server: &Server) -> Result<Self, String> {
        let url = Url::parse(&server.url).map_err(|_| "Invalid MCP URL")?;
        if !matches!(url.scheme(), "http" | "https") {
            return Err("Invalid MCP URL scheme".into());
        }
        let mut headers = HeaderMap::new();
        for (key, value) in &server.headers {
            let key = HeaderName::from_bytes(key.as_bytes())
                .map_err(|_| "Invalid MCP HTTP header name")?;
            let mut value =
                HeaderValue::from_str(value).map_err(|_| "Invalid MCP HTTP header value")?;
            value.set_sensitive(true);
            headers.insert(key, value);
        }
        let client = HttpClient::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "Could not create MCP HTTP client")?;
        Ok(Self {
            client,
            url,
            headers,
            session: None,
        })
    }

    async fn send(
        &self,
        method: Method,
        version: &str,
        payload: Option<&Value>,
        session: Option<&HeaderValue>,
    ) -> Result<Response, Failure> {
        let mut headers = HeaderMap::new();
        headers.insert(
            "accept",
            HeaderValue::from_static("application/json, text/event-stream"),
        );
        headers.insert("content-type", HeaderValue::from_static("application/json"));
        headers.insert(
            "mcp-protocol-version",
            HeaderValue::from_str(version)
                .map_err(|_| Failure::text("Invalid MCP protocol version"))?,
        );
        if let Some(session) = session {
            headers.insert("mcp-session-id", session.clone());
        }
        if let Some(payload) = payload
            && let Some(method) = payload["method"].as_str()
        {
            headers.insert(
                "mcp-method",
                HeaderValue::from_str(method).map_err(|_| Failure::text("Invalid MCP method"))?,
            );
            if let Some(name) = match method {
                "tools/call" | "prompts/get" => payload["params"]["name"].as_str(),
                "resources/read" => payload["params"]["uri"].as_str(),
                _ => None,
            } && let Ok(name) = HeaderValue::from_str(name)
            {
                headers.insert("mcp-name", name);
            }
        }
        headers.extend(self.headers.clone());
        let mut request = self
            .client
            .request(method, self.url.clone())
            .headers(headers);
        if let Some(payload) = payload {
            request = request.json(payload);
        }
        request.send().await.map_err(|error| {
            Failure::text(if error.is_timeout() {
                "MCP HTTP request timed out"
            } else {
                "MCP HTTP request failed"
            })
        })
    }

    async fn exchange(
        &mut self,
        version: &str,
        payload: Value,
        id: Option<u64>,
    ) -> Result<Value, Failure> {
        let mut reply = self
            .send(Method::POST, version, Some(&payload), self.session.as_ref())
            .await?;
        let status = reply.status();
        if status == StatusCode::NOT_FOUND
            && self.session.is_some()
            && payload["method"] != "initialize"
        {
            return Err(Failure::Expired);
        }
        if !status.is_success() {
            return Err(Failure::text(format!("MCP HTTP returned {status}")));
        }
        if let Some(session) = reply.headers().get("mcp-session-id") {
            self.session = Some(session.clone());
        }
        let Some(id) = id else {
            return Ok(json!({}));
        };
        let sse = reply
            .headers()
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.to_ascii_lowercase().contains("text/event-stream"));
        let mut body = Vec::new();
        let mut line = Vec::new();
        let mut event = Vec::new();
        while let Some(chunk) = reply
            .chunk()
            .await
            .map_err(|_| Failure::text("MCP HTTP response read failed"))?
        {
            if !sse {
                extend(&mut body, &chunk)?;
                continue;
            }
            for part in chunk.split_inclusive(|byte| *byte == b'\n') {
                extend(&mut line, part)?;
                if !line.ends_with(b"\n") {
                    continue;
                }
                let data_line = line
                    .strip_suffix(b"\r\n")
                    .or_else(|| line.strip_suffix(b"\n"))
                    .unwrap();
                if data_line.is_empty() && !event.is_empty() {
                    if let Some(result) = self.handle_event(version, &event, id).await? {
                        return Ok(result);
                    }
                    event.clear();
                } else if let Some(data) = data_line.strip_prefix(b"data:") {
                    let data = data.strip_prefix(b" ").unwrap_or(data);
                    extend(&mut event, data)?;
                    extend(&mut event, b"\n")?;
                }
                line.clear();
            }
        }
        if sse {
            if let Some(data) = line.strip_prefix(b"data:") {
                extend(&mut event, data)?;
            }
            if let Some(result) = self.handle_event(version, &event, id).await? {
                return Ok(result);
            }
        } else if let Ok(message) = serde_json::from_slice(&body)
            && let Some(result) = response(message, id)
        {
            return result;
        }
        Err(Failure::text(
            "MCP HTTP response did not contain the requested result",
        ))
    }

    async fn handle_event(
        &self,
        version: &str,
        event: &[u8],
        id: u64,
    ) -> Result<Option<Value>, Failure> {
        let Ok(message) = serde_json::from_slice::<Value>(event) else {
            return Ok(None);
        };
        if let Some(reply) = server_request(&message) {
            let status = self
                .send(Method::POST, version, Some(&reply), self.session.as_ref())
                .await?
                .status();
            if !status.is_success() {
                return Err(Failure::text(format!(
                    "MCP HTTP response to server returned {status}"
                )));
            }
        }
        response(message, id).transpose()
    }
}

fn extend(buffer: &mut Vec<u8>, bytes: &[u8]) -> Result<(), Failure> {
    if buffer.len().saturating_add(bytes.len()) > MAX_MESSAGE {
        return Err(Failure::text("MCP response exceeds size limit"));
    }
    buffer.extend_from_slice(bytes);
    Ok(())
}
