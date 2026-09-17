"""
OpenRouter API Client with Structured Outputs and Resilient Retries.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional, Type, TypeVar
import requests
from pydantic import BaseModel, ValidationError

from src.config import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    GEMINI_API_KEY,
    LLM_PROVIDER,
    MAX_RETRIES,
    OPEN_ROUTER_API_KEY,
    OPENAI_API_KEY,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenRouterClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        max_retries: int = MAX_RETRIES,
        timeout: float = REQUEST_TIMEOUT,
        provider: Optional[str] = None,
    ):
        self.provider = (provider or LLM_PROVIDER).lower()
        if self.provider == "openai":
            self.api_key = api_key or OPENAI_API_KEY
            chosen_default = default_model or (DEFAULT_MODEL if ("gpt" in str(DEFAULT_MODEL).lower() or "o1" in str(DEFAULT_MODEL).lower() or "o3" in str(DEFAULT_MODEL).lower()) else "gpt-4o-mini")
            chosen_fallback = fallback_model or (FALLBACK_MODEL if ("gpt" in str(FALLBACK_MODEL).lower() or "o1" in str(FALLBACK_MODEL).lower() or "o3" in str(FALLBACK_MODEL).lower()) else "gpt-4o")
            self.default_model = self._clean_model(chosen_default)
            self.fallback_model = self._clean_model(chosen_fallback)
            self.url = "https://api.openai.com/v1/chat/completions"
            if not self.api_key:
                logger.warning("OPENAI_API_KEY is not set. OpenAI API calls will fail.")
        elif self.provider == "gemini":
            self.api_key = api_key or GEMINI_API_KEY
            chosen_default = default_model or (DEFAULT_MODEL if "gemini" in str(DEFAULT_MODEL).lower() else "gemini-2.5-flash")
            chosen_fallback = fallback_model or (FALLBACK_MODEL if "gemini" in str(FALLBACK_MODEL).lower() else "gemini-2.5-pro")
            self.default_model = self._clean_model(chosen_default)
            self.fallback_model = self._clean_model(chosen_fallback)
            self.url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            if not self.api_key:
                logger.warning("GEMINI_API_KEY is not set. Gemini API calls will fail.")
        else:  # openrouter
            self.api_key = api_key or OPEN_ROUTER_API_KEY
            chosen_default = default_model or (DEFAULT_MODEL if not str(DEFAULT_MODEL).startswith("gemini") and "gpt" not in str(DEFAULT_MODEL).lower() else "nvidia/nemotron-3-super-120b-a12b:free")
            chosen_fallback = fallback_model or (FALLBACK_MODEL if not str(FALLBACK_MODEL).startswith("gemini") and "gpt" not in str(FALLBACK_MODEL).lower() else "nvidia/nemotron-3-ultra-550b-a55b:free")
            self.default_model = self._clean_model(chosen_default)
            self.fallback_model = self._clean_model(chosen_fallback)
            self.url = "https://openrouter.ai/api/v1/chat/completions"
            if not self.api_key:
                logger.warning("OPEN_ROUTER_API_KEY is not set. OpenRouter API calls will fail.")

        self.max_retries = max_retries
        self.timeout = timeout

    def _clean_model(self, model: str) -> str:
        if self.provider == "gemini":
            if model.startswith("google/"):
                return model[len("google/"):]
            if model.startswith("models/"):
                return model[len("models/"):]
        elif self.provider == "openai":
            if model.startswith("openai/"):
                return model[len("openai/"):]
        elif self.provider == "openrouter":
            if model.startswith("gemini-"):
                return f"google/{model}"
            elif model.startswith("gpt-") or model.startswith("o1-") or model.startswith("o3-"):
                return f"openai/{model}"
        return model

    def _get_headers(self) -> dict[str, str]:
        if self.provider in ("gemini", "openai"):
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/statements-extraction",
            "X-Title": "IDX Statements Extraction Pipeline",
            "Content-Type": "application/json",
        }

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.0,
        response_format: Optional[dict[str, Any]] = None,
        max_tokens: int = 32768,
    ) -> tuple[str, dict[str, Any]]:
        """
        Execute chat completion with automatic retries and model fallback.
        Returns (content_str, usage_dict).
        """
        chosen_model = self._clean_model(model or self.default_model)
        attempt = 0
        backoff = 2.0

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
        }
        # OpenAI reasoning / next-gen models (o1, o3, gpt-5) do not support custom temperature
        is_reasoning_model = any(k in chosen_model.lower() for k in ("o1", "o3", "gpt-5"))
        if not (self.provider == "openai" and is_reasoning_model):
            payload["temperature"] = temperature

        if self.provider == "openai":
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens

        if response_format:
            payload["response_format"] = response_format

        while attempt < self.max_retries:
            try:
                logger.debug(f"Sending request to {chosen_model} via {self.provider} (attempt {attempt + 1})")
                resp = requests.post(
                    self.url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data or "choices" not in data or not data.get("choices"):
                        err_detail = data.get("error", data)
                        logger.warning(
                            f"LLM API returned 200 with error/empty choices ({self.provider}): {err_detail}. Retrying in {backoff}s..."
                        )
                        time.sleep(backoff)
                        backoff *= 2
                        attempt += 1
                        continue

                    choice = data["choices"][0]
                    content = choice["message"]["content"]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason == "length":
                        logger.warning(f"Response truncated due to max_tokens={max_tokens} limit!")
                    usage = data.get("usage", {})
                    return content, usage

                # Fail fast on explicit authentication / authorization errors
                if resp.status_code in (401, 403):
                    err_msg = f"LLM API authentication or credit error ({self.provider}) {resp.status_code}: {resp.text}"
                    logger.error(err_msg)
                    raise RuntimeError(err_msg)

                # Rate limiting or temporary server error
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait_time = max(backoff, 5.0) if resp.status_code == 429 else backoff
                    logger.warning(
                        f"LLM API error ({self.provider}) {resp.status_code}: {resp.text[:300]}. Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                    backoff *= 2
                    attempt += 1
                    continue

                # Non-retriable error for current model -> try fallback model
                fallback_clean = self._clean_model(self.fallback_model)
                if attempt == 0 and chosen_model != fallback_clean:
                    logger.warning(f"Error {resp.status_code} with {chosen_model}: {resp.text[:300]}. Trying fallback {fallback_clean}")
                    payload["model"] = fallback_clean
                    chosen_model = fallback_clean
                    attempt += 1
                    continue

                logger.warning(f"LLM API error ({self.provider}) {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                logger.warning(f"Request exception: {e}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
                attempt += 1

        raise RuntimeError(f"LLM API failed after {self.max_retries} attempts with model {chosen_model} on provider {self.provider}.")

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        response_model: Type[T],
        model: Optional[str] = None,
        max_tokens: int = 32768,
    ) -> tuple[T, dict[str, Any]]:
        """
        Generate structured output validated against a Pydantic model.
        Uses JSON mode and schema guidance in system message.
        """
        schema = response_model.model_json_schema()
        schema_str = json.dumps(schema, indent=2)

        augmented_messages = list(messages)
        # Ensure system prompt commands JSON schema adherence
        system_instructions = (
            f"You MUST output valid, parseable JSON strictly matching this JSON Schema:\n"
            f"```json\n{schema_str}\n```\n"
            "Output ONLY the JSON object. Do not include markdown code block backticks if possible, "
            "and do not include any commentary before or after the JSON."
        )

        if augmented_messages and augmented_messages[0]["role"] == "system":
            augmented_messages[0] = {
                "role": "system",
                "content": augmented_messages[0]["content"] + "\n\n" + system_instructions,
            }
        else:
            augmented_messages.insert(0, {"role": "system", "content": system_instructions})

        raw_content, usage = self.chat_completion(
            messages=augmented_messages,
            model=model,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

        # Clean any accidental code block wrappers
        cleaned = raw_content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            parsed = response_model.model_validate_json(cleaned)
            return parsed, usage
        except ValidationError as val_err:
            logger.warning(f"Validation failed on structured output: {val_err}. Retrying with error correction...")
            # Try 1 repair call
            repair_messages = list(augmented_messages)
            repair_messages.append({"role": "assistant", "content": raw_content})
            repair_messages.append({
                "role": "user",
                "content": f"The output failed validation against the schema with error: {val_err}. "
                           f"Fix the JSON so it strictly satisfies the schema."
            })
            repaired_content, repair_usage = self.chat_completion(
                messages=repair_messages,
                model=model or self.default_model,
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            )
            repaired_clean = repaired_content.strip()
            if repaired_clean.startswith("```json"):
                repaired_clean = repaired_clean[7:]
            if repaired_clean.endswith("```"):
                repaired_clean = repaired_clean[:-3]
            parsed = response_model.model_validate_json(repaired_clean.strip())
            return parsed, repair_usage


# Convenience alias for provider-agnostic usage
LLMClient = OpenRouterClient

