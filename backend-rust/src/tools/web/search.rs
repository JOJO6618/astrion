use reqwest::{Client, RequestBuilder};
use serde_json::{Map, Value, json};
use time::{Duration, OffsetDateTime};

use super::{SEARCH, citation, client, credential, http_url, matches_domain, response_json, text};

pub(super) async fn run(args: &Value, prefs: &Value) -> Result<Value, String> {
    let query = text(args, "query")
        .filter(|query| query.len() <= 16_384)
        .ok_or("搜索查询不能为空或过长")?;
    let provider = text(prefs, "search_provider")
        .map(str::to_ascii_lowercase)
        .filter(|provider| {
            matches!(
                provider.as_str(),
                "tavily" | "bocha" | "exa" | "parallel" | "searxng"
            )
        })
        .unwrap_or_else(|| "tavily".into());
    let key =
        credential(prefs, &provider).ok_or_else(|| format!("尚未配置 {provider} 搜索服务凭证"))?;
    let mut filters = parameters(args)?;
    let limit = match provider.as_str() {
        "tavily" | "parallel" => 20,
        "exa" => 100,
        _ => 50,
    };
    let limit = args["max_results"].as_i64().unwrap_or(10).clamp(1, limit) as usize;
    let request = request(&client()?, &provider, query, &filters, limit, &key)?;
    let body = response_json(request).await?;
    if !body["error"].is_null()
        || (provider == "bocha" && body["code"].as_u64().is_some_and(|code| code != 200))
    {
        return Err(format!("{provider} 搜索服务拒绝了请求"));
    }
    let rows = if provider == "bocha" {
        let data = body.get("data").unwrap_or(&body);
        &data["webPages"]["value"]
    } else {
        &body["results"]
    };
    let mut results = Vec::new();
    let mut citations = Vec::new();
    for item in rows
        .as_array()
        .ok_or_else(|| format!("{provider} 搜索服务返回无效结果"))?
    {
        let Some(url) = text(item, "url").and_then(|value| http_url(value).ok()) else {
            continue;
        };
        if provider == "searxng"
            && let Some(domains) = filters["include_domains"].as_array()
            && !domains.is_empty()
            && !domains.iter().filter_map(Value::as_str).any(|domain| {
                url.host_str()
                    .is_some_and(|host| matches_domain(host, domain))
            })
        {
            continue;
        }
        let title = text(item, if provider == "bocha" { "name" } else { "title" })
            .unwrap_or_else(|| url.as_str());
        let content = match provider.as_str() {
            "exa" => text(item, "text").unwrap_or_default().into(),
            "parallel" => item["excerpts"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join("\n"),
            "bocha" => text(item, "summary")
                .or_else(|| text(item, "snippet"))
                .unwrap_or_default()
                .into(),
            _ => String::from(text(item, "content").unwrap_or_default()),
        };
        let date = [
            "published_date",
            "publishedDate",
            "publish_date",
            "datePublished",
        ]
        .into_iter()
        .find_map(|key| text(item, key))
        .unwrap_or_default();
        let annotation = citation(&url, title, &content, date, SEARCH);
        results.push(json!({
            "index":results.len()+1,"title":title,"url":url.as_str(),"domain":url.host_str(),"content":content,
            "score":item["score"].as_f64().unwrap_or_default(),"published_date":date,"citation_id":annotation["id"],
        }));
        citations.push(annotation);
        if results.len() == limit {
            break;
        }
    }
    let ignored = dropped(&provider, &filters);
    filters["provider"] = json!(provider);
    if !ignored.is_empty() {
        filters["provider_dropped"] = json!(ignored);
    }
    let timestamp = jiff::Timestamp::now().to_string();
    let mut summary = format!("🔍 搜索查询: {query}\n📅 搜索时间: {timestamp}");
    for row in &results {
        let snippet: String = text(row, "content")
            .unwrap_or_default()
            .chars()
            .take(200)
            .collect();
        summary.push_str(&format!(
            "\n{}. [{}] {}\n{}\n{snippet}",
            row["index"],
            text(row, "citation_id").unwrap_or_default(),
            text(row, "title").unwrap_or_default(),
            text(row, "url").unwrap_or_default()
        ));
    }
    if results.is_empty() {
        summary.push_str("\n未找到相关结果");
    }
    Ok(
        json!({"success":true,"query":query,"summary":summary,"results":results,
        "total_results":results.len(),"filters":filters,"timestamp":timestamp,"citations":citations,
        "answer":body["answer"].as_str().unwrap_or_default()}),
    )
}

fn parameters(args: &Value) -> Result<Value, String> {
    let topic = text(args, "topic")
        .unwrap_or("general")
        .to_ascii_lowercase();
    if !matches!(topic.as_str(), "general" | "news" | "finance") {
        return Err("无效的搜索类型".into());
    }
    let mut filters = json!({"topic":topic});
    let relative = text(args, "time_range");
    let days = args.get("days").filter(|value| !value.is_null());
    let dates = (text(args, "start_date"), text(args, "end_date"));
    if usize::from(relative.is_some())
        + usize::from(days.is_some())
        + usize::from(dates.0.is_some() || dates.1.is_some())
        > 1
    {
        return Err("time_range、days 与日期区间只能选一项".into());
    }
    if let Some(range) = relative {
        let canonical = match range.to_ascii_lowercase().as_str() {
            "d" | "day" => "day",
            "w" | "week" => "week",
            "m" | "month" => "month",
            "y" | "year" => "year",
            _ => return Err("无效的搜索时间范围".into()),
        };
        filters["time_range"] = json!(canonical);
    }
    if let Some(days) = days {
        let number = days
            .as_i64()
            .or_else(|| days.as_str()?.trim().parse().ok())
            .filter(|day| (1..=100_000).contains(day))
            .ok_or("days 必须为正整数")?;
        if topic != "news" {
            return Err("days 仅用于新闻搜索".into());
        }
        filters["days"] = json!(number);
    }
    match dates {
        (Some(start), Some(end)) => {
            let parsed_start = start
                .parse::<jiff::civil::Date>()
                .map_err(|_| "无效的开始日期")?;
            let parsed_end = end
                .parse::<jiff::civil::Date>()
                .map_err(|_| "无效的结束日期")?;
            if parsed_start > parsed_end {
                return Err("开始日期晚于结束日期".into());
            }
            filters["start_date"] = json!(start);
            filters["end_date"] = json!(end);
        }
        (None, None) => (),
        _ => return Err("日期区间需要开始和结束日期".into()),
    }
    if let Some(country) = text(args, "country") {
        if topic != "general" {
            return Err("country 仅用于普通搜索".into());
        }
        filters["country"] = json!(country.to_ascii_lowercase());
    }
    if let Some(domains) = args["include_domains"].as_array() {
        if domains.len() > 300 {
            return Err("include_domains 最多包含 300 个域名".into());
        }
        let normalized: Vec<_> = domains
            .iter()
            .filter_map(Value::as_str)
            .map(|value| value.trim().to_ascii_lowercase())
            .filter(|value| !value.is_empty())
            .collect();
        if !normalized.is_empty() {
            filters["include_domains"] = json!(normalized);
        }
    }
    Ok(filters)
}

fn dropped(provider: &str, filters: &Value) -> Vec<&'static str> {
    let topic = text(filters, "topic").unwrap_or_default();
    let mut list = Vec::new();
    if (topic == "news" && matches!(provider, "bocha" | "parallel"))
        || (topic == "finance" && !matches!(provider, "tavily" | "exa"))
    {
        list.push("topic");
    }
    if !filters["country"].is_null() && provider != "tavily" {
        list.push("country");
    }
    if !filters["start_date"].is_null() && provider == "searxng" {
        list.push("start_date");
    }
    if !filters["end_date"].is_null() && provider == "parallel" {
        list.push("end_date");
    }
    list
}

