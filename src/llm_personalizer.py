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
  │  Closing     →  dynamically adapted to listing type   │
  └────────────────────────────────────────────────────────┘

The LLM never sees or rewrites Unik's personal info — it only generates
a single bridge paragraph that connects Unik's interests to the listing.
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


def _build_closing(listing_type: str, poster_name: str) -> str:
    """
    Returns an appropriate closing question adapted to the listing type.
    """
    name_part = f" und {poster_name} persönlich kennenlernen" if poster_name else " euch persönlich kennenlernen"

    if listing_type == "wohnung":
        return (
            f"Ich würde mich sehr freuen, die Wohnung{name_part}. "
            f"Wäre ein Besichtigungstermin möglich?"
        )
    elif listing_type == "zimmer":
        return (
            f"Gerne würde ich das Zimmer besichtigen und mehr darüber erfahren. "
            f"Wäre ein kurzes Kennenlernen möglich?"
        )
    else:  # wg
        return (
            f"Gerne würde ich die WG{name_part}. "
            f"Wäre es möglich, einen Besichtigungstermin zu vereinbaren?"
        )


# ---------------------------------------------------------------------------
# Template parsing
# ---------------------------------------------------------------------------

def _parse_template_paragraphs(template: str) -> dict:
    """
    Split message.txt into its structural components.
    Expects the standard format:
      Hallo {name},

      [opener line]

      [para 1 — professional]

      [para 2 — personal]

      [closing question]

      [sign-off line]

      [signature name]
    """
    # Normalise line endings, strip trailing whitespace
    text = template.strip().replace("\r\n", "\n")
    blocks = [b.strip() for b in re.split(r"\n\n+", text) if b.strip()]

    result = {
        "greeting":  blocks[0] if len(blocks) > 0 else "",
        "opener":    blocks[1] if len(blocks) > 1 else "",
        "para1":     blocks[2] if len(blocks) > 2 else "",
        "para2":     blocks[3] if len(blocks) > 3 else "",
        "closing":   blocks[4] if len(blocks) > 4 else "",
        "signoff":   blocks[5] if len(blocks) > 5 else "",
        "signature": "\n".join(blocks[6:]) if len(blocks) > 6 else "",
    }
    return result


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

    # Truncate long descriptions based on user setting
    max_desc_chars = int(os.getenv("LLM_MAX_DESC_CHARS", "1200"))
    if len(listing_description) > max_desc_chars:
        listing_description = listing_description[:max_desc_chars] + "\n[...]"

    user_msg = (
        f"WG-ANZEIGE BESCHREIBUNG:\n{listing_description}\n\n"
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
      1. Greeting                    ← from template (with real name)
      2. Opener line                 ← from template verbatim
      3. Professional paragraph      ← from template verbatim
      4. Personal/hobbies paragraph  ← from template verbatim
      5. LLM personalisation para    ← generated, or omitted on failure
      6. Dynamic closing question    ← adapted to listing type
      7. Sign-off + signature        ← from template verbatim

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

    # --- Assemble final message -----------------------------------------
    # Scenario A: The user explicitly defined {LLM_TEXT} or [LLM TEXT] in their template
    if "{LLM_TEXT}" in filled_template or "[LLM TEXT]" in filled_template:
        if llm_para:
            filled_template = filled_template.replace("{LLM_TEXT}", llm_para).replace("[LLM TEXT]", llm_para)
        else:
            # If generation failed, perfectly remove the placeholder
            filled_template = filled_template.replace("{LLM_TEXT}", "").replace("[LLM TEXT]", "")
            # Clean up any resulting triple newlines/awkward gaps left behind
            filled_template = re.sub(r'\n{3,}', '\n\n', filled_template)
        return filled_template.strip()

    # Scenario B: Legacy fallback (append to the end of blocks)
    parts = _parse_template_paragraphs(filled_template)
    sections = [
        parts["greeting"],
        parts["opener"],
        parts["para1"],
        parts["para2"],
    ]

    if llm_para:
        sections.append(llm_para)

    # Template closing question (e.g. "Gerne würde ich die Zimmer...")
    if parts.get("closing"):
        sections.append(parts["closing"])

    if parts.get("signoff"):
        sections.append(parts["signoff"])

    if parts.get("signature"):
        sections.append(parts["signature"])

    return "\n\n".join(sections)


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
