use super::read_page;
use serde_json::json;
use std::io::{self, Cursor, Read, Seek, SeekFrom, Write};

struct Counted {
    file: Cursor<Vec<u8>>,
    bytes_read: usize,
}
impl Read for Counted {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let count = self.file.read(out)?;
        self.bytes_read += count;
        Ok(count)
    }
}
impl Seek for Counted {
    fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
        self.file.seek(to)
    }
}

#[test]
fn long_log_tail_and_older_page_use_bounded_io() {
    let mut bytes = Vec::new();
    for n in 0..200_000 {
        writeln!(bytes, "{{\"n\":{n}}}").unwrap();
    }
    let len = bytes.len() as u64;
    let mut file = Counted {
        file: Cursor::new(bytes),
        bytes_read: 0,
    };
    let tail = read_page(&mut file, len, None, None, 120).unwrap();
    assert_eq!(tail.entries.len(), 120);
    assert_eq!(tail.entries.first(), Some(&json!({"n":199880})));
    assert_eq!(tail.next_offset, len);
    assert!(tail.has_more_before);
    assert!(file.bytes_read < 70_000);
    let prev = read_page(&mut file, len, None, Some(tail.start_offset), 120).unwrap();
    assert_eq!(prev.entries.last(), Some(&json!({"n":199879})));
    assert_eq!(prev.next_offset, tail.start_offset);
    assert!(file.bytes_read < 140_000);
    file.bytes_read = 0;
    let poll = read_page(&mut file, len, Some(len), None, 120).unwrap();
    assert!(poll.entries.is_empty());
    assert_eq!(file.bytes_read, 1);
}

#[test]
fn pending_record_and_cursor_zero_do_not_drop_backlog() {
    let mut file = Cursor::new(b"{\"n\":".to_vec());
    let initial = read_page(&mut file, 5, None, None, 2).unwrap();
    assert_eq!(initial.next_offset, 0);
    file.get_mut()
        .extend_from_slice(b"1}\n{}\n{\"n\":3}\n{\"n\":");
    let len = file.get_ref().len() as u64;
    let first = read_page(&mut file, len, Some(0), None, 2).unwrap();
    assert_eq!(first.entries, [json!({"n":1}), json!({})]);
    assert!(first.has_more_after);
    let second = read_page(&mut file, len, Some(first.next_offset), None, 2).unwrap();
    assert_eq!(second.entries, [json!({"n":3})]);
    assert!(!second.has_more_after);
    file.get_mut().extend_from_slice(b"4}\n");
    let snapshot = read_page(&mut file, len, Some(second.next_offset), None, 2).unwrap();
    assert!(snapshot.entries.is_empty());
    assert_eq!(snapshot.next_offset, second.next_offset);
    let appended = read_page(&mut file, len + 3, Some(second.next_offset), None, 2).unwrap();
    assert_eq!(appended.entries, [json!({"n":4})]);
}

#[test]
fn handles_reset_zero_before_and_malformed_lines() {
    let mut file = Cursor::new(b"bad\n{}\n[2]\n{}\n".to_vec());
    let len = file.get_ref().len() as u64;
    for invalid in [2, len + 1] {
        let reset = read_page(&mut file, len, Some(invalid), None, 3).unwrap();
        assert!(reset.reset);
        assert_eq!(reset.entries, [json!({}), json!({})]);
        assert_eq!(reset.start_offset, 4);
    }
    let zero = read_page(&mut file, len, None, Some(0), 10).unwrap();
    assert!(zero.entries.is_empty());
    assert!(!zero.reset);
    assert!(read_page(&mut file, len, Some(0), Some(0), 1).is_err());
}

#[test]
fn only_complete_following_records_trigger_immediate_fetch() {
    let mut file = Cursor::new(b"{}\n{\"n\":".to_vec());
    let len = file.get_ref().len() as u64;
    for offset in [None, Some(0)] {
        let page = read_page(&mut file, len, offset, None, 1).unwrap();
        assert!(page.partial_tail);
        assert!(!page.has_more_after && !page.truncated);
    }
    let older = read_page(&mut file, len, None, Some(3), 1).unwrap();
    assert!(!older.partial_tail);
    file.get_mut().extend_from_slice(b"4}\n");
    let len = file.get_ref().len() as u64;
    assert!(
        read_page(&mut file, len, Some(0), None, 1)
            .unwrap()
            .has_more_after
    );
    assert!(
        !read_page(&mut file, len, Some(len), None, 1)
            .unwrap()
            .partial_tail
    );
}
