import os

from .schema import Extraction, JurisdictionLine, Party

_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                    "ANTHROPIC_FEDERATION_RULE_ID")


def ai_available() -> bool:
    mode = os.environ.get("CERTAPP_EXTRACTOR", "auto")
    if mode == "demo":
        return False
    return mode == "claude" or any(os.environ.get(v) for v in _CREDENTIAL_VARS)


def default_extractor():
    """Claude when credentials are configured, otherwise the offline demo extractor.

    Set CERTAPP_EXTRACTOR=claude to force Claude (e.g. when credentials come
    from an `ant auth login` profile), or =demo to force offline mode.
    """
    if ai_available():
        from .claude import ClaudeExtractor
        return ClaudeExtractor()
    from .demo import DemoExtractor
    return DemoExtractor()


__all__ = ["Extraction", "JurisdictionLine", "Party", "ai_available", "default_extractor"]
