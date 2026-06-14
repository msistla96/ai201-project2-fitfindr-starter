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
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


from tools import (
    TOOL_MODEL,
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
    """Extract description, size, and max_price from a natural-language query using Groq, with regex fallback."""

    prompt = f"""Extract search parameters from this fashion query and return ONLY a JSON object with no explanation.

    Query: "{query}"

    Return this exact structure:
    {{"description": "clothing item description only, no size or price info", "size": "size if mentioned, otherwise null", "max_price": number if a price or budget is mentioned, otherwise null}}

    Rules:
    - description should only contain what the item IS (e.g. "vintage graphic tee", "flowy midi skirt")
    - If the query is just a number like "100" or makes no sense as a fashion query, set description to null
    - size examples: "M", "S/M", "W28", "US 8"
    - max_price should be a number only, no $ sign"""

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=TOOL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)

        description = parsed.get("description") or ""
        size = parsed.get("size")
        max_price = parsed.get("max_price")

        logger.info(f"Parsed Query : description: {description}, size: {size}, max_price: {max_price}")
        return {"description": description, "size": size, "max_price": max_price}

    except Exception:
        logger.warning("LLM Query parse failed, falling back to regex")

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

        description = re.sub(r"\$\s*\d+(?:\.\d+)?", " ", leftover)
        description = re.sub(
            r"\b(i'?m|i am|looking for|a|an|the|for|some|please|find|me)\b",
            " ",
            description,
            flags=re.IGNORECASE,
        )
        description = re.sub(r"\s+", " ", description).strip(" ,.")

        logger.info(f"Parsed Query (regex): description: {description}, size: {size}, max_price: {max_price}")
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
        logger.info(f"\nTool name called: {tool_name}\n")
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

    logger.info(f"\nQuery parsed: {session['parsed']}\n")
    description = parsed["description"]
    size, max_price = parsed["size"], parsed["max_price"]

    # Invalid input (missing description) → tool returns an error string.
    logger.info(f": Passing parameters {description} , {size}, {max_price}\n")
    results = search_listings(description, size, max_price)
    if isinstance(results, str):
        session["error"] = results
        return False

    # No matches: retry dropping size, then price (per spec).
    if not results and size is not None:
        logger.info(f"\nRetrying call with no size passed:\n")
        results = search_listings(description, None, max_price)
    if not results and max_price is not None:
        logger.info(f"\nRetrying call with no price passed:\n")
        results = search_listings(description, None, None)

    if not results:
        session["error"] = (
            f"No listings matched '{description or query_text(session)}'. "
            "Try a different description, size, or price."
        )
        return False

    session["search_results"] = results
    session["selected_item"] = results[0]

    logger.info(f": Stored search_results:{session['search_results']} and selected_item: {session['selected_item']} in session\n")
    return True


def _step_price(session: dict) -> bool:
    """price_comparision — store the assessment and always continue (per spec)."""

    logger.info(f"\n: Using selected_item: {session['selected_item']}")
    session["price_comparision"] = price_comparision(session["selected_item"])
    logger.info(f": Stored price_comparision: {session['price_comparision']} in session\n")
    return True


# def _step_trends(session: dict) -> bool:
#     """suggest_trends — store trends and always continue (per spec)."""
#     session["trends_for_user"] = suggest_trends(session["parsed"]["size"])
#     return True


def _step_outfit(session: dict) -> bool:
    """suggest_outfit — empty-wardrobe fallback is handled inside the tool."""
    logger.info(f"\n: Using selected_item: {session['selected_item']} and wardrobe: {session['wardrobe']}\n")
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )
    logger.info(f": Stored outfit_suggestion:{session['outfit_suggestion']}\n")
    return True


def _step_fit_card(session: dict) -> bool:
    """create_fit_card — the final tool; missing outfit ends the run with an error."""
    outfit = session["outfit_suggestion"] or ""
    if outfit == "" or not outfit:
        session["error"] = f"Error: Outfit suggestions are missing or empty."
        return False
    logger.info(f"\nUsing outfit_suggestion: {session['outfit_suggestion']} and selected_item: {session['selected_item']}\n")
    card = create_fit_card(outfit, session['selected_item'])
    if card.lower().startswith("cannot create"):
        session["error"] = card
        return False
    session["fit_card"] = card
    logger.info(f"Stored fit_card:{session['fit_card']}\n")
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

    print("\n\n=== search_listings retry due to no match ===\n")
    session3 = run_agent(
        query="silk saree under 100",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")

    print("\n\n=== description is None ===\n")
    session3 = run_agent(
        query="100",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")
    

    print("\n\n=== match due to midi,dress ===\n")
    session3 = run_agent(
        query="flowy midi dress under $40",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")

    
    print("\n\n=== match due to black, boots===\n")
    session3 = run_agent(
        query="black boots size 8",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")

    print("\n\n=== perfect match ===\n")
    session3 = run_agent(
        query="90s track jacket in size M",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"Price: {session3['price_comparision']}")
        print(f"\nOutfit: {session3['outfit_suggestion']}")
        print(f"\nFit card: {session3['fit_card']}")

        
