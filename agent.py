"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import inspect
import re

from tools import (
    _get_groq_client,
    search_listings,
    suggest_outfit,
    create_fit_card,
    price_comparision,
    suggest_trends,
)

_TYPE_MAP = {"str": "string", "float": "number", "int": "integer", "dict": "object"}

MAX_ITERATIONS = 10

AGENT_MODEL = "llama-3.3-70b-versatile" # Use larger model for Agent

# The tools the planner may choose from. Their descriptions and parameters come
# straight from the documented functions in tools.py (no duplicated docs here).
TOOL_FUNCS = {
    "search_listings": search_listings,
    "price_comparision": price_comparision,
    "suggest_trends": suggest_trends,
    "suggest_outfit": suggest_outfit,
    "create_fit_card": create_fit_card,
}


def _tool_schemas() -> list[dict]:
    """
    Build Groq/OpenAI function-tool schemas from the documented functions in
    tools.py — descriptions and parameters are read off each signature/docstring
    so the planner sees exactly what tools.py defines.
    """
    schemas = []
    for name, fn in TOOL_FUNCS.items():
        properties = {}
        for pname, param in inspect.signature(fn).parameters.items():
            ann = getattr(param.annotation, "__name__", str(param.annotation))
            properties[pname] = {"type": _TYPE_MAP.get(ann, "string")}
        doc = inspect.getdoc(fn) or ""
        summary = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": summary,
                "parameters": {"type": "object", "properties": properties},
            },
        })
    return schemas


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "price_comparision": None,
        "trends_for_user": None,
        "error": None,               # set if the interaction ended early
    }


# ── query parsing ───────────────────────────────────────────────────────────

_PRICE_RE = re.compile(
    r"(?:under|below|up\s*to|upto|max|budget(?:\s*of)?|<)\s*\$?\s*(\d+(?:\.\d+)?)"
    r"|\$\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"\bsize\s+([a-z0-9/]+)\b", re.IGNORECASE)


def _parse_query(query: str) -> dict:
    """Extract description, size, and max_price from a natural-language query."""
    size = None
    max_price = None
    leftover = query

    price_match = _PRICE_RE.search(query)
    if price_match:
        max_price = float(price_match.group(1) or price_match.group(2))
        leftover = leftover.replace(price_match.group(0), " ")

    size_match = _SIZE_RE.search(query)
    if size_match:
        size = size_match.group(1)
        leftover = leftover.replace(size_match.group(0), " ")

    # The remaining words, stripped of common filler, form the description.
    description = re.sub(r"\$\s*\d+(?:\.\d+)?", " ", leftover)
    description = re.sub(
        r"\b(i'?m|i am|looking for|a|an|the|for|some|please|find|me)\b",
        " ",
        description,
        flags=re.IGNORECASE,
    )
    description = re.sub(r"\s+", " ", description).strip(" ,.")

    print(f"Parsed Query: description: {description}, size: {size}, max_price: {max_price}")

    return {"description": description, "size": size, "max_price": max_price}


# ── ReAct planner (Groq native tool calling) ──────────────────────────────────

def _plan_tools(query: str) -> list[str]:
    """
    Reason ReAct-style over the query and emit the ordered list of tools to call.

    Uses Groq native tool calling: the tool schemas are passed via `tools` and the
    model replies with `tool_calls` (it may call several in one turn). The order of
    those tool calls is the plan. Falls back to the full pipeline if the model
    returns no tool calls or the LLM is unavailable.
    """
    messages = [{
        "role": "user",
        "content": (
            "You are FitFindr's planning agent. Decide which of the available tools "
            "to call, and in what order, to fulfil the user's request. Call every "
            "tool the request needs (and only those) in the right order. "
            "search_listings must be called first because the other tools depend on "
            "the item it finds; create_fit_card, if needed, must be called last "
            "because it needs an outfit.\n\n"
            f"User query: {query!r}"
        ),
    }]

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model=AGENT_MODEL,
            temperature=0.0,
            messages=messages,
            tools=_tool_schemas(),
            tool_choice="auto",
        )
        tool_calls = resp.choices[0].message.tool_calls or []
        plan = [tc.function.name for tc in tool_calls if tc.function.name in TOOL_FUNCS]
    except Exception:
        plan = []

    if not plan:
        plan = list(TOOL_FUNCS.keys())  # fallback: full pipeline in dependency order

    # Enforce hard dependencies regardless of the model's output.
    plan = [t for t in plan if t != "search_listings"]
    plan.insert(0, "search_listings")
    if "create_fit_card" in plan:
        plan = [t for t in plan if t != "create_fit_card"] + ["create_fit_card"]
    return plan


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    session = _new_session(query, wardrobe)

    # First iteration: read the query, then let the ReAct planner (LLM) decide the
    # ordered list of tools to call.
    session["parsed"] = _parse_query(query)
    plan = _plan_tools(query)
    session["plan"] = plan

    iterations = 0
    for tool_name in plan:
        print("Tool name", tool_name)
        # The loop always checks it hasn't exceeded the iteration budget.
        if iterations >= MAX_ITERATIONS:
            break
        iterations += 1
        # Execute the tool via its handler; it stores results/errors in the session.
        # A handler returns False to halt the loop early (after setting session["error"]).
        if STEP_HANDLERS[tool_name](session) is False:
            break

    session["iterations"] = iterations
    return session


