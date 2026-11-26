use std::collections::BTreeSet;

use jiff::{
    Timestamp,
    civil::{Date, Time},
    tz::{self, TimeZone},
};
use serde_json::{Value, json};

use super::{
    Result, ScheduleError,
    schema::{integer, object},
};

const SEARCH_DAYS: usize = 366 * 8;
const NANOS_PER_SECOND: i128 = 1_000_000_000;

pub(crate) fn instant(value: &str) -> Result<Timestamp> {
    value
        .parse()
        .map_err(|_| ScheduleError::invalid("时间点应为包含时区的 ISO 日期时间"))
}

pub(crate) fn iso(value: Timestamp) -> String {
    format!("{value:.6}")
}

pub(crate) fn zone_names() -> Vec<String> {
    let mut names: Vec<_> = tz::db()
        .available()
        .map(|name| name.as_str().to_owned())
        .collect();
    names.sort_unstable();
    names.dedup();
    names
}

#[derive(Debug)]
enum Frequency {
    Once(Date),
    Interval { anchor: Timestamp, step: i128 },
    Daily,
    Weekly(BTreeSet<u32>),
    Monthly(Option<u32>),
    Cron(Cron),
}

#[derive(Debug)]
pub(crate) struct Rule {
    frequency: Frequency,
    zone: TimeZone,
    times: Vec<Time>,
}

