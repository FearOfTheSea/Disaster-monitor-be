from typing import Optional
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel
from app.core.config import settings

_gemini_client: Optional[AsyncOpenAI] = None
_deepseek_client: Optional[AsyncOpenAI] = None
_ollama_client: Optional[AsyncOpenAI] = None

def get_gemini_client() -> AsyncOpenAI:
    global _gemini_client
    if _gemini_client is None:
        api_key = settings.GEMINI_API_KEY or "not-configured"
        base_url = settings.GEMINI_BASE_URL
        _gemini_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return _gemini_client


def get_deepseek_client() -> AsyncOpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        api_key = settings.DEEPSEEK_API_KEY or "not-configured"
        base_url = settings.DEEPSEEK_BASE_URL
        _deepseek_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return _deepseek_client


def get_ollama_client() -> AsyncOpenAI:
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = AsyncOpenAI(
            base_url=settings.OLLAMA_BASE_URL,
            api_key=settings.OLLAMA_API_KEY,
        )
    return _ollama_client


def get_gemini_model(model_name: str) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(model=model_name, openai_client=get_gemini_client())


def get_deepseek_model(model_name: str) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(model=model_name, openai_client=get_deepseek_client())


def get_ollama_model(model_name: Optional[str] = None) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(
        model=model_name or settings.OLLAMA_MODEL,
        openai_client=get_ollama_client(),
    )


def get_agent_model() -> OpenAIChatCompletionsModel:
    provider = settings.LLM_PROVIDER.lower()
    model_name = settings.LLM_MODEL or None
    if provider == "ollama":
        return get_ollama_model(model_name)
    if provider == "deepseek":
        return get_deepseek_model(model_name or settings.DEEPSEEK_MODEL)
    if provider == "gemini":
        return get_gemini_model(model_name or settings.GEMINI_MODEL)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")
