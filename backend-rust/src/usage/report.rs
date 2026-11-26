use std::{cmp::Reverse, collections::BTreeMap};

use jiff::{
    Span, Timestamp,
    civil::{Date, DateTime},
    tz::{Offset, TimeZone},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{
    money::{Usd, parse_amount},
    sources::Source,
    token_count,
};

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct ReportQuery {
    pub source: Option<String>,
    pub model_group: Option<String>,
    pub tz: Option<String>,
    /// Minutes east of UTC, serialized as a string in the query.
    pub tz_offset: Option<String>,
}

impl ReportQuery {
    fn time_zone(&self) -> (TimeZone, String) {
        if let Some(name) = self.tz.as_deref().map(str::trim)
            && (1..=3).contains(&name.split('/').count())
            && name.split('/').all(|part| {
                !part.is_empty()
                    && part.bytes().all(|byte| {
                        byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'+' | b'-')
                    })
            })
            && let Ok(zone) = TimeZone::get(name)
        {
            return (zone, name.to_owned());
        }
        let minutes = self
            .tz_offset
            .as_deref()
            .and_then(|offset| offset.trim().parse::<i128>().ok())
            .unwrap_or_default()
            .clamp(-14 * 60, 14 * 60) as i32;
        let sign = if minutes < 0 { '-' } else { '+' };
        let absolute = minutes.abs();
        let label = format!("UTC{sign}{:02}:{:02}", absolute / 60, absolute % 60);
        (
            Offset::from_seconds(minutes * 60)
                .expect("clamped offset")
                .to_time_zone(),
            label,
        )
    }
}

pub(super) type Buckets = BTreeMap<String, Bucket>;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(from = "BucketFields", into = "BucketFields")]
pub(super) struct Bucket {
    input: u64,
    cached: u64,
    output: u64,
    cost: Usd,
    requests: u64,
    priced: u64,
    unpriced: u64,
    unpriced_tokens: u64,
    speed_tokens: u64,
    speed_ms: u64,
    ttft_ms: u64,
    ttft_n: u64,
}

// Stable version-1 cache layout shared with modules/usage_stats/rollup.py.
type BucketFields = (u64, u64, u64, Usd, u64, u64, u64, u64, u64, u64, u64, u64);

impl From<BucketFields> for Bucket {
    fn from(
        (
            input,
            cached,
            output,
            cost,
            requests,
            priced,
            unpriced,
            unpriced_tokens,
            speed_tokens,
            speed_ms,
            ttft_ms,
            ttft_n,
        ): BucketFields,
    ) -> Self {
        Self {
            input,
            cached,
            output,
            cost,
            requests,
            priced,
            unpriced,
            unpriced_tokens,
            speed_tokens,
            speed_ms,
            ttft_ms,
            ttft_n,
        }
    }
}

impl From<Bucket> for BucketFields {
    fn from(b: Bucket) -> Self {
        (
            b.input,
            b.cached,
            b.output,
            b.cost,
            b.requests,
            b.priced,
            b.unpriced,
            b.unpriced_tokens,
            b.speed_tokens,
            b.speed_ms,
            b.ttft_ms,
            b.ttft_n,
        )
    }
}

impl Bucket {
    fn tokens(&self) -> u64 {
        self.input.saturating_add(self.output)
    }

    fn merge(&mut self, source: &Self) {
        self.input = self.input.saturating_add(source.input);
        self.cached = self.cached.saturating_add(source.cached.min(source.input));
        self.output = self.output.saturating_add(source.output);
        self.cost.add(&source.cost);
        self.requests = self.requests.saturating_add(source.requests);
        self.priced = self.priced.saturating_add(source.priced);
        self.unpriced = self.unpriced.saturating_add(source.unpriced);
        self.unpriced_tokens = self.unpriced_tokens.saturating_add(source.unpriced_tokens);
        self.speed_tokens = self.speed_tokens.saturating_add(source.speed_tokens);
        self.speed_ms = self.speed_ms.saturating_add(source.speed_ms);
        self.ttft_ms = self.ttft_ms.saturating_add(source.ttft_ms);
        self.ttft_n = self.ttft_n.saturating_add(source.ttft_n);
    }
}

