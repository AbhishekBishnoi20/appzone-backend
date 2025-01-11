import aiohttp
import json

async def upload_to_imgbb(base64_image: str) -> str:
    """
    Upload an image to ImgBB and return the URL.
    
    Args:
        base64_image (str): Base64 encoded image data
        
    Returns:
        str: URL of the uploaded image
    """
    API_KEY = "da7d88ed84c2d79632b805fa7c0d5045"
    url = "https://api.imgbb.com/1/upload"
    
    # Remove the "data:image/png;base64," prefix if present
    if "base64," in base64_image:
        base64_image = base64_image.split("base64,")[1]
    
    # Prepare the form data
    data = {
        "key": API_KEY,
        "image": base64_image,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["data"]["url"]
                else:
                    error_text = await response.text()
                    raise Exception(f"ImgBB upload failed: {error_text}")
    except Exception as e:
        print(f"Error uploading to ImgBB: {str(e)}")
        raise