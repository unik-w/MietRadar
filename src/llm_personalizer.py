# -*- coding: utf-8 -*-
"""
llm_personalizer.py
===================
Generates ONE personalised paragraph for a WG rental application.

Supports multiple LLM backends, selected via LLM_PROVIDER in config/.env:
  - "gemma_local" (default) — local Google Gemma 3 4B-IT via HuggingFace +
    PyTorch (MPS/CUDA/CPU). No API key or network calls needed at runtime
    (besides the one-time model download).
  - "gemini"  — Google Gemini API (Developer API key OR Vertex AI project).
  - "openai"  — OpenAI API (e.g. GPT-5 nano).

Architecture (clean separation of concerns):
  ┌────────────────────────────────────────────────────────┐
  │  Fixed parts  →  kept verbatim from message.txt       │
  │  LLM output  →  1 short paragraph about the listing   │
  │  Closing     →  kept verbatim from message.txt        │
  └────────────────────────────────────────────────────────┘

The LLM never sees or rewrites User's personal info — it only generates
a single bridge paragraph that connects User's interests to the listing.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

# Enable MPS fallback for any ops not yet implemented in Apple Metal.
# Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

HF_MODEL_ID = "google/gemma-3-4b-it"   # 4B instruction-tuned

_model = None      # type: ignore
_tokenizer = None  # type: ignore

# Lazily-created API clients for the remote providers (created on first use).
_gemini_client = None  # type: ignore
_openai_client = None  # type: ignore


def _get_provider() -> str:
    """
    Which backend to use for `_generate_personalisation_para()`.

    Read lazily (not at import time) so that callers who call `load_dotenv()`
    *after* importing this module still get the right value.

    Options (set LLM_PROVIDER in config/.env):
      - "gemma_local" (default) — local Gemma 3 4B-IT via HF + PyTorch/MPS
      - "gemini"                — Google Gemini API (Developer API or Vertex AI)
      - "openai"                — OpenAI API (e.g. GPT-5 nano)
    """
    return os.getenv("LLM_PROVIDER", "gemma_local").strip().lower()


def _detect_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except ImportError:
        return "cpu"


def _get_model_and_tokenizer():
    global _model, _tokenizer
    if _model is not None and _tokenizer is not None:
        return _model, _tokenizer

    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
    except ImportError:
        raise ImportError(
            "Required packages missing. Run:\n"
            "  pip install transformers torch accelerate"
        )

    device = _detect_device()
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or None

    print(f"🧠 Loading Gemma 3 4B-IT on device={device}…")
    print("   (First run downloads ~9 GB — please wait.)")

    _tokenizer = AutoTokenizer.from_pretrained(HF_MODEL_ID, token=hf_token)
    _model = AutoModelForCausalLM.from_pretrained(
        HF_MODEL_ID,
        token=hf_token,
        dtype=torch.bfloat16 if device in ("mps", "cuda") else torch.float32,
        device_map="auto" if device == "cuda" else None,
    )

    if device in ("mps", "cpu") and _model.device.type != device:
        _model = _model.to(device)

    _model.eval()
    print(f"✅ Gemma 3 4B-IT ready on {_model.device}.")
    return _model, _tokenizer


# ---------------------------------------------------------------------------
# Listing type detection → drives dynamic closing line
# ---------------------------------------------------------------------------

def _detect_listing_type(listing_url: str, listing_description: str) -> str:
    """
    Returns 'wg', 'zimmer', or 'wohnung'.
    """
    url_lower = listing_url.lower()
    if "/wohnungen-" in url_lower or "/1-zimmer-wohnungen-" in url_lower:
        return "wohnung"
    if "/wg-zimmer-" in url_lower:
        # Check if it's a private room within a WG or a standalone room
        desc_lower = listing_description.lower()
        if "wg" in desc_lower or "wohngemeinschaft" in desc_lower or "mitbewohner" in desc_lower:
            return "wg"
        return "zimmer"
    return "wg"  # default



# ---------------------------------------------------------------------------
# LLM: generate only the personalisation paragraph
# ---------------------------------------------------------------------------

def _get_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "config" / "llm_persona.txt"
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Could not load {prompt_path}: {e}")
        return "Du schreibst einen Absatz für eine WG-Bewerbung basierend auf der Anzeige."


# ---------------------------------------------------------------------------
# Backend: local Gemma (unchanged) — HuggingFace + PyTorch (MPS/CUDA/CPU)
# ---------------------------------------------------------------------------

def _generate_via_gemma_local(system_prompt: str, user_msg: str, max_new_tokens: int) -> str:
    import torch
    model, tokenizer = _get_model_and_tokenizer()

    messages = [
        {"role": "user", "content": f"{system_prompt}\n\n{user_msg}"},
    ]

    device = model.device
    encoded = tokenizer.apply_chat_template(
        messages,
        return_tensors="pt",
        return_dict=True,
        add_generation_prompt=True,
    )
    inputs = {k: v.to(device) for k, v in encoded.items()}
    input_len = inputs["input_ids"].shape[-1]

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
        )

    generated = outputs[0][input_len:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


# ---------------------------------------------------------------------------
# Backend: Google Gemini (Developer API or Vertex AI) via the `google-genai` SDK
# ---------------------------------------------------------------------------

def _get_gemini_client():
    """
    Create (and cache) a `google.genai.Client`.

    The SDK auto-configures itself from environment variables — set these in
    config/.env:

      Gemini Developer API (simple API key):
        GEMINI_API_KEY=...            (or GOOGLE_API_KEY)

      Vertex AI (uses your Google Cloud project/billing — e.g. free credits):
        GOOGLE_GENAI_USE_VERTEXAI=true
        GOOGLE_CLOUD_PROJECT=your-gcp-project-id
        GOOGLE_CLOUD_LOCATION=us-central1
        (auth via `gcloud auth application-default login`, or a service
        account key referenced by GOOGLE_APPLICATION_CREDENTIALS)
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "Required package missing. Run:\n"
            "  pip install google-genai"
        )

    _gemini_client = genai.Client()
    return _gemini_client


