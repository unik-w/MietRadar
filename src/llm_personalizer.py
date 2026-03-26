# -*- coding: utf-8 -*-
"""
llm_personalizer.py
===================
Uses Google Gemma 3 4B (Instruction-Tuned) via HuggingFace + PyTorch MPS
to generate ONE personalised paragraph for a WG rental application.

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



def _generate_personalisation_para(
    listing_description: str,
    max_new_tokens: int = 150,
) -> str | None:
    """
    Ask the LLM to generate ONLY the personalisation paragraph.
    Returns None on failure (caller falls back to plain template).
    """
    try:
        import torch
        model, tokenizer = _get_model_and_tokenizer()
    except Exception as e:
        logger.error(f"LLM load error: {e}")
        return None

    # Truncate description — distribute budget proportionally across sections.
    # Sections are delimited by "--- SectionName ---\n" markers (from submit_immo.py).
    # This ensures Lage/Sonstiges aren't cut if Objektbeschreibung is very long.
    max_desc_chars = int(os.getenv("LLM_MAX_DESC_CHARS", "1200"))
    if len(listing_description) > max_desc_chars:
        import re as _re
        # Split into labeled blocks: ["", "--- Objektbeschreibung ---\ntxt", "--- Lage ---\ntxt", ...]
        blocks = _re.split(r'(?=--- .+ ---)', listing_description.strip())
        blocks = [b.strip() for b in blocks if b.strip()]
        if len(blocks) > 1:
            # Equal budget per section, minimum 100 chars each
            per_section = max(100, max_desc_chars // len(blocks))
            truncated = []
            for block in blocks:
                if len(block) > per_section:
                    # Keep header intact, truncate body
                    lines = block.split("\n", 1)
                    header = lines[0]
                    body = lines[1] if len(lines) > 1 else ""
                    body_limit = per_section - len(header) - 1
                    truncated.append(header + "\n" + body[:body_limit] + "…" if body_limit > 0 else header)
                else:
                    truncated.append(block)
            listing_description = "\n\n".join(truncated)
        else:
            # No sections — just head-truncate as before
            listing_description = listing_description[:max_desc_chars] + "\n[...]"

    user_msg = (
        f"MIETANZEIGE BESCHREIBUNG:\n{listing_description}\n\n"
        f"Schreibe jetzt den persönlichen Absatz (3–4 Sätze, max. 80 Wörter):"
    )

    messages = [
        {"role": "user", "content": f"{_get_system_prompt()}\n\n{user_msg}"},
    ]

    try:
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
        result = tokenizer.decode(generated, skip_special_tokens=True).strip()

        # Strip any accidental markdown fences
        if result.startswith("```"):
            result = "\n".join(
                l for l in result.splitlines() if not l.strip().startswith("```")
            ).strip()

        # Sanity check — must be at least 20 words
        if len(result.split()) < 10:
            logger.warning(f"LLM output too short: {repr(result)}")
            return None

        logger.info("✅ Personalisation paragraph generated.")
        return result

    except Exception as e:
        logger.error(f"LLM inference error: {e}")
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