pub(super) fn rollup(payload: &Value) -> Buckets {
    let mut buckets = Buckets::new();
    let Some(requests) = payload["requests"].as_object() else {
        return buckets;
    };
    for request in requests.values() {
        let Some(hour) = request["recorded_at"].as_str().and_then(hour_of) else {
            continue;
        };
        let actor_id = request["actor_id"].as_str().unwrap_or_default();
        let kind = text(&payload["actors"][actor_id]["kind"]).unwrap_or(if actor_id == "main" {
            "main"
        } else {
            "sub_agent"
        });
        let model = text(&request["price"]["model_key"]).unwrap_or("unknown");
        let bucket = buckets.entry(format!("{hour}|{model}|{kind}")).or_default();
        let input = token_count(&request["input_tokens"]).unwrap_or_default();
        let output = token_count(&request["output_tokens"]).unwrap_or_default();
        let cached = token_count(&request["cached_input_tokens"])
            .unwrap_or_default()
            .min(input);
        let price = (request["status"] == "priced")
            .then(|| parse_amount(&request["cost_usd"]))
            .flatten();
        let mut sample = Bucket {
            input,
            cached,
            output,
            requests: 1,
            ..Bucket::default()
        };
        match price {
            Some(price) => {
                sample.cost = price;
                sample.priced = 1;
            }
            None => {
                sample.unpriced = 1;
                sample.unpriced_tokens = sample.tokens();
            }
        }
        if kind == "main" {
            let (tokens, ms) = match request.get("speed_sample") {
                Some(value) => (
                    token_count(&value["output_tokens"]).unwrap_or_default(),
                    token_count(&value["duration_ms"]).unwrap_or_default(),
                ),
                None => (
                    output,
                    token_count(&request["duration_ms"]).unwrap_or_default(),
                ),
            };
            if tokens > 0 && ms > 0 {
                sample.speed_tokens = tokens;
                sample.speed_ms = ms;
            }
            if let Some(ttft) = request["ttft_ms"].as_u64() {
                sample.ttft_ms = ttft;
                sample.ttft_n = 1;
            }
        }
        bucket.merge(&sample);
    }
    buckets
}

fn text(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.is_empty())
}

fn hour_of(text: &str) -> Option<i64> {
    let timestamp = text.parse::<Timestamp>().ok().or_else(|| {
        text.parse::<DateTime>()
            .ok()?
            .to_zoned(TimeZone::UTC)
            .ok()
            .map(|zoned| zoned.timestamp())
    })?;
    Some((timestamp.as_second() - i64::from(timestamp.subsec_nanosecond() < 0)).div_euclid(3600))
}

#[derive(Default)]
struct Day {
    total: Bucket,
    models: BTreeMap<String, u64>,
}

#[derive(Default)]
struct Breakdown {
    models: BTreeMap<String, Bucket>,
    kinds: BTreeMap<String, u64>,
    modes: BTreeMap<String, u64>,
    sources: BTreeMap<String, u64>,
    punch: [[u64; 24]; 7],
}

fn add_tokens(values: &mut BTreeMap<String, u64>, key: &str, tokens: u64) {
    let total = values.entry(key.into()).or_default();
    *total = total.saturating_add(tokens);
}

impl Breakdown {
    fn finish(self) -> Value {
        let mut models: Vec<_> = self.models.into_iter().collect();
        models.sort_by_key(|(_, bucket)| Reverse(bucket.tokens()));
        let models: Vec<_> = models.into_iter().map(|(key, b)| json!({
            "model_key": key, "tokens": b.tokens(), "input_tokens": b.input, "cached_tokens": b.cached,
            "output_tokens": b.output, "cost_usd": b.cost, "requests": b.requests,
            "priced_requests": b.priced, "unpriced_requests": b.unpriced,
            "speed_tokens": b.speed_tokens, "speed_ms": b.speed_ms, "ttft_ms": b.ttft_ms, "ttft_n": b.ttft_n,
        })).collect();
        let mut sources: Vec<_> = self.sources.into_iter().collect();
        sources.sort_by_key(|(_, tokens)| Reverse(*tokens));
        let sources: Vec<_> = sources
            .into_iter()
            .map(|(key, tokens)| json!({"id": key, "tokens": tokens}))
            .collect();
        json!({"models": models, "kinds": self.kinds, "modes": self.modes, "sources": sources, "punch": self.punch})
    }
}

