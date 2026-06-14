"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import json
import os
import re
import urllib.error
import urllib.request

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

TOOL_MODEL = "llama-3.1-8b-instant"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _ask_llm(prompt: str, temperature: float = 0.6) -> str:
    """Send a single-turn prompt to the LLM and return the text response."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=TOOL_MODEL,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()



def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, ignoring very short fragments."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _item_text(item: dict) -> str:
    """Flatten a listing's searchable fields into one string."""
    parts = [
        item.get("title", ""),
        item.get("description", ""),
        item.get("category", ""),
        item.get("brand") or "",
        " ".join(item.get("style_tags", [])),
        " ".join(item.get("colors", [])),
    ]
    return " ".join(parts)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

_known_terms_cache: set[str] | None = None

def _get_known_terms() -> set[str]:
    global _known_terms_cache
    if _known_terms_cache is None:
        _known_terms_cache = set()
        for item in load_listings():
            _known_terms_cache |= _tokenize(_item_text(item))
    return _known_terms_cache


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
):
    has_size = size is not None and str(size).strip() != ""
    has_price = max_price is not None

    if not description or description.strip() == "":
        if not has_size and not has_price:
            return (
                "Cannot search: no parameters were provided. At minimum a "
                "`description` of the item you're looking for is required."
            )
        return "Cannot search: missing required parameter(s): description."

    query_tokens = _tokenize(description)
    known_terms = _get_known_terms()

    known_query_tokens = query_tokens & known_terms
    unknown_query_tokens = query_tokens - known_terms
    unknown_count = len(unknown_query_tokens)

    if not known_query_tokens:
        return []

    scored = []
    for item in load_listings():
        if max_price is not None and item.get("price", 0) > max_price:
            continue

        if size is not None and size.strip():
            if size.strip().lower() not in (item.get("size") or "").lower():
                continue

        item_tokens = _tokenize(_item_text(item))

        known_score = len(known_query_tokens & item_tokens)
        final_score = known_score / (2 ** unknown_count)

        if unknown_count == 0:
            # No unknowns — use raw score, just require at least one match
            if known_score > 0:
                scored.append((final_score, item))
        else:
            # Has unknowns — apply strict threshold to penalize
            """ Author note: In the case of final_score == 1, some queries such as `pink saree` should not give any results but `90s Silk Mini Dress` should probably match with `90s Silk Slip Dress`.
            # For the scope of this assignment, this will filter out final_score == 1, but combining semantic search would provide more accurate results.
            """
            if final_score > 1.0:
                scored.append((final_score, item))

    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored]

    return []


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    if not new_item or new_item == []:
        return "No outfit suggestions: no item was provided to style."

    item_line = f"{new_item.get('title', 'item')} ({new_item.get('category', '')}) — "
    item_line += f"colors: {', '.join(new_item.get('colors', []))}; "
    item_line += f"style: {', '.join(new_item.get('style_tags', []))}."

    items = (wardrobe or {}).get("items", [])
    if not items or items == []:
        prompt = (
            "You are a fashion stylist. The user is considering this thrifted item:\n"
            f"{item_line}\n\n"
            "They have no wardrobe on file. Give general styling advice: what kinds "
            "of pieces pair well with it, the vibe it suits, and 1-2 or more outfit ideas. "
            "Keep it to a short, friendly paragraph."
        )
        return _ask_llm(prompt)

    closet = "\n".join(
        f"- {it.get('name')} ({it.get('category')}; "
        f"{', '.join(it.get('colors', []))}; {', '.join(it.get('style_tags', []))})"
        for it in items
    )
    prompt = (
        "You are a fashion stylist. The user is considering this thrifted item:\n"
        f"{item_line}\n\n"
        "Their existing wardrobe:\n"
        f"{closet}\n\n"
        "Suggest 1-2 complete outfits that pair the new item with specific named "
        "pieces from their wardrobe. Reference the wardrobe items by name."
    )
    return _ask_llm(prompt)


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    error_message = ""
    if  outfit == "" or outfit.strip() == "":
        error_message+="Cannot create a fit card: no outfit suggestion was provided"
    if new_item == {}:
            if error_message == "":
                error_message+="Cannot create a fit card: "
            else:
                error_message+= " and "
            error_message+="no chosen item for the user was provided"
    if error_message !="":
        return error_message

    prompt = (
        "Write a casual, authentic OOTD-style social caption (2-4 sentences) for a "
        "thrifted find. Sound like a real person posting, not a product listing.\n\n"
        f"Item: {new_item.get('title', 'thrifted piece')}\n"
        f"Price: ${new_item.get('price', '?')}\n"
        f"Platform: {new_item.get('platform', 'thrift')}\n"
        f"Outfit: {outfit}\n\n"
        "Mention the item name, price, and platform naturally (once each) and "
        "capture the outfit vibe in specific terms."
    )

    caption = _ask_llm(prompt, temperature=0.9)
    if caption and caption.strip():
        return caption
    return "Cannot create a fit card: caption generation returned no text."

# ── Tool 4: price_comparision ───────────────────────────────────────────────────


def price_comparision(new_item: dict):
    """
    Provides a price assessment by comparing an item to other comparable items
    in the mock listings dataset.

    Args:
        new_item: The listing dict for the item the user selected.

    Returns:
        On success: "assessment":  str,   # paragraph + fair/overpriced/underpriced verdict
        On failure (missing item, or no comparables to assess against), returns a
        descriptive error string — does NOT raise an exception.

    Failure handling (per spec):
        - new_item empty / missing: return an error string.
        - No comparable items found: return an error string.
    """
    if not new_item or new_item == {}:
        return "Cannot assess price: no item was provided."

    category = new_item.get("category")
    comparables = [
        it for it in load_listings()
        if it.get("category") == category and it.get("id") != new_item.get("id")
    ]
    if not comparables:
        return "Cannot assess price: no comparable items found in the dataset."

    prices = [it.get("price", 0) for it in comparables]
    avg = sum(prices) / len(prices)
    item_price = new_item.get("price", 0)

    comp_lines = "\n".join(
        f"- {it.get('title')}: ${it.get('price')}" for it in comparables[:8]
    )
    prompt = (
        "You are a thrift pricing expert. Assess whether this item is fairly priced "
        "against comparable listings, then give a clear final verdict.\n\n"
        f"Item: {new_item.get('title')} — ${item_price}\n"
        f"Average price of {len(comparables)} comparable {category}: ${avg:.2f}\n"
        f"Comparables:\n{comp_lines}\n\n"
        "Write a short paragraph with the assessment and a final fair/overpriced/"
        "underpriced verdict."
    )
    return _ask_llm(prompt)

# ── Tool 5: suggest_trends ───────────────────────────────────────────────────


def suggest_trends(user_size: str | None) -> str:
    """
    Find latest fashion trends for the user by looking at latest posts or tags that match the user's size range.

    Args:
        wardrobe: dict

    Returns:
        A 2–4 sentence string describing (5) trends for the user.
        This should be 

    TODO:
        1. Guard against an empty .
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # Replace this with your implementation
    return ""