impl Rule {
    pub(crate) fn read(
        value: &Value,
        now: Timestamp,
        previous: Option<&Value>,
    ) -> Result<(Self, Value)> {
        let data = object(
            value,
            &[
                "type",
                "timezone",
                "times",
                "days",
                "day",
                "every",
                "unit",
                "date",
                "expression",
                "anchor",
            ],
            "schedule",
        )?;
        let kind = value["type"]
            .as_str()
            .ok_or_else(|| ScheduleError::invalid("schedule.type：无效频率"))?;
        let zone_name = data
            .get("timezone")
            .unwrap_or(&json!("Asia/Shanghai"))
            .as_str()
            .ok_or_else(|| ScheduleError::invalid("schedule.timezone：需要 IANA 时区"))?
            .to_owned();
        let zone = tz::db()
            .get(&zone_name)
            .map_err(|_| ScheduleError::invalid("schedule.timezone：时区不存在"))?;
        if zone.iana_name() != Some(zone_name.as_str()) {
            return Err(ScheduleError::invalid("schedule.timezone：时区不存在"));
        }
        let mut output = json!({"type":kind, "timezone":zone_name});
        let mut permitted = vec!["type", "timezone"];
        let mut times = Vec::new();
        if matches!(kind, "once" | "daily" | "weekly" | "monthly") {
            let requested = value["times"]
                .as_array()
                .filter(|times| (1..=24).contains(&times.len()))
                .ok_or_else(|| ScheduleError::invalid("schedule.times：需要 1–24 个 HH:mm 时间"))?;
            if kind == "once" && requested.len() != 1 {
                return Err(ScheduleError::invalid("单次计划只能设置一个时间"));
            }
            for value in requested {
                let text = value
                    .as_str()
                    .filter(|value| {
                        let bytes = value.as_bytes();
                        bytes.len() == 5
                            && bytes[2] == b':'
                            && bytes
                                .iter()
                                .enumerate()
                                .all(|(i, byte)| i == 2 || byte.is_ascii_digit())
                    })
                    .ok_or_else(|| {
                        ScheduleError::invalid("schedule.times：需要 1–24 个 HH:mm 时间")
                    })?;
                times.push(text.parse::<Time>().map_err(|_| {
                    ScheduleError::invalid("schedule.times：需要 1–24 个 HH:mm 时间")
                })?);
            }
            times.sort_unstable();
            times.dedup();
            output["times"] = json!(
                times
                    .iter()
                    .map(|time| format!("{:02}:{:02}", time.hour(), time.minute()))
                    .collect::<Vec<_>>()
            );
            permitted.push("times");
        }
        let frequency = match kind {
            "once" => {
                let error = || ScheduleError::invalid("schedule.date：需要 YYYY-MM-DD 日期");
                let date: Date = value["date"]
                    .as_str()
                    .filter(|text| text.len() == 10)
                    .ok_or_else(error)?
                    .parse()
                    .map_err(|_| error())?;
                if date.year() < 1 {
                    return Err(error());
                }
                output["date"] = json!(date.to_string());
                permitted.push("date");
                Frequency::Once(date)
            }
            "interval" => {
                let every = integer(&value["every"], 1, 525600, "schedule.every")?;
                let (unit, scale) = match value["unit"].as_str() {
                    Some("minutes") => ("minutes", 60),
                    Some("hours") => ("hours", 3600),
                    _ => {
                        return Err(ScheduleError::invalid(
                            "schedule.unit：只支持 minutes/hours",
                        ));
                    }
                };
                let prior_anchor = previous
                    .filter(|p| p["type"] == kind && p["every"] == every && p["unit"] == unit)
                    .and_then(|p| p["anchor"].as_str());
                let anchor = match data.get("anchor") {
                    None | Some(Value::Null) => {
                        prior_anchor.map(instant).transpose()?.unwrap_or(now)
                    }
                    Some(Value::String(text)) if text.is_empty() => {
                        prior_anchor.map(instant).transpose()?.unwrap_or(now)
                    }
                    Some(Value::String(text)) => instant(text)?,
                    _ => return Err(ScheduleError::invalid("时间点应为包含时区的 ISO 日期时间")),
                };
                let anchor =
                    Timestamp::from_nanosecond(anchor.as_nanosecond().div_euclid(1000) * 1000)
                        .map_err(|_| ScheduleError::invalid("schedule.anchor：时间超出范围"))?;
                output["every"] = json!(every);
                output["unit"] = json!(unit);
                output["anchor"] = json!(iso(anchor));
                permitted.extend(["every", "unit", "anchor"]);
                Frequency::Interval {
                    anchor,
                    step: i128::from(every) * scale * NANOS_PER_SECOND,
                }
            }
            "daily" => Frequency::Daily,
            "weekly" => {
                let days = value["days"]
                    .as_array()
                    .filter(|days| !days.is_empty())
                    .ok_or_else(|| ScheduleError::invalid("schedule.days：至少选择一天"))?;
                let days: BTreeSet<_> = days
                    .iter()
                    .map(|day| integer(day, 0, 6, "schedule.days").map(|day| day as u32))
                    .collect::<Result<_>>()?;
                output["days"] = json!(days);
                permitted.push("days");
                Frequency::Weekly(days)
            }
            "monthly" => {
                let day = if value["day"] == "last" {
                    None
                } else {
                    Some(integer(&value["day"], 1, 31, "schedule.day")? as u32)
                };
                output["day"] = day.map(|day| json!(day)).unwrap_or_else(|| json!("last"));
                permitted.push("day");
                Frequency::Monthly(day)
            }
            "cron" => {
                let expression = value["expression"]
                    .as_str()
                    .filter(|text| text.chars().count() <= 160)
                    .ok_or_else(|| {
                        ScheduleError::invalid("schedule.expression：需要五字段 cron")
                    })?;
                let cron = Cron::parse(expression)?;
                for &hour in &cron.fields[1] {
                    for &minute in &cron.fields[0] {
                        times.push(
                            Time::new(hour as i8, minute as i8, 0, 0).expect("validated cron time"),
                        );
                    }
                }
                output["expression"] =
                    json!(expression.split_whitespace().collect::<Vec<_>>().join(" "));
                permitted.push("expression");
                Frequency::Cron(cron)
            }
            _ => return Err(ScheduleError::invalid("schedule.type：无效频率")),
        };
        if data.keys().any(|key| !permitted.contains(&key.as_str())) {
            return Err(ScheduleError::invalid(
                "schedule：包含不适用于当前频率的字段",
            ));
        }
        Ok((
            Self {
                frequency,
                zone,
                times,
            },
            output,
        ))
    }

    pub(crate) fn next(&self, after: Timestamp) -> Option<Timestamp> {
        self.search(after, true)
    }

    pub(crate) fn previous(&self, before: Timestamp) -> Option<Timestamp> {
        self.search(before, false)
    }

    pub(crate) fn next_dates(&self, mut after: Timestamp) -> Vec<String> {
        std::iter::from_fn(|| {
            after = self.next(after)?;
            Some(iso(after))
        })
        .take(3)
        .collect()
    }

