import asyncio
from tools.create_image import generate_image
import logging
import base64
from io import BytesIO
from PIL import Image

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def dalle_generate(prompt: str, size: str = "1024x1024"):
    """
    Generate images using the Flux AI API and optimize the output.
    
    Args:
        prompt (str): The image description/prompt
        size (str): Image size in format "WxH" (e.g., "1024x1024", "1024x1792", "1792x1024")
    
    Returns:
        str: Base64 image data with data URI scheme or error message
    """
    try:
        logger.info(f"Starting image generation with prompt: {prompt[:50]}...")
        
        # Validate size format
        try:
            width, height = map(int, size.split('x'))
            if size not in ["1024x1024", "1024x1792", "1792x1024"]:
                logger.warning(f"Warning: Using closest supported size format for {size}")
        except ValueError:
            logger.warning("Invalid size format. Using default 1024x1024")
            size = "1024x1024"

        # Generate the image
        logger.info("Calling generate_image function...")
        image_data = await generate_image(prompt, size)
        
        if image_data and not image_data.startswith('Error'):
            logger.info("Successfully generated image data, optimizing...")
            
            try:
                # If the image data contains a data URI scheme, remove it
                if image_data.startswith('data:image'):
                    # Extract the base64 part after the comma
                    image_data = image_data.split(',')[1]
                
                # Decode base64 to bytes
                image_bytes = base64.b64decode(image_data)
                original_size = len(image_bytes) / 1024  # Convert to KB
                logger.info(f"Original image size: {original_size:.2f} KB")
                
                # Open the image with PIL
                with Image.open(BytesIO(image_bytes)) as img:
                    # Convert to RGB (in case it's PNG with alpha channel)
                    if img.mode in ('RGBA', 'LA'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[-1])
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    # Save as JPEG with optimized settings
                    output_buffer = BytesIO()
                    img.save(output_buffer, format='JPEG', quality=85, optimize=True, progressive=True)
                    optimized_bytes = output_buffer.getvalue()
                    optimized_size = len(optimized_bytes) / 1024  # Convert to KB
                    size_reduction = ((original_size - optimized_size) / original_size) * 100
                    
                    logger.info(f"Optimized image size: {optimized_size:.2f} KB")
                    logger.info(f"Size reduction: {size_reduction:.1f}%")
                    
                    # Convert back to base64 and add data URI scheme
                    optimized_data = base64.b64encode(optimized_bytes).decode('utf-8')
                    return f"data:image/jpeg;base64,{optimized_data}"
            except Exception as e:
                logger.error(f"Error during image optimization: {str(e)}")
                logger.error("Falling back to original image data")
                return f"data:image/jpeg;base64,{image_data}"  # Return original data if optimization fails
        else:
            logger.error(f"Failed to generate image: {image_data}")
            return f"Image generation failed: {image_data}"

    except Exception as e:
        error_msg = f"Error in dalle_generate: {str(e)}"
        logger.error(error_msg)
        return error_msg

# Example usage (only when run directly)
if __name__ == "__main__":
    async def test():
        result = await dalle_generate(
            prompt="a girl in a purple top in a neon city",
            size="1024x1024"
        )
        print(result)

    asyncio.run(test())
