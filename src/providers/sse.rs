//! Minimal Server-Sent Events reader over a streaming HTTP response.

use std::pin::Pin;

use futures_util::{Stream, StreamExt};

type ByteStream = dyn Stream<Item = reqwest::Result<bytes::Bytes>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SseEvent {
    pub event: Option<String>,
    pub data: String,
}

pub struct SseStream {
    inner: Pin<Box<ByteStream>>,
    buffer: Vec<u8>,
    finished: bool,
}

impl SseStream {
    pub fn new(response: reqwest::Response) -> Self {
        SseStream {
            inner: Box::pin(response.bytes_stream()),
            buffer: Vec::new(),
            finished: false,
        }
    }

    /// Next event with a non-empty payload; `None` at end of stream.
    pub async fn next_event(&mut self) -> Option<Result<SseEvent, String>> {
        loop {
            if let Some(end) = find_boundary(&self.buffer) {
                let raw: Vec<u8> = self.buffer.drain(..end + 2).collect();
                if let Some(event) = parse_event(&raw) {
                    return Some(Ok(event));
                }
                continue;
            }
            if self.finished {
                if self.buffer.is_empty() {
                    return None;
                }
                let raw = std::mem::take(&mut self.buffer);
                return parse_event(&raw).map(Ok);
            }
            match self.inner.next().await {
                Some(Ok(chunk)) => self.buffer.extend(chunk.iter().filter(|b| **b != b'\r')),
                Some(Err(e)) => return Some(Err(e.to_string())),
                None => self.finished = true,
            }
        }
    }
}

fn find_boundary(buffer: &[u8]) -> Option<usize> {
    buffer.windows(2).position(|w| w == b"\n\n")
}

fn parse_event(raw: &[u8]) -> Option<SseEvent> {
    let text = String::from_utf8_lossy(raw);
    let mut event = None;
    let mut data_lines = Vec::new();
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("data:") {
            data_lines.push(value.strip_prefix(' ').unwrap_or(value).to_string());
        } else if let Some(value) = line.strip_prefix("event:") {
            event = Some(value.trim().to_string());
        }
    }
    let data = data_lines.join("\n");
    if data.is_empty() || data == "[DONE]" {
        return None;
    }
    Some(SseEvent { event, data })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_events() {
        assert_eq!(
            parse_event(b"event: message_start\ndata: {\"a\":1}\n"),
            Some(SseEvent {
                event: Some("message_start".into()),
                data: "{\"a\":1}".into()
            })
        );
        assert_eq!(parse_event(b": comment\n"), None);
        assert_eq!(parse_event(b"data: [DONE]\n"), None);
        assert_eq!(parse_event(b"data: a\ndata: b\n").unwrap().data, "a\nb");
        assert_eq!(find_boundary(b"data: x\n\ndata: y"), Some(7));
    }
}
