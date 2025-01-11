import aiohttp
import asyncio
import json

async def retrieve_tool(url: str):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://r.jina.ai/{url}", headers={
                "Accept": "application/json",
                "X-With-Generated-Alt": "true"
            }) as response:
                json_data = await response.json()

                if not json_data.get("data") or len(json_data["data"]) == 0:
                    return "No search results found."

                data = json_data["data"]
                
                # Limit the content to 5000 characters
                if len(data["content"]) > 5000:
                    data["content"] = data["content"][:5000]

                # Convert to the similar format as tavily_search
                formatted_results = f"URL: {data.get('url', 'N/A')}\n"
                formatted_results += f"Title: {data.get('title', 'N/A')}\n"
                formatted_results += f"Content: {data.get('content', 'N/A')}\n"
                
                return formatted_results

        except Exception as error:
            error_message = f"An error occurred during the retrieve: {str(error)}"
            print(error_message)
            return error_message

# Example usage
async def main():
    url = "nike.com"
    results = await retrieve_tool(url)
    if results:
        print(json.dumps(results, indent=2))
    else:
        print(f'An error occurred while retrieving "{url}".')

if __name__ == "__main__":
    asyncio.run(main())