def _generate_via_gemini(system_prompt: str, user_msg: str, max_new_tokens: int) -> str:
    from google.genai import types

    client = _get_gemini_client()
    model_id = os.getenv("GEMINI_MODEL", "gemini-3-flash")

    # Gemini 3.x replaces the legacy thinking_budget param with thinking_level
    # (MINIMAL/LOW/MEDIUM/HIGH) — passing both in the same request is a 400 error,
    # and thinking_budget=0 is a Gemini 2.5-era concept. Pick the right one per model.
    if model_id.startswith("gemini-3"):
        level = types.ThinkingLevel.MINIMAL if "flash" in model_id else types.ThinkingLevel.LOW
        thinking_config = types.ThinkingConfig(thinking_level=level)
    else:
        thinking_config = types.ThinkingConfig(thinking_budget=0)

    response = client.models.generate_content(
        model=model_id,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_new_tokens,
            temperature=1.0,
            # Gemini 2.5+ "thinking" models spend part of max_output_tokens on
            # internal reasoning by default, which can eat the whole budget
            # for a short task like this and leave no visible text. This is a
            # simple single-paragraph task, so keep thinking minimal/disabled.
            # (Not all models allow budget=0 — e.g. gemini-2.5-pro requires >0 —
            # so fall back to the default if the model rejects it.)
            thinking_config=thinking_config,
        ),
    )
    if response.candidates and response.candidates[0].finish_reason == "MAX_TOKENS" and not (response.text or "").strip():
        # Some models don't support thinking_budget=0/MINIMAL or still burned the
        # budget on thoughts — retry once without capping thinking tokens.
        response = client.models.generate_content(
            model=model_id,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_new_tokens * 4,
                temperature=1.0,
            ),
        )
    return (response.text or "").strip()


