"""tools.py — The real (and mock) side-effecting tools.

The executor calls :func:`execute_step` to actually *do* the drafted action.
For the MSFT stock-price test this performs a genuine quote lookup via Stooq
(no API key required) and degrades gracefully to a deterministic mock when
offline, so the harness always runs.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, Optional
from urllib.parse import quote_plus

try:  # requests is optional; we fall back to mocks without it.
    import requests
except Exception:  # pragma: no cover - import guard
    requests = None  # type: ignore[assignment]


# Minimal name -> ticker map for the demo; extend as needed.
_KNOWN_TICKERS = {
    "microsoft": "MSFT",
    "msft": "MSFT",
    "apple": "AAPL",
    "aapl": "AAPL",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "visa": "V",
    "mastercard": "MA",
    "tesla": "TSLA",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "intel": "INTC",
    "disney": "DIS",
    "walmart": "WMT",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "paypal": "PYPL",
    "boeing": "BA",
    "coca-cola": "KO",
    "coca cola": "KO",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
}


def _detect_symbol(text: str) -> Optional[str]:
    low = text.lower()
    for name, ticker in _KNOWN_TICKERS.items():
        if name in low:
            return ticker
    for token in re.findall(r"\b([A-Z]{1,5})\b", text):
        if token not in _NON_TICKERS:
            return token
    return None


# Uppercase tokens that are ordinary words (e.g. "I will...") or exchange /
# currency / finance acronyms, not tickers.
_NON_TICKERS = {
    "I", "A", "AN", "THE", "USD", "IT", "TO", "OF", "IN", "ON", "AT", "BY",
    "AND", "OR", "URL", "AM", "PM", "OK", "ID", "US", "UK", "EU", "API",
    # Exchanges
    "NYSE", "AMEX", "LSE", "TSX", "TSXV", "NSE", "BSE", "HKEX", "SGX", "JSE",
    # Currencies
    "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "HKD", "SEK", "INR",
    # Finance / corporate acronyms
    "ETF", "IPO", "SEC", "CEO", "CFO", "CTO", "ADR", "PLC", "INC", "LLC",
    "LTD", "CO", "USA", "GDP", "EPS", "PE", "AI", "FAQ",
}


def _symbol_from(inputs: Optional[dict[str, Any]], context: str) -> str:
    """Resolve a ticker from the run's inputs (preferring a stock/company field),
    else the step/action text, else default to MSFT."""
    inputs = inputs or {}
    for key, value in inputs.items():
        k = str(key).lower()
        if any(t in k for t in ("symbol", "ticker", "stock", "company", "security")):
            v = str(value).strip()
            if v:
                return resolve_ticker(v)
    for value in inputs.values():
        v = str(value).strip()
        if v:
            return resolve_ticker(v)
    return _detect_symbol(context) or "MSFT"


_TICKER_CACHE: dict[str, str] = {}


def _yahoo_symbol_search(query: str) -> Optional[str]:
    """Resolve an arbitrary company name to a ticker via Yahoo's symbol search."""
    if requests is None:
        return None
    try:
        resp = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 5, "newsCount": 0},
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        quotes = resp.json().get("quotes", [])
    except Exception:
        return None
    for quote in quotes:
        if quote.get("quoteType") in ("EQUITY", "ETF") and quote.get("symbol"):
            return quote["symbol"]
    if quotes and quotes[0].get("symbol"):
        return quotes[0]["symbol"]
    return None


def _looks_like_ticker(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}", text)) and text not in _NON_TICKERS


def resolve_ticker(query: str) -> str:
    """Resolve a company name OR ticker to a stock symbol. Order: known map ->
    obvious ticker -> live Yahoo symbol search (cached) -> text heuristic -> MSFT.
    A real lookup means we never have to hand-maintain a company list."""
    q = (query or "").strip()
    if not q:
        return "MSFT"
    low = q.lower()
    for name, ticker in _KNOWN_TICKERS.items():
        if name in low:
            return ticker
    if _looks_like_ticker(q):
        return q
    if low in _TICKER_CACHE:
        return _TICKER_CACHE[low]
    found = _yahoo_symbol_search(q)
    if found:
        _TICKER_CACHE[low] = found
        return found
    return _detect_symbol(q) or "MSFT"


