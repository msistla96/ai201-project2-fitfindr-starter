# FitFindr — Starter Kit

This starter kit contains everything you need to begin Project 2.

## What's Included

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example wardrobe
├── utils/
│   └── data_loader.py         # Helper functions for loading the data
├── planning.md                # Your planning template — fill this out first
└── requirements.txt           # Python dependencies
```

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (get a free key at [console.groq.com](https://console.groq.com)):
```
GROQ_API_KEY=your_key_here
```

## The Mock Listings Dataset

`data/listings.json` contains 40 mock secondhand listings across categories (tops, bottoms, outerwear, shoes, accessories) and styles (vintage, y2k, grunge, cottagecore, streetwear, and more).

Each listing has: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, and `platform`.

Load it with:
```python
from utils.data_loader import load_listings
listings = load_listings()
```

## The Wardrobe Schema

`data/wardrobe_schema.json` defines the format your agent uses to represent a user's existing wardrobe. It includes:

- `schema`: field definitions for a wardrobe item
- `example_wardrobe`: a sample wardrobe with 10 items you can use for testing
- `empty_wardrobe`: a starting template for a new user

Load an example wardrobe with:
```python
from utils.data_loader import get_example_wardrobe
wardrobe = get_example_wardrobe()


```

<!-- ## Where to Start

1. **Read `planning.md` and fill it out before writing any code.**
2. Verify the data loads correctly by running `python utils/data_loader.py`.
3. Build and test each tool individually before connecting them through your planning loop.

Your implementation files go in this same directory. There's no required file structure for your agent code — organize it however makes sense for your design. -->


## Tools

### Tool 1: search_listings

**What it does:**

This tool searches the `listings.json` file which is a mock listings dataset based on some parameters, ranks by best match and then returns a set of matching items from the dataset.

**Input parameters:**

- `description` (str): Describes what the item is i.e Tops, Bottoms, accessories etc.
- `size` (str): Size of the item the user wants.
- `max_price` (float): The maximum price that the user is fine to pay upto.

**What it returns:**

Returns a list of matching items from the dataset. Each item is of the following format:
     "id":
    "title"
    "description"
    "category"
    "style_tags"
    "size"
    "condition"
    "price"
    "colors"
    "brand"
    "platform"

It also returns metadata indicating what parameters matched:
     description: boolean
     size: boolean
     price: boolean

**What happens if it fails or returns nothing:**

The following are the ways the tool can fail:

1. No matching items are returned: Return an empty list, let the user know and then retry with size excluded, then price. If there are no results even after retry, end the workflow.
2. If either description or all parameters are not provided: Provide a descriptive error message string about what is missing.

---

### Tool 2: suggest_outfit

**What it does:**

Using the user's wardrobe and an item, this tool provides a description of what the item matches with in the user's wardrobe with details. 

**Input parameters:**

- `new_item` (dict): Item chosen from search_listings()
- `wardrobe` (dict): List of items that the user already owns.

**What it returns:**

A non empty string with a detailed description of what in the user's wardrobe goes with the item.

**What happens if it fails or returns nothing:**

1. When the wardrobe is empty or when it returns no suggestions: Provide a descriptive error message to the user and provide general styling advices with the item.

---

### Tool 3: create_fit_card

**What it does:**

This tools provides a shareable description about the new outfits generated from suggest_outfits() paired with the new item.

**Input parameters:**

- `outfit` (string): String generated from suggest_outfits()
- `new_item`(dict): Item chosen from search_listings()

**What it returns:**

Returns a captionable post that feels casual and authentic, capturing the outfit vibes with specific terms and also talking about the new item name, price and platform it was found on.

**What happens if it fails or returns nothing:**

1. When the outfit string is empty/incomplete or when tool returns an empty string: Provide a descriptive error message string and end the workflow.

---

### Additional Tools (if any)


### Tool 4: price_comparision

**What it does:**

This tool provides a price assessment about an item against other comparable items in the `listing.json` dataset to see if it's a fair price.

**Input parameters:**

- `new_items` (dict): Item selected by the user.

**What it returns:**
A non empty string providing details about the price assessment and a final verdict of if the item has a fair and reasonable price.

**What happens if it fails or returns nothing:**

1. When it returns nothing: Provide a descriptive error message string and continue the flow.

<!-- ### Tool 5: suggest_trends

<!-- **What it does:**
<!-- Describe what this tool does in 1–2 sentences -->
This tool gets a list of posts/tags from a public fashion platform based on the user's sizing range if specified in the query, to find the the most trendy/popular/hot/top styles.

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `size` (string): User's size from the query.

**What it returns:**
<!-- Describe the return value -->
A non empty string providing details about trending and popular styles for the user's size range.

**What happens if it fails or returns nothing:**
<!--What should the agent do if the outfit data is incomplete? -->
1. When size is empty or when it returns no results: Provide a descriptive error message with respect to the issue and retry the call with no size. -->

---

## Planning Loop

**How does your agent decide which tool to call next?**

The planning loop takes the following inputs:

1. User Query
2. Session state
3. Number of iterations completed


The planning loop does the following:
1. It checks if the session has an error message. If it does, it exits the loop and returns it.
1. It then checks to see if the number of iterations are under the limits. If it has reached limits, it exits the loop.
2. If it is the first iteration, it reads the query, session state and list of tools calls [search_listings, suggest_outfit, create_fit_card, price_comparision, suggest_trends], using a ReACT style prompt to reason and create a plan for which tool calls it needs to fulfill the query.  It then lists the tools it plans to use in order. It starts with the the first tool call and returns its responses/errors, while updating the results of each tool call in the session state. The next tool is called.
2. If the previous tool call has any errors, it will display any error messages to the user and allow the user to retry with a different query(expected results from tool calls are missing or null), answer(if required parameters are missing from the calls) or terminate the workflow gracefully if the user wants to quit.
3. If the previous tool call was successful, it will save its results to the session and then make the next tool call.
4. Once it has finished all of its tool calls or reached the iteration limits, it will provide a graceful exit message with any final responses from create_fit_card(if it's the final tool call) or any other tool called to the user.

---

## State Management

**How does information from one tool get passed to the next?**

A Session variables store the user queries, wardrobe, tool call results, iterations done and allowed, tool calls made and any errors it returns. This can look like the following:

`user_queries`: dict(str)
`user_wardrobe`: dict
`matched_items`: list(dict)
`suggested_outfits`: str
`chosen_item`: dict
<!--`trends_for_user`: str-->
`price_assessment`: str
`fit_card`: str
`error`: str
`number_of_iterations`: int
`max_iterations`: 10

Once each tool call is made, it stores the results/errors it gets to the following variables:

search_listings: `matched_items`
price_comparision: `price_assessment`
suggest_outfit: `suggested_outfits`
<!--suggest_trends: `trends_for_user`-->
create_fit_card: `fit_card`

These variables are globally available to each tool call, but only the required data is passed to the tool call. The planning loop updates `number_of_iterations` after each tool call. 


---

## Error Handling


| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Agent retries with looser constraints and returns results if any or simple shows the user an error and asks to try with a different value. |
| suggest_outfit | Wardrobe is empty or missing | Agent returns a response with a more general outfit suggestion.  |
| create_fit_card | Outfit input is missing or incomplete | Agent returns an error to the user and asks to retry again |
| price_comparision | No results are returned | Agent lets the user know that there are no results and proceeds as usual. | 
<!-- | suggest_trends | Size is missing or it returns no results  | Lets the the user know respective error message and provides general trending styles without any size(Based on Tool Spec) | -->


**Example queries**

**Trigger search_listings retry when no results match**
Query: silk saree
Agent Answer: Error: No listings matched 'silk saree'. Try a different description, size, or price.

**Description is found to be missing or nonsensical before calling search_listings**
Query: 100
Agent Answer: Error: Cannot search: no parameters were provided. At minimum a `description` of the item you're looking for is required.

---


## AI Tool Plan

**Milestone 3 — Individual tool implementations:**

Tools used: Claude Code, Claude
Files provided to tools: `Tools` in `planning.md`
Files implemented: `tools.py`, `test_tools.py` under `tests`
Changes or overrides made to implementation: 
1. Added test cases for `search_listings()`, `suggest_outfits()` and `create_fit_card()` where missing required parameters would trigger TypeErrors.
2. The initial specification had tools handle retries and errors. I rewrote code and tests to keep them out of the tools and only have them be present in the agent loop.


**Milestone 4 — Planning loop and state management:**

Tools used: Claude Code, Claude, ChatGPT
Files provided to tools: `Planning Loop`, `State Management`. `Architecture`, `Error Handling` in  `planning.md`
Files implemented: `agent.py`, `app.py`
Changes or overrides made to implementation: 
1. Claude Code did not create the agent code to be agentic and ended up hard coding the tool list for the plan. I had it rewrite the code, pointing to specific sentences in the files about the ReACT loop that it didn't catch before.
2. Claude Code added unecessary code to parse the tool schemas and add it in the agent LLM prompt. I had it take a look at Groq's documentation to use the tools parameters for the models and then parse the schema so that it can be passed to the model directly. 
3. I added some additional test cases under the test_agent so that it was more reflective of `Error Handling`.


---

## Spec Reflection

The Specifications provided in `plannning.md` helped me to use Test Driven Development as part of the implementation, which in turn helped me improve and refine my decision making for how to implement `agent.py`. 

Some of the ways I diverged from the specification:
1. I iterated back and forth from testing the agent and checking if the agent decision making needed to be refined or further modified i.e if parsing the query with Regex worked for all cases or could an LLM be involved as a hybrid approach. I tried both approaches and decided to use an LLM for accuracy as the regex search did not check to see if a parameter was filled with a valid value. Regex would be a backup if the LLM failed, which was something I didn't realize was necessary until I encountered some rate limiting issues from the model. 
2. I also made a decision to use a smaller model `llama-3.1-8b-instant` for the LLM used in `tools.py` and for Query parsing in `agent.py` as there were rate limiting issues with a 10k token limit per day. Also, the LLM used by the tools and query parsing was mainly for summarization and parsing respectively which are capabilties that smaller models have been doing well on for a long time as opposed to planning used for the Agent, which is done by a larger model. This helps reduce tokens and prevents rate limiting issues caused by using one single model. It is worth testing models for planning as well.
3. `search_listings()` in the initial implementation used blind keyword search, which did not work well for the query `pink saree` as it would match anything that is pink even if a saree was never in the list. To make sure the search was tighter, I came up with a keyword search that score matched and unmatched words as a ratio, penalizing if number of unmatched words is larger than matched words (Claude suggested filtering unknown words and matching known words but it didn't work). Semantic search should solve the issue but it is beyond the scope of the assignment.
