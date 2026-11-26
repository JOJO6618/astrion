use rusqlite::{Connection, OptionalExtension, params};
use serde::Serialize;
use serde_json::Value;
use std::io;
#[cfg(test)]
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};

use super::registry::sql;

#[cfg(test)]
const BLOCK: usize = 64 * 1024;

#[derive(Debug, Serialize)]
pub struct ActivityPage {
    pub entries: Vec<Value>,
    pub start_offset: u64,
    pub next_offset: u64,
    pub file_size: u64,
    pub reset: bool,
    pub truncated: bool,
    pub has_more_before: bool,
    pub has_more_after: bool,
    pub partial_tail: bool,
}

pub(super) fn empty(offset: Option<u64>, before: Option<u64>) -> ActivityPage {
    ActivityPage {
        entries: Vec::new(),
        start_offset: 0,
        next_offset: 0,
        file_size: 0,
        reset: offset.is_some() || before.is_some(),
        truncated: false,
        has_more_before: false,
        has_more_after: false,
        partial_tail: false,
    }
}

fn complete_end(db: &Connection, id: &str) -> io::Result<u64> {
    db.query_row(
        "SELECT COALESCE(MAX(end_offset),0) FROM child_activity WHERE task_id=?",
        [id],
        |row| row.get(0),
    )
    .map_err(sql)
}

pub(super) fn append(db: &Connection, id: &str, event: &Value) -> io::Result<()> {
    let start = complete_end(db, id)?;
    let encoded = event.to_string();
    let next = start
        .checked_add(encoded.len() as u64 + 1)
        .ok_or(io::ErrorKind::InvalidData)?;
    insert(db, id, start, next, Some(&encoded))?;
    db.execute(
        "UPDATE child_task SET activity_tail=0 WHERE id=? AND activity_tail<>0",
        [id],
    )
    .map_err(sql)?;
    Ok(())
}

pub(super) fn insert(
    db: &Connection,
    id: &str,
    start: u64,
    end: u64,
    event: Option<&str>,
) -> io::Result<()> {
    db.prepare_cached("INSERT INTO child_activity VALUES(?,?,?,?)")
        .map_err(sql)?
        .execute(params![id, start, end, event])
        .map_err(sql)?;
    Ok(())
}

pub(super) fn read_database_page(
    db: &Connection,
    id: &str,
    offset: Option<u64>,
    before: Option<u64>,
    limit: usize,
) -> io::Result<ActivityPage> {
    if limit == 0 || (offset.is_some() && before.is_some()) {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    let complete = complete_end(db, id)?;
    let tail = db
        .query_row(
            "SELECT activity_tail FROM child_task WHERE id=?",
            [id],
            |row| row.get::<_, u64>(0),
        )
        .optional()
        .map_err(sql)?
        .unwrap_or_default();
    let length = complete
        .checked_add(tail)
        .ok_or(io::ErrorKind::InvalidData)?;
    if length == 0 {
        return Ok(empty(offset, before));
    }
    let cursor = offset.or(before).unwrap_or_default();
    let valid = cursor == 0
        || (cursor <= complete
            && db
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM child_activity WHERE task_id=? AND end_offset=?)",
                    params![id, cursor],
                    |row| row.get(0),
                )
                .map_err(sql)?);
    let forward = valid && offset.is_some();
    let (query, boundary) = if forward {
        (
            "SELECT start_offset,end_offset,event FROM child_activity WHERE task_id=? AND end_offset>? ORDER BY end_offset LIMIT ?",
            cursor,
        )
    } else {
        (
            "SELECT start_offset,end_offset,event FROM child_activity WHERE task_id=? AND end_offset<=? ORDER BY end_offset DESC LIMIT ?",
            if valid {
                before.unwrap_or(complete)
            } else {
                complete
            },
        )
    };
    let mut statement = db.prepare_cached(query).map_err(sql)?;
    let rows = statement
        .query_map(params![id, boundary, limit], |row| {
            Ok((
                row.get::<_, u64>(0)?,
                row.get::<_, u64>(1)?,
                row.get::<_, Option<String>>(2)?,
            ))
        })
        .map_err(sql)?;
    let mut rows = rows.collect::<rusqlite::Result<Vec<_>>>().map_err(sql)?;
    if !forward {
        rows.reverse();
    }
    let start = rows
        .first()
        .map_or(if forward { cursor } else { 0 }, |row| row.0);
    let next = rows.last().map_or(start, |row| row.1);
    let more_after = forward && next < complete;
    let entries = rows
        .into_iter()
        .filter_map(|row| row.2)
        .map(|event| serde_json::from_str(&event))
        .collect::<serde_json::Result<Vec<_>>>()?;
    Ok(ActivityPage {
        entries,
        start_offset: start,
        next_offset: next,
        file_size: length,
        reset: !valid,
        truncated: false,
        has_more_before: !forward && start > 0,
        has_more_after: more_after,
        partial_tail: (before.is_none() || !valid) && next < length && !more_after,
    })
}