    fn search(&self, reference: Timestamp, forward: bool) -> Option<Timestamp> {
        let qualifies = |point: &Timestamp| (*point > reference) == forward;
        match self.frequency {
            Frequency::Interval { anchor, step } => {
                let anchor = anchor.as_nanosecond();
                let elapsed = (reference.as_nanosecond() - anchor).div_euclid(step);
                let index = if forward {
                    (elapsed + 1).max(1)
                } else {
                    elapsed
                };
                return (index >= 1)
                    .then(|| Timestamp::from_nanosecond(anchor + index * step).ok())
                    .flatten();
            }
            Frequency::Once(day) => return self.local(day, self.times[0]).filter(qualifies),
            _ => {}
        }
        let mut day = reference.to_zoned(self.zone.clone()).date();
        for _ in 0..=SEARCH_DAYS {
            if self.matches(day) {
                let iter = self
                    .times
                    .iter()
                    .map(|&time| self.local(day, time))
                    .flatten();
                if let Some(point) = if forward {
                    iter.filter(qualifies).next()
                } else {
                    iter.rev().find(qualifies)
                } {
                    return Some(point);
                }
            }
            day = if forward {
                day.tomorrow()
            } else {
                day.yesterday()
            }
            .ok()?;
        }
        None
    }

    fn local(&self, date: Date, time: Time) -> Option<Timestamp> {
        let wall = date.to_datetime(time);
        let zoned = self.zone.to_ambiguous_zoned(wall).earlier().ok()?;
        // The earlier resolution moves a nonexistent wall time out of the DST gap.
        (zoned.datetime() == wall).then(|| zoned.timestamp())
    }

    fn matches(&self, date: Date) -> bool {
        let weekday = date.weekday().to_sunday_zero_offset() as u32;
        match &self.frequency {
            Frequency::Weekly(days) => days.contains(&weekday),
            Frequency::Monthly(day) => {
                u32::from(date.day() as u8) == day.unwrap_or(date.last_of_month().day() as u32)
            }
            Frequency::Cron(cron) => cron.matches(date, weekday),
            _ => true,
        }
    }
}

#[derive(Debug)]
struct Cron {
    fields: [BTreeSet<u32>; 5],
    dom_wild: bool,
    dow_wild: bool,
}

impl Cron {
    fn parse(expression: &str) -> Result<Self> {
        let fields: Vec<_> = expression.split_whitespace().collect();
        let [minute, hour, dom, month, dow] = fields.as_slice() else {
            return Err(ScheduleError::invalid(
                "cron：需要标准五字段（分 时 日 月 周）",
            ));
        };
        Ok(Self {
            fields: [
                field(minute, 0, 59)?,
                field(hour, 0, 23)?,
                field(dom, 1, 31)?,
                field(month, 1, 12)?,
                field(dow, 0, 7)?.into_iter().map(|day| day % 7).collect(),
            ],
            dom_wild: dom.starts_with('*'),
            dow_wild: dow.starts_with('*'),
        })
    }

    fn matches(&self, date: Date, weekday: u32) -> bool {
        let dom = self.fields[2].contains(&(date.day() as u32));
        let dow = self.fields[4].contains(&weekday);
        self.fields[3].contains(&(date.month() as u32))
            && if self.dom_wild || self.dow_wild {
                dom && dow
            } else {
                dom || dow
            }
    }
}

fn field(text: &str, low: u32, high: u32) -> Result<BTreeSet<u32>> {
    let number = |text: &str| -> Option<u32> {
        text.bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| text.parse().ok())
            .flatten()
    };
    let mut result = BTreeSet::new();
    for part in text.split(',') {
        let (base, step) = part
            .split_once('/')
            .map_or((part, None), |(base, step)| (base, Some(step)));
        let stride = match step {
            None => 1,
            Some(step) => number(step)
                .filter(|&n| (1..=high - low + 1).contains(&n))
                .ok_or_else(|| ScheduleError::invalid("cron：步长超出范围或格式错误"))?,
        };
        let bounds = if base == "*" {
            Some((low, high))
        } else if let Some((start, end)) = base.split_once('-') {
            number(start).zip(number(end))
        } else {
            number(base).map(|start| (start, if step.is_some() { high } else { start }))
        };
        let (start, end) = bounds
            .ok_or_else(|| ScheduleError::invalid("cron：只支持数字、*、列表、范围和步长"))?;
        if start < low || end > high || start > end {
            return Err(ScheduleError::invalid("cron：字段数值超出范围"));
        }
        result.extend((start..=end).step_by(stride as usize));
    }
    Ok(result)
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/schedules_clock.rs"]
mod tests;
