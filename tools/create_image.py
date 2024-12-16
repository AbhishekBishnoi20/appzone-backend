import aiohttp
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

def determine_size_format(size: str) -> str:
    """Determine the appropriate size format based on dimensions string (e.g., '1024x1024')."""
    width, height = map(int, size.split('x'))
    if width == height:  # 1:1 ratio
        return "1_1"
    elif width < height:  # 9:16 ratio
        return "9_16"
    else:  # 16:9 ratio
        return "16_9"

async def generate_image(prompt: str, size: str = "1024x1024"):
    url = "https://api.freeflux.ai/v1/images/generate"
    
    logger.info(f"Starting generate_image with size: {size}")
    size_format = determine_size_format(size)
    logger.info(f"Determined size format: {size_format}")
    
    # Headers based on the request
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "origin": "https://freeflux.ai",
        "referer": "https://freeflux.ai/",
        "sec-ch-ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36 Edg/131.0.0.0"
    }

    # Form data
    form_data = aiohttp.FormData()
    form_data.add_field("prompt", prompt)
    form_data.add_field("model", "flux_1_schnell")
    form_data.add_field("size", size_format)  # Use determined size format
    form_data.add_field("lora", "null")
    form_data.add_field("style", "no_style")
    form_data.add_field("color", "no_color")
    form_data.add_field("lighting", "no_lighting")
    form_data.add_field("composition", "null")

    async with aiohttp.ClientSession() as session:
        logger.info("Making API request...")
        async with session.post(url, headers=headers, data=form_data) as response:
            if response.status == 200:
                logger.info("Received 200 response from API")
                data = await response.json()
                result = data.get('result')
                logger.info("Successfully extracted image data")
                return result
            else:
                error_msg = f"Error: {response.status} - {await response.text()}"
                logger.error(error_msg)
                return error_msg

async def main():
    prompt = "https://i.ibb.co/hgcn6SK/28e2d0765f56.png"
    
    # Example usage with different sizes
    sizes = [
        "1024x1024",  # Will use 1_1
        "1024x1792",  # Will use 9_16
        "1792x1024"   # Will use 16_9
    ]
    
    for size in sizes:
        print(f"\nGenerating image {size}")
        image_data = await generate_image(prompt, size)
        if image_data and not image_data.startswith('Error'):
            print(f"Image data (base64) for {size}:", image_data)
        else:
            print(image_data)

if __name__ == "__main__":
    asyncio.run(main())
