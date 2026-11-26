use std::{ffi::OsStr, io::BufReader};

use jiff::{Timestamp, civil::DateTime, tz::TimeZone};
use time::{Date, Weekday, format_description::well_known::Iso8601};

use super::fs::Dir;

pub(super) struct BackupTime {
    pub text: String,
    pub instant: Timestamp,
}

pub(super) fn backup_time(dir: &Dir, zone: &TimeZone) -> Option<BackupTime> {
    if let Some(value) = metadata_time(dir, zone) {
        return Some(value);
    }
    let instant = Timestamp::try_from(dir.modified().ok()?).ok()?;
    Some(BackupTime {
        text: format_in_local_time(instant, zone, false),
        instant,
    })
}

fn metadata_time(dir: &Dir, zone: &TimeZone) -> Option<BackupTime> {
    let input = BufReader::new(dir.read_file(OsStr::new("meta.json")).ok()?);
    let data: serde_json::Value = serde_json::from_reader(input).ok()?;
    let text = data.get("updated_at")?.as_str()?.trim();
    Some(BackupTime {
        text: text.into(),
        instant: parse_iso_datetime(text, zone)?,
    })
}

fn parse_iso_datetime(input: &str, zone: &TimeZone) -> Option<Timestamp> {
    let (date, tail) = split_date(input)?;
    let (clock, offset) = if let Some(separator) = tail.chars().next() {
        // Python accepts any single separator between the date and clock.
        let clock = &tail[separator.len_utf8()..];
        let (time, offset) = clock
            .find(['+', '-', 'Z'])
            .map_or((clock, ""), |index| clock.split_at(index));
        (parse_clock(time)?, offset)
    } else {
        (
            Clock {
                hour: 0,
                minute: 0,
                second: 0,
                micros: 0,
            },
            "",
        )
    };
    if !(1..=9999).contains(&date.year()) {
        return None;
    }
    let date = DateTime::new(
        date.year().try_into().ok()?,
        u8::from(date.month()) as i8,
        date.day() as i8,
        clock.hour as i8,
        clock.minute as i8,
        clock.second as i8,
        clock.micros * 1_000,
    )
    .ok()?;
    if offset.is_empty() {
        // Python writes updated_at without an offset in the server's local time.
        return Some(zone.to_zoned(date).ok()?.timestamp());
    }
    let utc = TimeZone::UTC.to_zoned(date).ok()?.timestamp();
    let delta = if offset == "Z" {
        0
    } else {
        let (sign, offset) = offset.split_at(1);
        let offset = parse_clock(offset)?;
        let seconds = i64::from(offset.hour) * 3600
            + i64::from(offset.minute) * 60
            + i64::from(offset.second);
        if seconds >= 86400 {
            return None;
        }
        let magnitude = if seconds == 0 {
            0
        } else {
            seconds * 1_000_000 + i64::from(offset.micros)
        };
        match sign {
            "+" => magnitude,
            "-" => -magnitude,
            _ => return None,
        }
    };
    utc.checked_sub(jiff::SignedDuration::from_micros(delta))
        .ok()
}

fn split_date(input: &str) -> Option<(Date, &str)> {
    let bytes = input.as_bytes();
    let extended = bytes.get(4) == Some(&b'-');
    let week_start = if extended { 5 } else { 4 };
    if bytes.get(week_start) == Some(&b'W') {
        let week_end = week_start + 3;
        let year = input.get(..4)?.parse().ok()?;
        if !(1..=9999).contains(&year) || !bytes[..4].iter().all(u8::is_ascii_digit) {
            return None;
        }
        let week = parse_pair(input.get(week_start + 1..week_end)?)?;
        let mut weekday = Weekday::Monday;
        let mut date_end = week_end;
        if !extended || bytes.get(week_end) == Some(&b'-') {
            let index = week_end + usize::from(extended);
            if let Some(day) = bytes.get(index).filter(|day| day.is_ascii_digit()) {
                if !(b'1'..=b'7').contains(day) {
                    return None;
                }
                weekday = Weekday::Monday.nth_next(day - b'1');
                date_end = index + 1;
            }
        }
        return Some((
            Date::from_iso_week_date(year, week, weekday).ok()?,
            &input[date_end..],
        ));
    }
    let end = if extended { 10 } else { 8 };
    let raw = input.get(..end)?;
    // Date::parse also accepts ordinal dates, which Python does not use here.
    if !raw.as_bytes().iter().enumerate().all(|(index, byte)| {
        if extended && matches!(index, 4 | 7) {
            *byte == b'-'
        } else {
            byte.is_ascii_digit()
        }
    }) {
        return None;
    }
    Some((Date::parse(raw, &Iso8601::DEFAULT).ok()?, &input[end..]))
}

struct Clock {
    hour: u8,
    minute: u8,
    second: u8,
    micros: i32,
}

fn parse_clock(text: &str) -> Option<Clock> {
    if !text.is_ascii() {
        return None;
    }
    let (clock, fraction) = text.find(['.', ',']).map_or((text, None), |index| {
        (&text[..index], Some(&text[index + 1..]))
    });
    let (hour, minute, second) = match clock.len() {
        2 => (clock, "00", "00"),
        4 => (&clock[..2], &clock[2..], "00"),
        5 if clock.as_bytes()[2] == b':' => (&clock[..2], &clock[3..], "00"),
        6 => (&clock[..2], &clock[2..4], &clock[4..]),
        8 if clock.as_bytes()[2] == b':' && clock.as_bytes()[5] == b':' => {
            (&clock[..2], &clock[3..5], &clock[6..])
        }
        _ => return None,
    };
    let micros = if let Some(fraction) = fraction {
        if fraction.is_empty() || !fraction.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        fraction
            .bytes()
            .chain(std::iter::repeat(b'0'))
            .take(6)
            .fold(0, |value, digit| value * 10 + i32::from(digit - b'0'))
    } else {
        0
    };
    Some(Clock {
        hour: parse_pair(hour)?,
        minute: parse_pair(minute)?,
        second: parse_pair(second)?,
        micros,
    })
}

fn parse_pair(text: &str) -> Option<u8> {
    match text.as_bytes() {
        [first @ b'0'..=b'9', second @ b'0'..=b'9'] => Some((first - b'0') * 10 + second - b'0'),
        _ => None,
    }
}

pub(super) fn format_in_local_time(instant: Timestamp, zone: &TimeZone, fraction: bool) -> String {
    let format = if fraction {
        "%Y-%m-%dT%H:%M:%S%.9f%:z"
    } else {
        "%Y-%m-%dT%H:%M:%S%:z"
    };
    instant.to_zoned(zone.clone()).strftime(format).to_string()
}
