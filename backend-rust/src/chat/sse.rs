#[derive(Default)]
pub(crate) struct Decoder {
    line: Vec<u8>,
    event: String,
    data: String,
    skip_lf: bool,
    saw_first_line: bool,
}

impl Decoder {
    pub(crate) fn push(
        &mut self,
        bytes: &[u8],
        mut on_event: impl FnMut(&str, &str) -> Result<bool, String>,
    ) -> Result<bool, String> {
        for &byte in bytes {
            if self.skip_lf {
                self.skip_lf = false;
                if byte == b'\n' {
                    continue;
                }
            }
            match byte {
                b'\r' | b'\n' => {
                    self.skip_lf = byte == b'\r';
                    if self.finish_line(&mut on_event)? {
                        return Ok(true);
                    }
                }
                _ => self.line.push(byte),
            }
        }
        Ok(false)
    }

    /// Dispatches an event buffered at EOF for protocols that accept a missing final blank line.
    pub(crate) fn finish(
        &mut self,
        mut on_event: impl FnMut(&str, &str) -> Result<bool, String>,
    ) -> Result<bool, String> {
        if !self.line.is_empty() && self.finish_line(&mut on_event)? {
            return Ok(true);
        }
        self.finish_line(&mut on_event)
    }

    fn finish_line(
        &mut self,
        on_event: &mut impl FnMut(&str, &str) -> Result<bool, String>,
    ) -> Result<bool, String> {
        let mut line = std::str::from_utf8(&self.line)
            .map_err(|error| format!("invalid UTF-8 in SSE stream: {error}"))?;
        if !self.saw_first_line {
            self.saw_first_line = true;
            line = line.strip_prefix('\u{feff}').unwrap_or(line);
        }
        if line.is_empty() {
            let done = match self.data.strip_suffix('\n') {
                Some(data) => on_event(&self.event, data)?,
                None => false,
            };
            self.event.clear();
            self.data.clear();
            self.line.clear();
            return Ok(done);
        }
        let (field, value) = line.split_once(':').unwrap_or((line, ""));
        let value = value.strip_prefix(' ').unwrap_or(value);
        match field {
            "data" => {
                self.data.push_str(value);
                self.data.push('\n');
            }
            "event" => {
                self.event.clear();
                self.event.push_str(value);
            }
            _ => {}
        }
        self.line.clear();
        Ok(false)
    }
}