fn after(filters: &Value) -> Option<String> {
    let days = match text(filters, "time_range") {
        Some("day") => Some(1),
        Some("week") => Some(7),
        Some("month") => Some(30),
        Some("year") => Some(365),
        _ => filters["days"].as_i64(),
    };
    text(filters, "start_date").map(str::to_owned).or_else(|| {
        let date = OffsetDateTime::now_utc()
            .date()
            .checked_sub(Duration::days(days?))?;
        Some(date.to_string())
    })
}

fn request(
    client: &Client,
    provider: &str,
    query: &str,
    filters: &Value,
    max: usize,
    key: &str,
) -> Result<RequestBuilder, String> {
    let topic = text(filters, "topic").unwrap_or("general");
    let end = text(filters, "end_date");
    let domains = &filters["include_domains"];
    Ok(match provider {
        "searxng" => {
            let mut url = http_url(key)?;
            url.path_segments_mut()
                .map_err(|_| "无效的 SearXNG 地址")?
                .pop_if_empty()
                .push("search");
            let mut search = vec![("q", query), ("format", "json"), ("safesearch", "1")];
            if topic == "news" {
                search.push(("categories", "news"));
            }
            let period = text(filters, "time_range").or_else(|| {
                filters["days"].as_i64().map(|days| match days {
                    1 => "day",
                    2..=7 => "week",
                    8..=31 => "month",
                    _ => "year",
                })
            });
            if let Some(period) = period {
                search.push(("time_range", period));
            }
            client.get(url).query(&search)
        }
        "bocha" => {
            let freshness = match text(filters, "time_range") {
                Some("day") => "oneDay".into(),
                Some("week") => "oneWeek".into(),
                Some("month") => "oneMonth".into(),
                Some("year") => "oneYear".into(),
                _ => after(filters)
                    .map(|start| {
                        format!(
                            "{start}..{}",
                            end.map(str::to_owned)
                                .unwrap_or_else(|| OffsetDateTime::now_utc().date().to_string())
                        )
                    })
                    .unwrap_or_else(|| "noLimit".into()),
            };
            let mut body = json!({"query":query,"count":max,"summary":true,"freshness":freshness});
            if let Some(domains) = domains.as_array() {
                body["include"] = json!(
                    domains
                        .iter()
                        .filter_map(Value::as_str)
                        .take(100)
                        .collect::<Vec<_>>()
                        .join("|")
                );
            }
            client
                .post("https://api.bochaai.com/v1/web-search")
                .bearer_auth(key)
                .json(&body)
        }
        "exa" => {
            let mut body = json!({"query":query,"type":"auto","numResults":max,"contents":{"text":{"maxCharacters":800}}});
            if topic == "news" {
                body["category"] = json!("news");
            }
            if topic == "finance" {
                body["category"] = json!("financial report");
            }
            if let Some(start) = after(filters) {
                body["startPublishedDate"] = json!(format!("{start}T00:00:00.000Z"));
            }
            if let Some(end) = end {
                body["endPublishedDate"] = json!(format!("{end}T23:59:59.000Z"));
            }
            if !domains.is_null() {
                body["includeDomains"] = domains.clone();
            }
            client
                .post("https://api.exa.ai/search")
                .header("x-api-key", key)
                .json(&body)
        }
        "parallel" => {
            let mut policy = Map::new();
            if let Some(start) = after(filters) {
                policy.insert("after_date".into(), json!(start));
            }
            if let Some(domains) = domains.as_array() {
                policy.insert(
                    "include_domains".into(),
                    json!(domains.iter().take(200).collect::<Vec<_>>()),
                );
            }
            let mut body =
                json!({"objective":query,"search_queries":[query],"max_results":max,"mode":"fast"});
            if !policy.is_empty() {
                body["advanced_settings"] = json!({"source_policy":policy});
            }
            client
                .post("https://api.parallel.ai/v1/search")
                .header("x-api-key", key)
                .json(&body)
        }
        _ => {
            let mut body = filters.clone();
            body["query"] = json!(query);
            body["max_results"] = json!(max);
            body["search_depth"] = json!("advanced");
            for key in ["include_answer", "include_images", "include_raw_content"] {
                body[key] = json!(false);
            }
            client
                .post("https://api.tavily.com/search")
                .bearer_auth(key)
                .json(&body)
        }
    })
}