def get_stock_price(symbol: str = "MSFT") -> str:
    """Return a human-readable quote for ``symbol`` from Yahoo Finance, or a mock.

    ``symbol`` may be a ticker or a company name; it is resolved to a ticker."""
    symbol = resolve_ticker(symbol)
    if requests is not None:
        try:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                "?interval=1d&range=1d"
            )
            resp = requests.get(
                url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            if price is not None:
                currency = meta.get("currency", "USD")
                return (
                    f"{symbol.upper()} is trading at {price} {currency} "
                    "[source: yahoo finance]"
                )
        except Exception as exc:  # network/parse failure -> mock
            return (
                f"[mock] Could not fetch live {symbol.upper()} price "
                f"({exc.__class__.__name__}); simulated $427.13"
            )
    return f"[mock] Simulated {symbol.upper()} price $427.13"


def web_search(query: str) -> str:
    """Mocked web search placeholder (swap for a real search API later)."""
    return f"[mock] Web search results for '{query}'"


# ---------------------------------------------------------------------------
# Playwright browsing — let a skill navigate the web to gain insight.
# ---------------------------------------------------------------------------
def _looks_like_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://"))


def _browse_query(step: dict[str, Any], action: str) -> str:
    """Pick what to browse: an explicit URL, a quoted query, or the step name."""
    text = f"{step.get('description', '')} {action}"
    url = re.search(r"https?://\S+", text)
    if url:
        return url.group(0)
    quoted = re.search(r"[\"']([^\"']{3,})[\"']", text)
    if quoted:
        return quoted.group(1)
    return step.get("name") or text[:80]


def _browse_sync(target: str) -> str:
    """Drive a headless Chromium via Playwright and extract page insight."""
    from playwright.sync_api import sync_playwright

    url = target.strip() if _looks_like_url(target) else (
        f"https://www.bing.com/search?q={quote_plus(target)}"
    )
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=ua)
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            title = (page.title() or "").strip()
            body = page.inner_text("body")
        finally:
            browser.close()
    snippet = re.sub(r"\s+", " ", body).strip()[:500]
    where = url if _looks_like_url(target) else f"web search '{target}'"
    return f"[browsed] {where}: {title}. {snippet} [via playwright]"


def _browse_fallback(target: str, exc: Exception) -> str:
    """When Playwright is unavailable, try a plain HTTP fetch, else mock."""
    if requests is not None and _looks_like_url(target):
        try:
            r = requests.get(target, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).strip()[:400]
            return f"[browsed:http] {target}: {text}"
        except Exception:
            pass
    return (
        f"[mock] Would browse '{target}' but Playwright is unavailable "
        f"({exc.__class__.__name__}). Run: playwright install chromium"
    )


