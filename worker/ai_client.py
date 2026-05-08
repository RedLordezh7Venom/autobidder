"""
ai_client.py — LLM client with Groq primary + OpenRouter fallback.

Pipeline:
  1. Intent Classification — is this a real freelance opportunity?
  2. Pain Point Extraction — what does the client need?
  3. Bid Drafting — personalized, human-sounding 2-3 sentence pitch.
"""
import asyncio
from pathlib import Path
from typing import Optional

import structlog
import yaml
from groq import AsyncGroq
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings
from database import get_config, get_skills

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Load prompts from YAML ─────────────────────────────────────────────────────
_PROMPTS_PATH = str(Path(__file__).parent / "prompts.yaml")
_prompts_cache: Optional[dict] = None


def _load_prompts() -> dict:
    global _prompts_cache
    if _prompts_cache is None:
        try:
            with open(_PROMPTS_PATH) as f:
                _prompts_cache = yaml.safe_load(f)
        except FileNotFoundError:
            _prompts_cache = {}
    return _prompts_cache


# ── Groq client ───────────────────────────────────────────────────────────────
def _get_groq_client() -> AsyncGroq:
    return AsyncGroq(api_key=settings.groq_api_key)


# ── OpenRouter fallback ───────────────────────────────────────────────────────
def _get_openrouter_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )


# ── Core LLM call with auto-fallback ─────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
async def _call_groq(system: str, user: str, temperature: float, model: str) -> str:
    client = _get_groq_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=512,
    )
    return resp.choices[0].message.content.strip()


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
async def _call_openrouter(system: str, user: str, temperature: float, model: str) -> str:
    client = _get_openrouter_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=512,
    )
    return resp.choices[0].message.content.strip()


async def _call_llm(system: str, user: str, temperature: float = 0.75) -> tuple[str, str]:
    """Call LLM with Groq primary and OpenRouter fallback. Returns (response, model_used)."""
    groq_model = await get_config("groq_model", "llama-3.3-70b-versatile")
    try:
        text = await _call_groq(system, user, temperature, groq_model)
        return text, f"groq/{groq_model}"
    except Exception as e:
        logger.warning("groq.failed_switching_to_openrouter", error=str(e))
        or_model = await get_config("openrouter_model", "anthropic/claude-3-haiku")
        text = await _call_openrouter(system, user, temperature, or_model)
        return text, f"openrouter/{or_model}"


# ── Pipeline steps ────────────────────────────────────────────────────────────

async def classify_intent(post_content: str) -> bool:
    """
    Step 1: Is this post a genuine freelance/developer opportunity?
    Returns True if yes, False otherwise.
    """
    system = (
        "You are an expert at classifying social media posts. "
        "Respond with ONLY 'YES' or 'NO'."
    )
    user = (
        "Is the following post a genuine request for a freelance developer, "
        "contract engineer, or technical service provider? "
        "It must be a real opportunity, not spam, self-promotion, or unrelated content.\n\n"
        f"POST:\n{post_content[:800]}"
    )
    try:
        response, _ = await _call_llm(system, user, temperature=0.1)
        return response.strip().upper().startswith("YES")
    except Exception as e:
        logger.error("intent_classification.failed", error=str(e))
        return False


async def extract_pain_point(post_content: str) -> str:
    """
    Step 2: What is the client's core problem or technical need?
    """
    system = (
        "You extract core technical pain points from job/hiring posts. "
        "Respond with ONE concise sentence (max 20 words) describing what the client needs."
    )
    user = f"POST:\n{post_content[:800]}"
    try:
        response, _ = await _call_llm(system, user, temperature=0.3)
        return response.strip()
    except Exception as e:
        logger.warning("pain_point.extraction_failed", error=str(e))
        return "build a custom software solution"


async def generate_bid(
    post_content: str,
    pain_point: str,
    platform: str,
) -> tuple[str, str, str]:
    """
    Step 3: Generate a personalized, human-sounding bid comment.
    Returns (bid_text, ai_prompt, model_used).
    """
    # Build skills context from DB
    skills = await get_skills()
    skills_by_cat: dict[str, list[str]] = {}
    for s in skills:
        skills_by_cat.setdefault(s["category"], []).append(s["skill"])

    skills_text = "\n".join(
        f"  • {cat.title()}: {', '.join(items)}"
        for cat, items in skills_by_cat.items()
    )

    prompts = _load_prompts()
    system_template = prompts.get("bid_generation", {}).get("system", _DEFAULT_SYSTEM)
    user_template = prompts.get("bid_generation", {}).get("user", _DEFAULT_USER)

    system_prompt = system_template
    user_prompt = user_template.format(
        platform=platform,
        post_content=post_content[:600],
        pain_point=pain_point,
        skills_text=skills_text,
    )

    temp = float(await get_config("ai_temperature", "0.75"))
    response, model_used = await _call_llm(system_prompt, user_prompt, temperature=temp)

    return response.strip(), user_prompt, model_used


# ── Default prompt templates (overridden by prompts.yaml) ────────────────────

_DEFAULT_SYSTEM = """\
You are a senior freelance developer writing a genuine, personalized reply to a hiring post.

RULES (CRITICAL — violating any will make your response useless):
1. Sound 100% human — like a real developer briefly scrolling their feed.
2. NEVER start with "I", "Hi there", "Hello", or "As a developer".
3. NO phrases: "I can help", "I am skilled", "As an AI", "I would be perfect".
4. Maximum 3 sentences. Each sentence must add concrete value.
5. Mention ONE specific technology from the job post context.
6. End with one brief, low-pressure call-to-action (e.g., "happy to share relevant work").
7. Conversational tone — not a cover letter, not a sales pitch.
"""

_DEFAULT_USER = """\
Platform: {platform}

Post content:
\"\"\"{post_content}\"\"\"

Core client need: {pain_point}

My available skills:
{skills_text}

Write the reply now. No preamble, no quotes, just the message text.
"""
