//! Strict YAML document loading and export for the API.

use std::{collections::HashSet, fmt, mem::MaybeUninit};

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value, json};
use serde_yaml_ng::Value as Yaml;
use unsafe_libyaml::{
    YAML_ALIAS_EVENT, YAML_STREAM_END_EVENT, yaml_event_delete, yaml_event_t, yaml_parser_delete,
    yaml_parser_initialize, yaml_parser_parse, yaml_parser_set_input_string, yaml_parser_t,
};

use super::{Result, ScheduleError};

pub(super) fn parse(text: &str) -> Result<Value> {
    if text.trim_start().starts_with('{') {
        if let Ok(value) = parse_json(text.as_bytes()) {
            return Ok(value);
        }
    }
    reject_aliases(text)?;
    let value = serde_yaml_ng::from_str::<Yaml>(text)
        .map_err(|error| ScheduleError::invalid(format!("YAML 格式错误：{error}")))?;
    if !value.is_mapping() {
        return Err(ScheduleError::invalid("计划配置需要对象"));
    }
    to_json(value)
}

pub(super) fn dump(value: &Value) -> Result<String> {
    fn mapping(value: &Map<String, Value>, indent: usize, output: &mut String) {
        for (key, value) in value {
            output.push_str(&" ".repeat(indent));
            output.push_str(key);
            output.push(':');
            if let Some(nested) = value.as_object().filter(|nested| !nested.is_empty()) {
                output.push('\n');
                mapping(nested, indent + 2, output);
            } else {
                // JSON string escapes and flow arrays are valid YAML. Quoting every string
                // also preserves ISO dates, numeric-looking text and YAML 1.1 scalars.
                output.push(' ');
                output.push_str(&value.to_string());
                output.push('\n');
            }
        }
    }
    let mut result = String::new();
    mapping(
        value
            .as_object()
            .ok_or_else(|| ScheduleError::storage("Invalid stored schedule definition"))?,
        0,
        &mut result,
    );
    Ok(result)
}

pub(super) fn parse_json(bytes: &[u8]) -> Result<Value> {
    let invalid = |error| ScheduleError::invalid(format!("请求必须是 JSON 对象：{error}"));
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    StrictJson::deserialize(&mut deserializer).map_err(invalid)?;
    deserializer.end().map_err(invalid)?;
    let value = serde_json::from_slice(bytes).map_err(invalid)?;
    if !finite(&value) {
        return Err(ScheduleError::invalid("配置数字必须有限"));
    }
    Ok(value)
}

fn finite(value: &Value) -> bool {
    match value {
        Value::Number(number) => number.as_f64().is_some_and(f64::is_finite),
        Value::Array(items) => items.iter().all(finite),
        Value::Object(fields) => fields.values().all(finite),
        _ => true,
    }
}

struct StrictJson;

impl<'de> Deserialize<'de> for StrictJson {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        deserializer.deserialize_any(Self)
    }
}

impl<'de> Visitor<'de> for StrictJson {
    type Value = Self;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value without duplicate keys")
    }

    fn visit_bool<E: de::Error>(self, _: bool) -> std::result::Result<Self, E> {
        Ok(self)
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> std::result::Result<Self, E> {
        Ok(self)
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> std::result::Result<Self, E> {
        Ok(self)
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> std::result::Result<Self, E> {
        Ok(self)
    }
    fn visit_str<E: de::Error>(self, _: &str) -> std::result::Result<Self, E> {
        Ok(self)
    }
    fn visit_unit<E: de::Error>(self) -> std::result::Result<Self, E> {
        Ok(self)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut values: A) -> std::result::Result<Self, A::Error> {
        while values.next_element::<Self>()?.is_some() {}
        Ok(self)
    }

    fn visit_map<A: MapAccess<'de>>(self, mut values: A) -> std::result::Result<Self, A::Error> {
        let mut keys = HashSet::new();
        while let Some(key) = values.next_key::<String>()? {
            if !keys.insert(key) {
                return Err(de::Error::custom("配置存在重复字段"));
            }
            values.next_value::<Self>()?;
        }
        Ok(self)
    }
}

fn to_json(value: Yaml) -> Result<Value> {
    Ok(match value {
        Yaml::Null => Value::Null,
        Yaml::Bool(value) => json!(value),
        Yaml::String(value) => json!(value),
        Yaml::Number(value) => {
            if let Some(number) = value.as_i64() {
                json!(number)
            } else if let Some(number) = value.as_u64() {
                json!(number)
            } else {
                let number = value
                    .as_f64()
                    .and_then(Number::from_f64)
                    .ok_or_else(|| ScheduleError::invalid("配置数字必须有限"))?;
                Value::Number(number)
            }
        }
        Yaml::Sequence(values) => {
            Value::Array(values.into_iter().map(to_json).collect::<Result<_>>()?)
        }
        Yaml::Mapping(values) => {
            let mut map = Map::new();
            for (key, value) in values {
                let Yaml::String(key) = key else {
                    return Err(ScheduleError::invalid("配置存在重复或非字符串字段"));
                };
                map.insert(key, to_json(value)?);
            }
            Value::Object(map)
        }
        Yaml::Tagged(_) => return Err(ScheduleError::invalid("配置含有不支持的 YAML 标签")),
    })
}

// serde_yaml resolves aliases as it builds values. Reject them from parser events first,
// where a literal asterisk inside a quoted, plain, or block scalar remains ordinary text.
fn reject_aliases(text: &str) -> Result<()> {
    let mut parser = MaybeUninit::<yaml_parser_t>::uninit();
    let parser = parser.as_mut_ptr();
    // SAFETY: The parser has aligned backing storage for this entire call. The input bytes
    // remain borrowed and valid until deletion. Successful parse calls initialize one event,
    // which is inspected and deleted once. Parser initialization and cleanup are paired.
    unsafe {
        if !yaml_parser_initialize(parser).ok {
            return Err(ScheduleError::invalid("YAML 解析器初始化失败"));
        }
        yaml_parser_set_input_string(parser, text.as_ptr(), text.len() as u64);
        let outcome = loop {
            let mut event = MaybeUninit::<yaml_event_t>::uninit();
            let event = event.as_mut_ptr();
            if !yaml_parser_parse(parser, event).ok {
                break Err(ScheduleError::invalid("YAML 格式错误"));
            }
            let kind = (*event).type_;
            yaml_event_delete(event);
            if kind == YAML_ALIAS_EVENT {
                break Err(ScheduleError::invalid("配置不支持 YAML 引用"));
            }
            if kind == YAML_STREAM_END_EVENT {
                break Ok(());
            }
        };
        yaml_parser_delete(parser);
        outcome
    }
}