# ── plan steps — each reads/writes the shared session (State Management) ───────

def _step_search(session: dict) -> bool:
    """search_listings, retrying with looser constraints per Error Handling."""
    parsed = session["parsed"]
    description = parsed["description"]
    size, max_price = parsed["size"], parsed["max_price"]

    # Invalid input (missing description) → tool returns an error string.
    results = search_listings(description, size, max_price)
    if isinstance(results, str):
        session["error"] = results
        return False

    # No matches: retry dropping size, then price (per spec).
    if not results and size is not None:
        results = search_listings(description, None, max_price)
    if not results and max_price is not None:
        results = search_listings(description, None, None)

    if not results:
        session["error"] = (
            f"No listings matched '{description or query_text(session)}'. "
            "Try a different description, size, or budget."
        )
        return False

    session["search_results"] = results
    session["selected_item"] = results[0]
    return True


def _step_price(session: dict) -> bool:
    """price_comparision — store the assessment and always continue (per spec)."""
    session["price_comparision"] = price_comparision(session["selected_item"])
    return True


# def _step_trends(session: dict) -> bool:
#     """suggest_trends — store trends and always continue (per spec)."""
#     session["trends_for_user"] = suggest_trends(session["parsed"]["size"])
#     return True


def _step_outfit(session: dict) -> bool:
    """suggest_outfit — empty-wardrobe fallback is handled inside the tool."""
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )
    return True


def _step_fit_card(session: dict) -> bool:
    """create_fit_card — the final tool; missing outfit ends the run with an error."""
    outfit = session["outfit_suggestion"] or ""
    card = create_fit_card(outfit, session["selected_item"])
    if not outfit.strip() or card.lower().startswith("cannot create"):
        session["error"] = card
        return False
    session["fit_card"] = card
    return True


def query_text(session: dict) -> str:
    """Original query, used in error messages when the parse yielded no description."""
    return session["query"]


# Maps each planner-chosen tool name to the handler that executes it against the
# session. The planning loop calls these in the order the ReAct planner returned.
STEP_HANDLERS = {
    "search_listings": _step_search,
    "price_comparision": _step_price,
    # "suggest_trends": _step_trends,
    "suggest_outfit": _step_outfit,
    "create_fit_card": _step_fit_card,
}


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"Price: {session['price_comparision']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")

    print("\n\n=== No-results path with empty wardrobe ===\n")
    session3 = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")