# ---------------------------------------------------------------------------
# Backend: OpenAI (e.g. GPT-5 nano) via the `openai` SDK
# ---------------------------------------------------------------------------

def _get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "Required package missing. Run:\n"
            "  pip install openai"
        )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in config/.env")

    _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def _generate_via_openai(system_prompt: str, user_msg: str, max_new_tokens: int) -> str:
    client = _get_openai_client()
    model_id = os.getenv("OPENAI_MODEL", "gpt-5-nano")

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        max_completion_tokens=max_new_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def _truncate_description(listing_description: str, max_desc_chars: int) -> str:
    """
    Truncate description — distribute budget proportionally across sections.
    Sections are delimited by "--- SectionName ---\\n" markers (from submit_immo.py).
    This ensures Lage/Sonstiges aren't cut if Objektbeschreibung is very long.
    """
    if len(listing_description) <= max_desc_chars:
        return listing_description

    import re as _re
    # Split into labeled blocks: ["", "--- Objektbeschreibung ---\ntxt", "--- Lage ---\ntxt", ...]
    blocks = _re.split(r'(?=--- .+ ---)', listing_description.strip())
    blocks = [b.strip() for b in blocks if b.strip()]
    if len(blocks) <= 1:
        # No sections — just head-truncate as before
        return listing_description[:max_desc_chars] + "\n[...]"

    # Dynamic budget redistribution:
    # 1. Give each section its equal share.
    # 2. Sections that are shorter than their share return the surplus.
    # 3. Surplus is redistributed proportionally to sections that need more.
    equal_share = max(100, max_desc_chars // len(blocks))

    # First pass: determine which blocks fit within equal_share and collect surplus
    surplus = 0
    needs_more: list[int] = []   # indices of blocks that exceed their share
    allotments: list[int] = [0] * len(blocks)
    for i, block in enumerate(blocks):
        if len(block) <= equal_share:
            allotments[i] = len(block)          # fits as-is
            surplus += equal_share - len(block)  # unused budget
        else:
            allotments[i] = equal_share          # placeholder; will grow
            needs_more.append(i)

    # Second pass: distribute surplus to over-budget sections
    # proportional to how much they exceed equal_share
    if surplus > 0 and needs_more:
        excess = [len(blocks[i]) - equal_share for i in needs_more]
        total_excess = sum(excess)
        for idx, i in enumerate(needs_more):
            extra = int(surplus * excess[idx] / total_excess)
            allotments[i] = equal_share + extra

    # Build truncated blocks
    truncated = []
    for i, block in enumerate(blocks):
        limit = allotments[i]
        if len(block) <= limit:
            truncated.append(block)
        else:
            lines = block.split("\n", 1)
            header = lines[0]
            body = lines[1] if len(lines) > 1 else ""
            body_limit = limit - len(header) - 1
            if body_limit > 0:
                truncated.append(header + "\n" + body[:body_limit] + "…")
            else:
                truncated.append(header)
    return "\n\n".join(truncated)


def _generate_personalisation_para(
    listing_description: str,
    max_new_tokens: int = 150,
) -> str | None:
    """
    Ask the LLM to generate ONLY the personalisation paragraph.
    Returns None on failure (caller falls back to plain template).

    Backend is chosen via LLM_PROVIDER in config/.env — see `_get_provider()`.
    """
    provider = _get_provider()

    max_desc_chars = int(os.getenv("LLM_MAX_DESC_CHARS", "1200"))
    listing_description = _truncate_description(listing_description, max_desc_chars)

    user_msg = (
        f"MIETANZEIGE BESCHREIBUNG:\n{listing_description}\n\n"
        f"Schreibe jetzt den persönlichen Absatz (3–4 Sätze, max. 80 Wörter):"
    )
    system_prompt = _get_system_prompt()

    max_retries = 4
    result = ""
    try:
        for attempt in range(max_retries):
            try:
                if provider == "gemini":
                    result = _generate_via_gemini(system_prompt, user_msg, max_new_tokens)
                elif provider == "openai":
                    result = _generate_via_openai(system_prompt, user_msg, max_new_tokens)
                elif provider == "gemma_local":
                    result = _generate_via_gemma_local(system_prompt, user_msg, max_new_tokens)
                else:
                    logger.error(
                        f"Unknown LLM_PROVIDER={provider!r}. "
                        "Expected one of: gemma_local, gemini, openai."
                    )
                    return None
                break  # success
            except Exception as e:
                is_rate_limit = any(
                    marker in str(e) for marker in ("429", "RESOURCE_EXHAUSTED", "rate_limit", "RateLimit")
                )
                if is_rate_limit and attempt < max_retries - 1:
                    wait_s = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        f"Rate limited by {provider} (attempt {attempt + 1}/{max_retries}) — "
                        f"retrying in {wait_s}s…"
                    )
                    time.sleep(wait_s)
                    continue
                raise

        # Strip any accidental markdown fences
        if result.startswith("```"):
            result = "\n".join(
                l for l in result.splitlines() if not l.strip().startswith("```")
            ).strip()

        # Sanity check — must be at least 20 words
        if len(result.split()) < 10:
            logger.warning(f"LLM output too short: {repr(result)}")
            return None

        logger.info(f"✅ Personalisation paragraph generated via '{provider}'.")
        return result

    except Exception as e:
        logger.error(f"LLM inference error ({provider}): {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def personalise_message(
    template: str,
    listing_description: str,
    poster_name: str = "",
    listing_url: str = "",
) -> str:
    """
    Build a personalised application message.

    Structure of the output:
      1. Greeting/Content              ← from template (with real name if substituted)
      2. LLM personalisation para      ← inserted dynamically in place of {LLM_TEXT}
      
    Parameters
    ----------
    template : str
        Content of message.txt (may contain {name}).
    listing_description : str
        Full description text scraped from the WG listing.
    poster_name : str
        Name of the person who posted the ad.
    listing_url : str
        URL of the listing (used for type detection).

    Returns
    -------
    str
        The assembled, personalised message.
    """
    # --- Substitute name -------------------------------------------------
    if poster_name:
        filled_template = template.replace("{name}", poster_name)
    else:
        filled_template = template.replace(" {name}", "").replace("{name}", "")

    # --- Detect listing type (still used for logging) ----------------------
    listing_type = _detect_listing_type(listing_url, listing_description)
    logger.info(f"Listing type detected: {listing_type}")

    # --- Generate personalisation paragraph -----------------------------
    llm_para = None
    if listing_description.strip():
        llm_para = _generate_personalisation_para(listing_description)

    # Assemble final message via direct placeholder injection
    if "{LLM_TEXT}" in filled_template:
        if llm_para:
            filled_template = filled_template.replace("{LLM_TEXT}", llm_para)
        else:
            # If generation failed, perfectly remove the placeholder
            filled_template = filled_template.replace("{LLM_TEXT}", "")
            # Clean up any resulting triple newlines/awkward gaps left behind
            import re
            filled_template = re.sub(r'\n{3,}', '\n\n', filled_template)
            
    return filled_template.strip()


# ---------------------------------------------------------------------------
# CLI — python llm_personalizer.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    here = Path(__file__).parent.parent
    tmpl_path = here / "config" / "message.txt"
    if not tmpl_path.exists():
        print(f"Error: {tmpl_path} not found.")
        sys.exit(1)

    template = tmpl_path.read_text(encoding="utf-8")

    demo_desc = (
        "Wir sind zwei Berufstätige (26, 28) und suchen einen ruhigen Mitbewohner. "
        "Wir gehen gerne zusammen joggen und kochen ab und zu gemeinsam. "
        "Die WG liegt direkt an der U-Bahn, 10 Minuten vom Englischen Garten."
    )

    print("=== Original template ===")
    print(template)
    print("\n=== Personalised output ===\n")
    msg = personalise_message(template, demo_desc, poster_name="Tom",
                              listing_url="https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.123.html")
    print(msg)
