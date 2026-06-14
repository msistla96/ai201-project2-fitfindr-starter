"""
Tests for the FitFindr tools, based on the Tool specs in planning.md.

LLM-backed tools (suggest_outfit, create_fit_card, price_comparision) are tested
on their guard/error paths only — the LLM call (_ask_llm) is monkeypatched so the
tests run offline without a GROQ_API_KEY.
"""

import pytest
import re

import tools
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace _ask_llm with a deterministic echo so the prompt is observable."""
    calls = {}

    def _fake(prompt, temperature=0.6):
        calls["prompt"] = prompt
        calls["temperature"] = temperature
        return "FAKE_LLM_RESPONSE"

    monkeypatch.setattr(tools, "_ask_llm", _fake)
    return calls


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def test_search_returns_matches_sorted_by_relevance():
    results = tools.search_listings("vintage denim jeans")
    assert len(results) > 0
    assert all(isinstance(r, dict) for r in results)

    # Scores are non-increasing: best match first.
    scores = [
        len(tools._tokenize("vintage denim jeans") & tools._tokenize(tools._item_text(r)))
        for r in results
    ]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_max_price():
    results = tools.search_listings("jeans", max_price=40.0)
    assert len(results) > 0
    assert all(r["price"] <= 40.0 for r in results)


def test_search_size_filter_is_case_insensitive():
    results = tools.search_listings("jeans", size="w30 l30")
    assert len(results) > 0
    assert all("w30 l30" in r["size"].lower() for r in results)


def test_search_empty_description_returns_error_string():

    # If description string is empty
    msg = tools.search_listings("", size="M",max_price="20")
    assert isinstance(msg, str)
    assert "description" in msg.lower()

    # If description string is not passed at all
    with pytest.raises(TypeError, match= re.escape("search_listings() missing 1 required positional argument: 'description'")):
        tools.search_listings(size="M",max_price="20")


def test_search_no_parameters_at_all_returns_error_string():

    # If description string is empty and the other parameters are not passed
    msg = tools.search_listings("")
    assert isinstance(msg, str)
    assert "no parameters were provided" in msg

    # If all parameters are not passed
    with pytest.raises(TypeError, match= re.escape("search_listings() missing 1 required positional argument: 'description'")):
        tools.search_listings()


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def test_suggest_outfit_missing_item_returns_error(fake_llm):
    msg = tools.suggest_outfit({}, get_example_wardrobe())
    assert isinstance(msg, str) and msg
    assert "prompt" not in fake_llm  # LLM not called for empty item


def test_suggest_outfit_empty_wardrobe_gives_general_advice(fake_llm):

    # Wardrobe is present but not items 
    item = {"title": "Red dress", "category": "dresses", "colors": ["red"], "style_tags": ["cute"]}
    out = tools.suggest_outfit(item, get_empty_wardrobe())
    assert out == "FAKE_LLM_RESPONSE"
    assert "no wardrobe" in fake_llm["prompt"].lower()

    # Wardbrobe is empty to begin with
    item = {"title": "Red dress", "category": "dresses", "colors": ["red"], "style_tags": ["cute"]}
    out = tools.suggest_outfit(item, {})
    assert out == "FAKE_LLM_RESPONSE"
    assert "no wardrobe" in fake_llm["prompt"].lower()


def test_suggest_outfit_uses_wardrobe_items(fake_llm):
    item = {"title": "Red dress", "category": "dresses", "colors": ["red"], "style_tags": ["cute"]}
    out = tools.suggest_outfit(item, get_example_wardrobe())
    assert out == "FAKE_LLM_RESPONSE"
    # A named wardrobe piece should appear in the prompt.
    assert "denim jacket" in fake_llm["prompt"].lower()


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def test_create_fit_card_empty_outfit_returns_error(fake_llm):

    # Only spaces in the outfit description
    msg = tools.create_fit_card("   ", {"title": "x"})
    assert isinstance(msg, str) and msg
    assert "prompt" not in fake_llm

    # Only empty string in the outfit description
    msg = tools.create_fit_card("", {"title": "x"})
    assert isinstance(msg, str) and msg
    assert "prompt" not in fake_llm

    # Empty chosen item is provided
    msg = tools.create_fit_card("  ",{})
    assert isinstance(msg, str) and msg
    assert "prompt" not in fake_llm

    # No chosen item is provided
    with pytest.raises(TypeError):
        tools.create_fit_card(outfit="")

    # No outfit description is provided
    with pytest.raises(TypeError):
        tools.create_fit_card()

    # No outfit description is provided
    with pytest.raises(TypeError):
        tools.create_fit_card(new_item={})


def test_create_fit_card_builds_caption(fake_llm):
    item = {"title": "Levi's 501", "price": 38.0, "platform": "depop"}
    out = tools.create_fit_card("Pair with white tank and sneakers.", item)
    assert out == "FAKE_LLM_RESPONSE"
    assert fake_llm["temperature"] > 0.6  # higher temp per spec
    assert "depop" in fake_llm["prompt"]

# ── Tool 4: price_comparision ─────────────────────────────────────────────────

def test_price_comparision_empty_item_returns_error(fake_llm):
    msg = tools.price_comparision({})
    assert isinstance(msg, str) and msg
    assert "prompt" not in fake_llm


def test_price_comparision_compares_same_category(fake_llm):
    listing = tools.load_listings()[0]  # a bottoms item
    out = tools.price_comparision(listing)
    assert out == "FAKE_LLM_RESPONSE"
    assert "average price" in fake_llm["prompt"].lower()

