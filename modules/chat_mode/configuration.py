"""Conversation-local model settings, shared provider protocol restrictions."""
from config.limits import REASONING_EFFORT_LEVELS
from config.model_profiles import get_model_profile

EFFORTS = {None, *REASONING_EFFORT_LEVELS}


def configure_conversation(storage, conversation_id, settings):
    if set(settings) - {"model_key", "thinking_mode", "reasoning_effort", "chatgpt_fast_mode"}:
        raise ValueError("Unsupported Chat conversation setting")
    if "chatgpt_fast_mode" in settings:
        from utils.api_client.codex.service_tier import validate_fast_mode
        validate_fast_mode(settings["chatgpt_fast_mode"])
    if "thinking_mode" in settings and not isinstance(settings["thinking_mode"], bool):
        raise ValueError("Thinking mode must be a boolean")
    effort = settings.get("reasoning_effort")
    if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS):
        raise ValueError("Invalid reasoning effort")
    if "model_key" in settings and (not isinstance(settings["model_key"], str) or not settings["model_key"].strip()):
        raise ValueError("Invalid model identity")
    profile = get_model_profile(settings["model_key"]) if "model_key" in settings else None

    def mutate(document):
        metadata = document["metadata"]
        if settings.get("chatgpt_fast_mode") is True:
            selected = profile or get_model_profile(metadata.get("model_key")) or {}
            if selected.get("responses_auth") == "codex_oauth" and not selected.get("supports_chatgpt_fast_mode"):
                raise ValueError("Selected ChatGPT model does not support Fast mode")
        if profile and metadata.get("model_key") and document["messages"]:
            previous = get_model_profile(metadata["model_key"])
            if ((previous.get("api_protocol") == "responses") !=
                    (profile.get("api_protocol") == "responses")):
                raise RuntimeError("Cannot switch API protocol within a conversation")
        metadata.update(settings)
        if "thinking_mode" in settings:
            metadata["run_mode"] = "thinking" if settings["thinking_mode"] else "fast"
        return dict(settings)

    return storage.manager.mutate_conversation(conversation_id, mutate)[1]
