import asyncio
import aiohttp
import json
from datetime import datetime

currentDate = datetime.now().strftime("%d %B %Y")

async def tavily_search(query="write a poem on AI", max_results=10):
    api_key = "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"
    url = 'https://api.tavily.com/search'
    headers = {
        'Content-Type': 'application/json'
    }
    payload = {
        'api_key': api_key,
        'query': f"{query} {currentDate}",
        'max_results': max_results,
        'search_depth': 'advanced',
        'include_images': False,
        'include_answers': False
    }
    # print(payload)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, timeout=100) as response:
                data = await response.json()
                results = data.get('results', [])
                
                if not results:
                    return "No search results found."
                
                formatted_results = "Search Results:\n\n"
                for result in results:
                    formatted_results += f"URL: {result.get('url', 'N/A')}\n"
                    formatted_results += f"Title: {result.get('title', 'N/A')}\n"
                    formatted_results += f"Content: {result.get('content', 'N/A')}\n\n"
                # print(formatted_results)
                return formatted_results
        except Exception as e:
            error_message = f"An error occurred during the search: {str(e)}"
            print(error_message)
            return error_message


if __name__ == "__main__":
    asyncio.run(tavily_search())