/// All cursors refer to complete JSONL records in a single open file. The caller
/// obtains `length` from that handle, so appends during a request belong to the next poll.
#[cfg(test)]
pub fn read_page<R: Read + Seek>(
    file: &mut R,
    length: u64,
    offset: Option<u64>,
    before: Option<u64>,
    limit: usize,
) -> io::Result<ActivityPage> {
    if limit == 0 || (offset.is_some() && before.is_some()) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid activity page",
        ));
    }
    let cursor = offset.or(before).unwrap_or(0);
    let valid = if cursor == 0 {
        true
    } else if cursor > length {
        false
    } else {
        file.seek(SeekFrom::Start(cursor - 1))?;
        let mut byte = [0];
        file.read_exact(&mut byte)?;
        byte[0] == b'\n'
    };
    let forward = valid && offset.is_some();
    let (start, end) = if forward {
        (cursor, length)
    } else {
        tail_range(
            file,
            if valid {
                before.unwrap_or(length)
            } else {
                length
            },
            limit,
        )?
    };
    let mut entries = Vec::new();
    let mut next = start;
    let mut consumed = 0;
    let mut has_more_after = false;
    if end > start {
        file.seek(SeekFrom::Start(start))?;
        let mut reader = BufReader::new(file.take(end - start));
        let mut line = Vec::new();
        while consumed < limit {
            line.clear();
            let size = reader.read_until(b'\n', &mut line)?;
            if size == 0 || line.last() != Some(&b'\n') {
                break;
            }
            next += size as u64;
            consumed += 1;
            if let Ok(value @ Value::Object(_)) = serde_json::from_slice(&line) {
                entries.push(value);
            }
        }
        if forward && consumed == limit && next < end {
            loop {
                let buf = reader.fill_buf()?;
                if buf.is_empty() {
                    break;
                }
                if buf.contains(&b'\n') {
                    has_more_after = true;
                    break;
                }
                let count = buf.len();
                reader.consume(count);
            }
        }
    }
    Ok(ActivityPage {
        entries,
        start_offset: start,
        next_offset: next,
        file_size: length,
        reset: !valid,
        truncated: false,
        has_more_before: !forward && start > 0,
        has_more_after,
        partial_tail: (before.is_none() || !valid) && next < length && !has_more_after,
    })
}

/// Scan blocks back to the separator immediately before the requested page.
/// Even a large, unfinished trailing record only occupies a single scan buffer.
#[cfg(test)]
fn tail_range<R: Read + Seek>(file: &mut R, end: u64, limit: usize) -> io::Result<(u64, u64)> {
    let mut position = end;
    let mut complete_end = 0;
    let mut seen = 0;
    let mut bytes = [0; BLOCK];
    while position != 0 {
        let size = position.min(BLOCK as u64) as usize;
        position -= size as u64;
        file.seek(SeekFrom::Start(position))?;
        file.read_exact(&mut bytes[..size])?;
        for i in (0..size).rev().filter(|&i| bytes[i] == b'\n') {
            let boundary = position + i as u64 + 1;
            if seen == limit {
                return Ok((boundary, complete_end));
            }
            if seen == 0 {
                complete_end = boundary;
            }
            seen += 1;
        }
    }
    Ok((0, complete_end))
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/sub_activity_contract.rs"]
mod tests;
