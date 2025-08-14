"""Web search tool for LLM."""

import json
from typing import Any, Dict
from urllib.parse import quote_plus

import aiohttp

from ..config import HashConfig
from .base import Tool


class WebSearchTool(Tool):
    """Tool for performing web searches."""

    def get_name(self) -> str:
        return "web_search"

    def get_description(self) -> str:
        return "Search the web for current information"

    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Perform a web search and return results."""

        query = arguments.get("query", "")
        num_results = arguments.get("num_results", 5)

        if not query:
            return "No search query provided"

        # For now, return a placeholder since we don't have a search API configured
        # In a production system, you would integrate with search APIs like:
        # - DuckDuckGo API
        # - Google Custom Search API
        # - Bing Search API
        # - SearxNG instance

        return f"""Web search functionality not yet implemented.

Query: {query}
Requested results: {num_results}

To implement this tool, you would need to:
1. Choose a search API (DuckDuckGo, Google Custom Search, etc.)
2. Add API credentials to configuration
3. Implement the search request and response parsing
4. Format results for the LLM

For now, you can manually search for '{query}' and provide the information."""

    async def _search_duckduckgo(self, query: str, num_results: int = 5) -> str:
        """Example implementation using DuckDuckGo (requires API setup)."""
        try:
            # This is a placeholder - DuckDuckGo doesn't have a free API
            # You would need to use their HTML scraping (not recommended)
            # or use alternative search APIs

            async with aiohttp.ClientSession() as session:
                url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
                async with session.get(url) as response:
                    data = await response.json()

                    # Format results
                    results = []
                    if "AbstractText" in data and data["AbstractText"]:
                        results.append(f"Summary: {data['AbstractText']}")

                    if "RelatedTopics" in data:
                        for i, topic in enumerate(data["RelatedTopics"][:num_results]):
                            if "Text" in topic:
                                results.append(f"{i+1}. {topic['Text']}")

                    if not results:
                        return f"No results found for: {query}"

                    return f"Search results for '{query}':\\n\\n" + "\\n\\n".join(
                        results
                    )

        except Exception as e:
            return f"Error performing web search: {e}"

    def requires_confirmation(self) -> bool:
        """Web searches don't require confirmation."""
        return False
