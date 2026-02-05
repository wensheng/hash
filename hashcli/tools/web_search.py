"""Web search tool for LLM."""

import asyncio
from typing import Any, Dict

from ddgs import DDGS

from ..config import HashConfig
from .base import Tool


class WebSearchTool(Tool):
    """Tool for performing web searches using DuckDuckGo (via ddgs)."""

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

        try:
            # Run the synchronous ddgs search in a separate thread
            results = await asyncio.to_thread(self._perform_search, query, num_results)

            if not results:
                return f"No results found for: {query}"

            # Format results for the LLM
            formatted_results = [f"Search results for '{query}':\n"]
            for i, res in enumerate(results, 1):
                title = res.get("title", "No Title")
                href = res.get("href", "No URL")
                body = res.get("body", "No description available")
                formatted_results.append(f"{i}. {title}\n   URL: {href}\n   {body}\n")

            return "\n".join(formatted_results)

        except Exception as e:
            return f"Error performing web search: {str(e)}"

    def _perform_search(self, query: str, num_results: int) -> list[dict[str, Any]]:
        """Synchronous search execution to be run in a thread."""
        with DDGS() as ddgs:
            return ddgs.text(query, max_results=num_results)

    def requires_confirmation(self) -> bool:
        """Web searches don't require confirmation."""
        return False