pub(super) fn build(
    sources: &[Source],
    query: &ReportQuery,
    now: Timestamp,
    exchange_rate: Value,
) -> Result<Value, &'static str> {
    let selected = query
        .source
        .as_deref()
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .unwrap_or("all");
    if selected != "all" && !sources.iter().any(|source| source.id == selected) {
        return Err("unknown source");
    }
    let group_by_provider = query.model_group.as_deref().map(str::trim) == Some("provider");
    let (zone, timezone) = query.time_zone();
    let today = now.to_zoned(zone.clone()).date();
    let cutoff = |days| {
        today
            .checked_sub(Span::new().days(days))
            .unwrap_or(Date::MIN)
    };
    let window_start = cutoff(370);
    let mut ranges = [
        ("7", cutoff(6), Breakdown::default()),
        ("30", cutoff(29), Breakdown::default()),
        ("all", Date::MIN, Breakdown::default()),
    ];
    let mut days = BTreeMap::<Date, Day>::new();
    let mut localized = BTreeMap::new();
    for source in sources
        .iter()
        .filter(|source| selected == "all" || selected == source.id)
    {
        for buckets in &source.conversations {
            for (key, bucket) in buckets {
                let mut parts = key.splitn(3, '|');
                let (Some(hour), Some(model), Some(kind)) = (
                    parts.next().and_then(|hour| hour.parse::<i64>().ok()),
                    parts.next(),
                    parts.next(),
                ) else {
                    continue;
                };
                let Some((date, weekday, hour)) = *localized.entry(hour).or_insert_with(|| {
                    let local = Timestamp::from_second(hour.checked_mul(3600)?)
                        .ok()?
                        .to_zoned(zone.clone());
                    Some((
                        local.date(),
                        local.weekday().to_monday_zero_offset() as usize,
                        local.hour() as usize,
                    ))
                }) else {
                    continue;
                };
                if date > today {
                    continue;
                }
                let model = if group_by_provider {
                    model
                } else {
                    model
                        .split_once('/')
                        .map(|(_, name)| name)
                        .filter(|name| !name.is_empty())
                        .unwrap_or(model)
                };
                let day = days.entry(date).or_default();
                let tokens = bucket.tokens();
                day.total.merge(bucket);
                add_tokens(&mut day.models, model, tokens);
                for (_, _, range) in ranges.iter_mut().filter(|(_, start, _)| date >= *start) {
                    range.models.entry(model.into()).or_default().merge(bucket);
                    add_tokens(&mut range.kinds, kind, tokens);
                    add_tokens(&mut range.modes, source.mode, tokens);
                    add_tokens(&mut range.sources, &source.id, tokens);
                    range.punch[weekday][hour] = range.punch[weekday][hour].saturating_add(tokens);
                }
            }
        }
    }
    let mut prior = Bucket::default();
    let mut rows = Vec::new();
    for (&day, summary) in &days {
        let b = &summary.total;
        if day < window_start {
            prior.merge(b);
            continue;
        }
        let top_model = summary
            .models
            .iter()
            .max_by_key(|&(name, tokens)| (*tokens, Reverse(name)))
            .map(|(model, _)| model);
        rows.push(json!({
            "date": day.to_string(), "input_tokens": b.input, "cached_tokens": b.cached,
            "output_tokens": b.output, "cost_usd": b.cost, "requests": b.requests,
            "unpriced_requests": b.unpriced, "unpriced_tokens": b.unpriced_tokens, "top_model": top_model,
        }));
    }
    let source_rows: Vec<_> = sources.iter().map(|source| json!({
        "id": source.id, "label": source.label, "mode": source.mode, "has_data": !source.conversations.is_empty(),
    })).collect();
    let ranges = ranges
        .into_iter()
        .map(|(name, _, range)| (name.into(), range.finish()))
        .collect::<serde_json::Map<_, _>>();
    Ok(json!({
        "today": today.to_string(), "window_start": window_start.to_string(),
        "first_date": days.keys().next().map(Date::to_string), "overview": overview(&days, today),
        "days": rows, "prior": {"tokens": prior.tokens(), "work_tokens": prior.input.saturating_sub(prior.cached).saturating_add(prior.output), "cost_usd": prior.cost, "requests": prior.requests},
        "ranges": ranges, "selected": selected, "model_group": if group_by_provider { "provider" } else { "name" },
        "timezone": timezone, "sources": source_rows, "exchange_rate": exchange_rate,
    }))
}

fn overview(days: &BTreeMap<Date, Day>, today: Date) -> Value {
    let mut total = Bucket::default();
    for day in days.values() {
        total.merge(&day.total);
    }
    let previous_day = |day: Date| day.checked_sub(Span::new().days(1)).ok();
    let active = |day: &Date| days.get(day).is_some_and(|day| day.total.requests > 0);
    let mut active_days = 0;
    let mut previous = None;
    let mut streak = 0;
    let mut longest = 0;
    let mut best = None;
    for (&day, row) in days.iter().filter(|(day, _)| active(day)) {
        active_days += 1;
        streak = if previous.is_some() && previous == previous_day(day) {
            streak + 1
        } else {
            1
        };
        longest = longest.max(streak);
        previous = Some(day);
        let tokens = row.total.tokens();
        if best.is_none_or(|(_, best_tokens)| tokens > best_tokens) {
            best = Some((day, tokens));
        }
    }
    let mut cursor = if active(&today) {
        Some(today)
    } else {
        previous_day(today)
    };
    let mut current = 0;
    while let Some(day) = cursor.filter(active) {
        current += 1;
        cursor = previous_day(day);
    }
    json!({
        "total_tokens": total.tokens(), "input_tokens": total.input, "cached_tokens": total.cached, "output_tokens": total.output,
        "cost_usd": total.cost, "requests": total.requests, "priced_requests": total.priced,
        "unpriced_requests": total.unpriced, "unpriced_tokens": total.unpriced_tokens,
        "active_days": active_days, "current_streak": current, "longest_streak": longest,
        "best_day": best.map(|(day, tokens)| json!({"date": day.to_string(), "tokens": tokens})),
    })
}
