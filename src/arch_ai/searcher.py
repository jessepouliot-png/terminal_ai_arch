import os
import sys
import logging
import httpx
import asyncio
import re
import json
import urllib.parse
from typing import List, Dict, Optional
from googlesearch import search
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.markup import escape
from rich import box
from arch_ai.config import Config, BORDERLESS_BOX
from arch_ai.response_utils import extract_full_model_response



_RE_SCRIPTS_STYLES = re.compile(r"<(script|style|nav|footer|header).*?>.*?</\1>", flags=re.DOTALL | re.IGNORECASE)
_RE_HTML_TAGS = re.compile(r"<.*?>")
_RE_WHITESPACE = re.compile(r"\s+")
_RE_TITLE = re.compile(r"<title>(.*?)</title>", flags=re.IGNORECASE)


log = logging.getLogger("agent_terminal")

class WebSearcher:
    def __init__(self, model=None):
        self.console = Console()
        self.model = model
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def _extract_content(self, html: str) -> str:
        """Extracts and cleans text from HTML."""
        # Remove scripts, styles, and navigation elements that usually contain noise
        html = _RE_SCRIPTS_STYLES.sub("", html)
        text = _RE_HTML_TAGS.sub(" ", html)
        text = _RE_WHITESPACE.sub(" ", text).strip()
        return text[:2500] # Increased limit for more detail

    async def fetch_source(self, url: str, client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, str]]:
        """Fetches a single URL asynchronously and returns its content."""
        try:
            if client is not None:
                response = await client.get(url)
                if response.status_code == 200:
                    return {
                        "url": url,
                        "content": self._extract_content(response.text),
                        "title": self._get_title(response.text)
                    }
            else:
                async with httpx.AsyncClient(headers=self.headers, timeout=12.0) as local_client:
                    response = await local_client.get(url)
                    if response.status_code == 200:
                        return {
                            "url": url,
                            "content": self._extract_content(response.text),
                            "title": self._get_title(response.text)
                        }
        except Exception as e:
            log.warning(f"Failed to fetch {url}: {e}")
        return None

    def _get_title(self, html: str) -> str:
        match = _RE_TITLE.search(html)
        return match.group(1).strip() if match else "Unknown Title"

    async def _tavily_search(self, query: str, num_results: int = 3) -> List[Dict[str, str]]:
        """Performs a search using the Tavily API (Stable & Structured)."""
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            log.warning("Tavily API key not configured. Falling back to scraping.")
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": num_results
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    for r in data.get("results", []):
                        results.append({
                            "url": r.get("url"),
                            "title": r.get("title", "No Title"),
                            "content": r.get("content", "No Content")
                        })
                    return results
                else:
                    log.error(f"Tavily API error: {response.status_code} - {response.text}")
        except Exception as e:
            log.error(f"Tavily search failed: {e}")
        return []

    async def search_and_analyze(self, query: str, num_results: int = 3) -> Dict:
        """The core search process: API -> Custom Scraper -> Fallback -> Analyze."""
        results = {"query": query, "sources": [], "summary": ""}
        
        # 1. Primary: Stable Search API (Tavily)
        log.info(f"Initiating stable API search for: {query}")
        results["sources"] = await self._tavily_search(query, num_results)

        # 2. Secondary: Fallback to scraping if API failed or returned no results
        if not results["sources"]:
            log.info("API search returned no results. Falling back to scraping...")
            found_urls = []
            
            # Custom Google Scraper
            try:
                # Offload blocking googlesearch generator to thread pool
                found_urls = await asyncio.to_thread(lambda: list(search(query, num_results=num_results)))
            except Exception as e:
                log.warning(f"Scraper fallback failed: {e}")

            # Fetch scraped URLs in parallel using a shared AsyncClient for connection pooling
            if found_urls:
                async with httpx.AsyncClient(headers=self.headers, timeout=12.0) as client:
                    tasks = [self.fetch_source(url, client=client) for url in found_urls]
                    fetched_results = await asyncio.gather(*tasks)
                    for data in fetched_results:
                        if data:
                            results["sources"].append(data)

        if not results["sources"]:
            return {"error": "No content could be retrieved. Check your internet connection or API keys."}

        # AI Synthesis
        if self.model:
            context = "\n\n".join([f"SOURCE: {s['url']}\nTITLE: {s['title']}\nCONTENT: {s['content']}" for s in results["sources"]])
            prompt = (
                f"Analyze the following web search results for the query: '{query}'\n\n"
                f"{context}\n\n"
                "Provide a detailed, technical report. "
                "Include a 'Key Findings' section and a 'Recommended Steps' section. "
                "Ensure you reference the sources provided."
            )
            try:
                response = await self.model.aio.models.generate_content(
                    model=Config.MODEL_NAME,
                    contents=prompt
                )
                summary = extract_full_model_response(response, include_function_calls=False)
                results["summary"] = summary if summary else "No summary generated."
            except (ValueError, AttributeError):
                results["summary"] = "AI returned a non-text summary. Please review search sources manually."
            except Exception as e:
                results["summary"] = f"AI Analysis failed: {e}"
        
        return results

    def display_results(self, results: Dict):
        """Displays the detailed search results in a production-grade UI."""
        if "error" in results:
            self.console.print(f"[bold red]Search Error:[/bold red] {escape(str(results['error']))}")
            return

        table = Table(title=f"Search Results: {results['query']}", box=BORDERLESS_BOX)
        table.add_column("Title", style="cyan", no_wrap=False)
        table.add_column("Source", style="dim")

        for s in results["sources"]:
            table.add_row(s["title"], s["url"])

        self.console.print(table)
        if results["summary"]:
            self.console.print(Panel(Markdown(results["summary"]), title="[AI SYNTHESIS]", border_style="green", box=BORDERLESS_BOX))


def main():
    import os
    from google import genai
    
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "latest linux kernel version"
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    searcher = WebSearcher(model=client)
    with Console().status("[bold blue]Running deep search...", spinner="dots"):
        results = asyncio.run(searcher.search_and_analyze(query))
    searcher.display_results(results)


if __name__ == "__main__":
    main()
