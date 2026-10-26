"""Chat tools reuse provider services while enforcing account and network scope."""
from __future__ import annotations

import asyncio
import json

from modules.chat_mode.definitions import NAMES
from modules.chat_mode.settings import CHAT_FIELDS, SHARED_FIELDS
from modules.citations import get_registry
from modules.personalization_manager import load_personalization_config, save_personalization_config
from modules.search_engine import SearchEngine
from modules.webpage_extractor import extract_single_url, resolve_direct_extract_config, resolve_extract_provider_config


class ChatTools:
    def __init__(self, runtime):
        self.runtime = runtime

    def network_enabled(self):
        runtime = self.runtime
        return runtime.storage.read(runtime.conversation_id)["metadata"]["network_enabled"]

    async def execute(self, name, arguments):
        if name not in NAMES:
            return json.dumps({"success": False, "error": "Tool unavailable in Chat"})
        if name in {"web_search", "extract_webpage"} and not self.network_enabled():
            return json.dumps({"success": False, "code": "chat_network_disabled",
                               "error": "联网已关闭"}, ensure_ascii=False)
        args = {k: v for k, v in arguments.items() if k != "intent" and v is not None}
        try:
            value = await self._dispatch(name, args)
            return json.dumps(value, ensure_ascii=False)
        except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    async def _dispatch(self, name, args):
        runtime = self.runtime
        memory = runtime.memory_manager
        if name == "web_search":
            allowed, quota = runtime.record_search_call()
            if not allowed:
                return {"success": False, "code": "quota_exceeded", "quota": quota}
            results = await SearchEngine(data_dir=runtime.preferences_dir).search(**args)
            registry = get_registry(runtime)
            for result in results.get("results", []):
                annotation = registry.register_url(
                    title=result.get("title", ""), url=result.get("url", ""),
                    snippet=result.get("content", ""), source_tool=name,
                )
                result["citation_id"] = annotation.get("id")
            return results
        if name == "extract_webpage":
            prefs = load_personalization_config(runtime.preferences_dir)
            result = await extract_single_url(
                args["url"], extract_config=resolve_extract_provider_config(prefs),
                direct_config=resolve_direct_extract_config(prefs),
            )
            if len(result.get("content", "")) > 80000:
                return {"success": False, "error": "网页超过80000字符，请指定更精确的资料"}
            if result.get("success"):
                annotation = get_registry(runtime).register_url(
                    url=args["url"], snippet=result.get("content", ""), source_tool=name,
                )
                result["citation_id"] = annotation.get("id")
            return result
        if name == "update_memory":
            return await asyncio.to_thread(memory.update, **args)
        if name == "search_chat_memory":
            return {"results": await asyncio.to_thread(memory.search, **args)}
        if name == "read_chat_memory":
            return await asyncio.to_thread(memory.read_topic, **args)
        if name == "update_chat_memory":
            return await asyncio.to_thread(memory.write_topic, **args)
        if name == "search_chat_history":
            return {"results": await asyncio.to_thread(
                runtime.storage.search, args["query"], args.get("max_results", 20),
            )}
        if name == "read_chat_history":
            document = runtime.storage.project(args["conversation_id"])
            return {"title": document["title"], "messages": [
                {"role": m["role"], "content": m.get("content", "")}
                for m in document["messages"]
                if m["role"] in {"user", "assistant"}
                and m.get("metadata", {}).get("visibility") != "hidden"
            ]}
        if name == "manage_personalization":
            allowed = SHARED_FIELDS | CHAT_FIELDS
            if args["action"] == "read":
                prefs = load_personalization_config(runtime.preferences_dir)
                return {k: v for k, v in prefs.items() if k in allowed}
            if args["action"] != "update" or args.get("field") not in allowed:
                raise ValueError("This preference is unavailable in Chat")
            prefs = save_personalization_config(
                runtime.preferences_dir, {args["field"]: args.get("value")},
            )
            return {"success": True, "field": args["field"], "value": prefs[args["field"]]}
        raise ValueError("Unknown Chat tool")
