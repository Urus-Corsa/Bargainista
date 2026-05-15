import anthropic
from langsmith.wrappers import wrap_anthropic

from app.core.config import settings


def get_anthropic_client() -> anthropic.AsyncAnthropic:
    """Return a LangSmith-traced AsyncAnthropic client.

    When LANGCHAIN_TRACING_V2=true, every messages.create() call is automatically
    sent to LangSmith with input tokens, output tokens, model, latency, and full
    message content. When tracing is disabled the client behaves identically to
    a plain AsyncAnthropic() — wrap_anthropic is a no-op in that case.
    """
    return wrap_anthropic(anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key))