def browse_web(target: str) -> str:
    """Navigate the web (URL or search query) and return a text insight.

    Playwright's sync API is run in a dedicated worker thread so it works
    inside FastAPI's threadpool (no running asyncio loop there).
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_browse_sync, target).result(timeout=45)
    except Exception as exc:
        return _browse_fallback(target, exc)


# Verbs that indicate a step wants to drive/inspect a web page.
_BROWSE_KEYWORDS = (
    "open browser",
    "browser",
    "navigate",
    "browse",
    "website",
    "web page",
    "webpage",
    "web search",
    "search the web",
    "look up online",
    "research online",
    "google",
    "bing",
    "visit",
)


# ---------------------------------------------------------------------------
# MCP resolution - bind a step to a tool discovered from an MCP server.
# ---------------------------------------------------------------------------
# Tokens too generic to identify a specific tool.
_MCP_STOPWORDS = {"get", "the", "and", "for", "web", "run", "list", "set"}

# Cache of tool-description embeddings so we embed each tool at most once.
_TOOL_VEC_CACHE: dict[str, list[float]] = {}
# Minimum cosine similarity for a semantic tool binding.
_MCP_SEMANTIC_THRESHOLD = 0.35


def _tool_text(tool: dict[str, Any]) -> str:
    return f"{tool.get('name', '')}: {tool.get('description', '')}".strip()


def _resolve_mcp_semantic(tools: list[dict[str, Any]], step, action, inputs=None):
    """Bind a step to a tool by embedding similarity (#9), or None if unavailable."""
    import embeddings

    if not tools or not embeddings.is_available():
        return None
    query = f"{step.get('name', '')} {step.get('description', '')} {action}"
    query_vec = embeddings.embed_one(query)
    if not query_vec:
        return None

    missing = [t for t in tools if _tool_text(t) not in _TOOL_VEC_CACHE]
    if missing:
        vectors = embeddings.embed([_tool_text(t) for t in missing])
        if not vectors:
            return None
        for tool, vec in zip(missing, vectors):
            _TOOL_VEC_CACHE[_tool_text(tool)] = vec

    best = None
    best_score = 0.0
    for tool in tools:
        score = embeddings.cosine(query_vec, _TOOL_VEC_CACHE[_tool_text(tool)])
        if score > best_score:
            best_score = score
            best = tool
    if best and best_score >= _MCP_SEMANTIC_THRESHOLD:
        name = best["name"]
        return best["server"], name, _mcp_args(name, step, action, inputs)
    return None


def _mcp_args(tool_name: str, step: dict[str, Any], action: str, inputs=None) -> dict[str, Any]:
    """Best-effort arguments for a matched MCP tool (prototype heuristic)."""
    name = tool_name.lower()
    context = f"{step.get('name', '')} {step.get('description', '')} {action}"
    if "stock" in name or "price" in name:
        return {"symbol": _symbol_from(inputs, context)}
    if "expense" in name and "submit" in name:
        return {"total": 0.0, "cost_center": "CC-1000"}
    return {}


def _resolve_mcp(step: dict[str, Any], action: str, inputs=None):
    """Return ``(server, tool, args)`` for the best matching MCP tool, or None.

    Prefers semantic (embeddings) matching when available (#9), else falls back
    to token scoring: count distinctive tool-name tokens present in the step and
    require at least two to avoid over-eager single-word matches.
    """
    try:
        import mcp_client
    except Exception:
        return None
    if not mcp_client.is_available():
        return None

    tools = mcp_client.available_tools()
    semantic = _resolve_mcp_semantic(tools, step, action, inputs)
    if semantic is not None:
        return semantic

    blob = f"{step.get('name', '')} {step.get('description', '')} {action}".lower()
    best = None
    best_score = 0
    for tool in tools:
        name = tool.get("name") or ""
        tokens = {
            t
            for t in re.split(r"[_\s]+", name.lower())
            if len(t) >= 4 and t not in _MCP_STOPWORDS
        }
        score = sum(1 for t in tokens if t in blob)
        if score > best_score:
            best_score = score
            best = tool
    if best and best_score >= 2:
        name = best["name"]
        return best["server"], name, _mcp_args(name, step, action, inputs)
    return None


# Verbs that mark an irreversible / high-consequence step. Such steps are never
# auto-approved by the skill, even when mature (see the executor's gate).
_HIGH_RISK_KEYWORDS = (
    "submit",
    "send",
    "email",
    "pay",
    "payment",
    "transfer",
    "wire",
    "purchase",
    "order",
    "delete",
    "remove",
    "cancel",
    "refund",
    "approve",
    "post to",
    "publish",
    # Shell / command execution is consequential: always keep it human-gated.
    "run command",
    "shell command",
    "powershell",
    "terminal command",
    "run script",
    "invoke script",
    "pip install",
    "npm install",
    "install package",
)


def is_high_risk(step: dict[str, Any]) -> bool:
    """True if a step is irreversible/high-consequence and must stay human-gated."""
    if step.get("human_required"):
        return True
    blob = f"{step.get('name', '')} {step.get('description', '')}".lower()
    return any(k in blob for k in _HIGH_RISK_KEYWORDS)


def classify_execution(result: str) -> str:
    """Grade a tool result: ``success``, ``failed`` (real tool errored / degraded
    to a mock), or ``manual`` (no tool bound). Used to demote-on-failure so a
    step whose tool keeps breaking never runs unsupervised.
    """
    text = (result or "").lstrip()
    if text.startswith("[mock]"):
        return "failed"
    if text.startswith("[manual]"):
        return "manual"
    return "success"


def _run_binding(binding, step: dict[str, Any], action: str, inputs) -> str:
    """Execute a structured tool decision (an MCP tool or a built-in), honestly."""
    tool = (binding.tool or "").strip()
    low = tool.lower()
    try:
        args = json.loads(binding.args_json) if binding.args_json else {}
        if not isinstance(args, dict):
            args = {}
    except Exception:
        args = {}

    if low in ("", "none", "manual"):
        return (
            f"[manual] No automated tool is needed for "
            f"'{step.get('name', 'this step')}'. Recorded as a manual step."
        )

    # Normalise stock-price args: the model may resolve the entity (e.g. Visa ->
    # V) but under any key (symbol/ticker/query); ensure the resolved ticker is
    # present under "symbol" so the tool always receives it.
    is_stock = "stock" in low or "price" in low or "quote" in low
    if is_stock:
        raw = (
            args.get("symbol") or args.get("ticker") or args.get("query")
            or args.get("company") or args.get("name") or ""
        )
        resolved = resolve_ticker(raw) if raw else _symbol_from(inputs, f"{step} {action}")
        args = {**args, "symbol": resolved}

    # An MCP tool chosen by name.
    try:
        import mcp_client

        if mcp_client.is_available():
            for t in mcp_client.available_tools():
                if t.get("name") == tool:
                    server = binding.server or t.get("server")
                    out = mcp_client.call_tool(server, tool, args)
                    return f"[mcp:{server}/{tool}] {out}"
    except Exception:
        pass

    # Built-ins.
    if is_stock:
        return get_stock_price(args.get("symbol") or "MSFT")
    if "browse" in low or "web" in low or "search" in low:
        target = args.get("target") or args.get("url") or args.get("query") or ""
        return browse_web(target or _browse_query(step, action))

    return (
        f"[manual] Requested tool '{tool}' is not available here. "
        "Recorded as a manual step."
    )


def execute_step(
    step: dict[str, Any],
    action: str,
    inputs: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> str:
    """Dispatch a drafted action to the appropriate tool.

    When a live model is available, a structured tool binding (LLM emits the
    tool name + typed arguments) is authoritative — no argument guessing. The
    heuristic resolver (MCP token/embedding match -> web browse -> built-ins)
    is the offline fallback. ``inputs`` are the run's parameters.
    """
    context = f"{step.get('name', '')} {step.get('description', '')} {action}"
    blob = context.lower()

    # 0. Governed shell / script execution (opt-in via SHELL_TOOL_ENABLED). An
    #    explicit command or script invocation wins over fuzzy tool matching.
    try:
        import shell

        shell_out = shell.try_execute(step, action)
        if shell_out is not None:
            return shell_out
    except Exception:
        pass  # never let the shell tool break the executor

    # 1. Structured tool binding: the model chooses the tool and its arguments.
    try:
        import tool_binding
        import mcp_client

        catalog = mcp_client.available_tools() if mcp_client.is_available() else []
        binding = tool_binding.bind_tool(step, action, inputs, catalog, model)
    except Exception:
        binding = None
    if binding is not None:
        return _run_binding(binding, step, action, inputs)

    # 2. Heuristic fallback (offline): an MCP capability that matches the step.
    resolved = _resolve_mcp(step, action, inputs)
    if resolved:
        server, tool, args = resolved
        try:
            import mcp_client

            out = mcp_client.call_tool(server, tool, args)
            return f"[mcp:{server}/{tool}] {out}"
        except Exception:
            pass  # fall through to built-in tools

    # 3. Web navigation via Playwright.
    if any(k in blob for k in _BROWSE_KEYWORDS):
        return browse_web(_browse_query(step, action))

    # 4. Built-in tools.
    if any(k in blob for k in ("stock", "price", "ticker", "quote")):
        return get_stock_price(_symbol_from(inputs, context))

    if any(k in blob for k in ("search", "web")):
        return browse_web(_browse_query(step, action))

    # 5. No tool is bound to this step. Be honest rather than faking success:
    # record it as a manual step so a human still owns it.
    return (
        f"[manual] No automated tool is bound to '{step.get('name', 'this step')}'. "
        "Recorded as a manual step; bind an MCP tool or Function to automate it."
    )
