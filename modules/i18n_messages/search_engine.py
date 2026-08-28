"""Backend i18n message pack: web search engine user-visible messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time.
"""

MESSAGES = {
    "search_engine.api_key_not_configured": {
        "zh-CN": "Tavily API密钥未配置",
        "en-US": "Tavily API key is not configured",
    },
    "search_engine.api_request_failed": {
        "zh-CN": "API请求失败: {status_code}",
        "en-US": "API request failed: {status_code}",
    },
    "search_engine.search_timeout": {
        "zh-CN": "搜索超时",
        "en-US": "Search timed out",
    },
    "search_engine.search_failed": {
        "zh-CN": "搜索失败: {error}",
        "en-US": "Search failed: {error}",
    },
    "search_engine.unknown_error": {
        "zh-CN": "未知错误",
        "en-US": "Unknown error",
    },
    "search_engine.no_relevant_info": {
        "zh-CN": "未找到相关信息",
        "en-US": "No relevant information found",
    },
    "search_engine.invalid_topic": {
        "zh-CN": "无效的topic: {topic}. 可选值: {valid}",
        "en-US": "Invalid topic: {topic}. Valid values: {valid}",
    },
    "search_engine.time_params_mutually_exclusive": {
        "zh-CN": "时间参数只能三选一：time_range、days、start_date+end_date 不能同时使用",
        "en-US": "Time parameters are mutually exclusive: time_range, days and start_date+end_date cannot be combined",
    },
    "search_engine.days_must_be_positive_int": {
        "zh-CN": "days 必须是正整数，当前值: {days}",
        "en-US": "days must be a positive integer, current value: {days}",
    },
    "search_engine.days_must_be_greater_than_zero": {
        "zh-CN": "days 必须大于0，当前值: {days}",
        "en-US": "days must be greater than 0, current value: {days}",
    },
    "search_engine.days_only_for_news": {
        "zh-CN": "days 参数仅在 topic=\"news\" 时可用，请调整 topic 或改用其他时间参数",
        "en-US": "days is only available with topic=\"news\"; adjust topic or use another time parameter",
    },
    "search_engine.invalid_time_range": {
        "zh-CN": "无效的time_range: {time_range}. 可选值: day/week/month/year 或缩写 d/w/m/y",
        "en-US": "Invalid time_range: {time_range}. Valid values: day/week/month/year or short forms d/w/m/y",
    },
    "search_engine.date_range_requires_both": {
        "zh-CN": "start_date 与 end_date 必须同时提供且格式为 YYYY-MM-DD",
        "en-US": "start_date and end_date must both be provided and formatted as YYYY-MM-DD",
    },
    "search_engine.start_date_invalid_format": {
        "zh-CN": "start_date 格式无效: {start_date}，请使用 YYYY-MM-DD",
        "en-US": "Invalid start_date format: {start_date}; use YYYY-MM-DD",
    },
    "search_engine.end_date_invalid_format": {
        "zh-CN": "end_date 格式无效: {end_date}，请使用 YYYY-MM-DD",
        "en-US": "Invalid end_date format: {end_date}; use YYYY-MM-DD",
    },
    "search_engine.invalid_calendar_date": {
        "zh-CN": "start_date 或 end_date 含无效日期，请检查是否为有效的公历日期",
        "en-US": "start_date or end_date contains an invalid date; check that it is a valid calendar date",
    },
    "search_engine.start_date_after_end_date": {
        "zh-CN": "start_date ({start_date}) 不能晚于 end_date ({end_date})",
        "en-US": "start_date ({start_date}) cannot be later than end_date ({end_date})",
    },
    "search_engine.country_only_for_general": {
        "zh-CN": "country 参数仅在 topic=\"general\" 时可用，请调整 topic 或移除 country",
        "en-US": "country is only available with topic=\"general\"; adjust topic or remove country",
    },
    "search_engine.include_domains_must_be_array": {
        "zh-CN": "include_domains 必须是字符串数组",
        "en-US": "include_domains must be an array of strings",
    },
    "search_engine.include_domains_item_must_be_string": {
        "zh-CN": "include_domains 中每一项都必须是字符串",
        "en-US": "Each item in include_domains must be a string",
    },
    "search_engine.include_domains_too_many": {
        "zh-CN": "include_domains 最多支持300个域名，当前: {count}",
        "en-US": "include_domains supports at most 300 domains, current: {count}",
    },
}