"""把单个对话账本解析为按 UTC 小时分桶的用量汇总（纯函数，不做 IO）。

账本（modules/conversation_costs.py）是唯一权威：这里只做派生汇总，可随时丢弃重建。
桶键为 ``"<UTC 小时序号>|<model_key>|<actor kind>"``，值为定长数值列表，字段见 FIELDS。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

# 列表下标即字段含义；cost_usd 以字符串保存 Decimal，避免浮点累计误差
FIELDS = (
    "input_tokens",      # 0 全部输入（含缓存命中）
    "cached_tokens",     # 1 缓存命中输入
    "output_tokens",     # 2 输出
    "cost_usd",          # 3 已计价金额（str Decimal）
    "requests",          # 4 请求数
    "priced",            # 5 已计价请求数
    "unpriced",          # 6 未计价请求数
    "unpriced_tokens",   # 7 未计价请求的输入+输出 token
    "speed_tokens",      # 8 速度样本输出 token（仅主智能体）
    "speed_ms",          # 9 速度样本生成毫秒
    "ttft_ms",           # 10 首包等待毫秒累计（仅主智能体）
    "ttft_n",            # 11 首包样本数
)
COST = 3
UNKNOWN_MODEL = "unknown"


def empty_bucket() -> List[Any]:
    return [0, 0, 0, "0", 0, 0, 0, 0, 0, 0, 0, 0]


def merge_bucket(target: List[Any], source: List[Any]) -> None:
    for idx, value in enumerate(source):
        if idx == COST:
            target[idx] = str(Decimal(target[idx]) + Decimal(value))
        else:
            target[idx] += value


def _hour_of(raw: Any) -> Optional[int]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:  # 账本写入恒为 UTC；缺时区按 UTC 处理
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() // 3600)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _speed_sample(record: Dict[str, Any], output: int) -> Optional[tuple]:
    """与工作段 tokens/s 同口径：Responses 用独立样本（空对象=跳过），Chat 用生成时长。"""
    if "speed_sample" in record:
        sample = record.get("speed_sample")
        if not isinstance(sample, dict):
            return None
        tokens, ms = _int(sample.get("output_tokens")), _int(sample.get("duration_ms"))
        return (tokens, ms) if tokens > 0 and ms > 0 else None
    ms = _int(record.get("duration_ms"))
    return (output, ms) if output > 0 and ms > 0 else None


def rollup_ledger(payload: Any) -> Dict[str, List[Any]]:
    """账本 JSON → {桶键: 数值列表}。无法识别的请求跳过，不猜测。"""
    if not isinstance(payload, dict):
        return {}
    actors = payload.get("actors") if isinstance(payload.get("actors"), dict) else {}
    requests = payload.get("requests") if isinstance(payload.get("requests"), dict) else {}
    buckets: Dict[str, List[Any]] = {}
    for record in requests.values():
        if not isinstance(record, dict):
            continue
        hour = _hour_of(record.get("recorded_at"))
        if hour is None:
            continue
        actor = actors.get(record.get("actor_id")) or {}
        kind = str(actor.get("kind") or ("main" if record.get("actor_id") == "main" else "sub_agent"))
        price = record.get("price") if isinstance(record.get("price"), dict) else {}
        model = str(price.get("model_key") or UNKNOWN_MODEL)
        bucket = buckets.setdefault(f"{hour}|{model}|{kind}", empty_bucket())

        input_tokens = _int(record.get("input_tokens"))
        cached = min(input_tokens, _int(record.get("cached_input_tokens")))
        output = _int(record.get("output_tokens"))
        bucket[0] += input_tokens
        bucket[1] += cached
        bucket[2] += output
        bucket[4] += 1
        cost = record.get("cost_usd")
        priced = record.get("status") == "priced" and cost is not None
        if priced:
            try:
                bucket[COST] = str(Decimal(bucket[COST]) + Decimal(str(cost)))
            except (InvalidOperation, ValueError):
                priced = False
        if priced:
            bucket[5] += 1
        else:
            bucket[6] += 1
            bucket[7] += input_tokens + output
        if kind == "main":
            sample = _speed_sample(record, output)
            if sample:
                bucket[8] += sample[0]
                bucket[9] += sample[1]
            ttft = record.get("ttft_ms")
            if isinstance(ttft, int) and not isinstance(ttft, bool) and ttft >= 0:
                bucket[10] += ttft
                bucket[11] += 1
    return buckets
