import os
from typing import Optional
from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

load_dotenv(override=True)

_gemini_client: Optional[AsyncOpenAI] = None
_deepseek_client: Optional[AsyncOpenAI] = None


def get_gemini_client() -> AsyncOpenAI:
    global _gemini_client
    if _gemini_client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        base_url = os.getenv("GEMINI_BASE_URL")
        _gemini_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return _gemini_client


def get_deepseek_client() -> AsyncOpenAI:
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = os.getenv("DEEPSEEK_BASE_URL")
        _deepseek_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return _deepseek_client


def get_gemini_model(model_name: str) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(model=model_name, openai_client=get_gemini_client())


def get_deepseek_model(model_name: str) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(model=model_name, openai_client=get_deepseek_client())
