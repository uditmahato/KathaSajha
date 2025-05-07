from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import google.generativeai as genai
import os
from dotenv import load_dotenv
import base64
import logging
import asyncio

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for frontend-backend communication

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY not found in .env file. Please ensure it's set.")
    # It's better to raise an error here if the key is mandatory for the app to function
    # raise ValueError("GOOGLE_API_KEY not found in .env file")
    # However, if you want the app to start but fail gracefully later, you could pass

# Configure Gemini APIs
story_model = None
image_model = None
try:
    if GOOGLE_API_KEY: # Only configure if key was loaded
        genai.configure(api_key=GOOGLE_API_KEY)
        # Use a model capable of text generation for the story
        story_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        # Use the experimental image generation model.
        # IMPORTANT: This model 'gemini-2.0-flash-exp-image-generation'
        # is *likely* not designed to output image data (like base64)
        # via generate_content. It's more likely to return text *descriptions*
        # suitable for image generation or understand images.
        # If you need actual image data, you'll need to use a dedicated
        # image generation API like Vertex AI's Imagen.
        image_model = genai.GenerativeModel('gemini-2.0-flash-exp-image-generation')
        logger.info("Gemini models configured successfully.")
    else:
        logger.warning("GOOGLE_API_KEY not found. Gemini models not configured.")

except Exception as e:
    logger.error(f"Failed to configure Gemini: {str(e)}")
    # Models remain None


@app.route('/')
def index():
    return render_template('index.html')

# Ensure the route is async because the API calls are async
@app.route('/generate', methods=['POST'])
async def generate():
    # Check if models were initialized successfully
    if not story_model or not image_model:
         logger.error("Backend AI models not initialized due to missing API key or configuration error.")
         return jsonify({'error': 'Backend AI models not initialized. Please check server logs.'}), 500

    try:
        data = request.get_json()
        prompt = data.get('prompt')
        if not prompt:
            logger.warning("Prompt not provided in request.")
            return jsonify({'error': 'Prompt is required'}), 400

        logger.info(f"Received prompt: {prompt}")

        # --- Story Generation ---
        story_generation_prompt = f"Write a short story (around 300-400 words) based on this prompt: {prompt}. The story should have a clear title as the very first line, starting with '# ' (e.g., '# The Lost Kitten'). Ensure the story is engaging and creative."
        logger.info(f"Generating story...")
        # (Story generation code remains the same)
        try:
            story_response = await story_model.generate_content_async(story_generation_prompt)
            # ... (story processing logic) ...
            story_text = story_response.text # Example of getting story text
            lines = story_text.split('\n')
            title = 'Untitled Story'
            story_body_lines = []
            if lines and lines[0].strip().startswith('# '):
                title = lines[0].strip('# ').strip()
                story_body_lines = lines[1:]
            else:
                story_body_lines = lines
                logger.warning("No title found in the generated story, using 'Untitled Story'.")
            story_body = '\n'.join(story_body_lines).strip()
            logger.info("Story generated successfully.")

        except Exception as story_err:
             logger.error(f"Error during story generation: {str(story_err)}", exc_info=True)
             if isinstance(story_err, genai.types.BlockedPromptException):
                  safety_details = getattr(story_err.response, 'prompt_feedback', None)
                  logger.error(f"Story generation blocked: {safety_details}")
                  return jsonify({'error': f'Story generation blocked.', 'details': str(story_err), 'safety_feedback': str(safety_details)}), 400
             else:
                  return jsonify({'error': f'An error occurred during story generation: {str(story_err)}'}), 500


        # --- Image Generation (Using gemini-2.0-flash-exp-image-generation with IMAGE modality) ---
        # Based on documentation, this model *can* return inline_data (images)
        # if response_modalities=['TEXT', 'IMAGE'] is set.
        image_generation_prompt = f"Generate one illustration as an image for a children's story titled '{title}' about: '{prompt}'. Focus on a key scene or character. The image should be suitable for a storybook."
        logger.info(f"Attempting image generation with IMAGE modality...")

        images_base64 = []
        image_description = None # Still useful for fallback or accompanying text

        try:
            image_response = await image_model.generate_content_async(
                [image_generation_prompt],
                 generation_config={
                    "candidate_count": 1, # Request one candidate
                    # *** ADD THIS LINE BASED ON DOCS ***
                    "response_modalities": ["TEXT", "IMAGE"]
                 }
            )
            logger.info(f"Image response received: {image_response}")

            if image_response and image_response.parts:
                logger.info("Processing image response parts...")
                for i, part in enumerate(image_response.parts):
                    logger.info(f"Processing image response part {i}: {part}")
                    if part.inline_data:
                        # Check if it's image data
                        if part.inline_data.mime_type.startswith('image/'):
                            try:
                                # Extract and base64 encode the data
                                images_base64.append(base64.b64encode(part.inline_data.data).decode('utf-8'))
                                logger.info(f"Image data found and extracted from part {i}.")
                                # No need to set image_description here if image is found,
                                # unless the model also provides text alongside the image.
                                # break # Stop after finding an image if you only need one
                            except Exception as decode_error:
                                 logger.error(f"Error decoding image data from part {i}: {str(decode_error)}")
                                 # Optionally add an error to the description
                                 image_description = (image_description or "") + f" (Decode error in part {i}: {str(decode_error)})"
                                 continue # Try next part
                        else:
                            logger.warning(f"Inline data found in part {i}, but not an image mime type: {part.inline_data.mime_type}")
                            image_description = (image_description or "") + f" (Non-image inline data in part {i}: {part.inline_data.mime_type})"

                    elif part.text:
                        # The model might provide text alongside the image, or only text sometimes.
                        logger.info(f"Text content received from part {i}: {part.text}")
                        # Append text content to description, or use as main description if no image is found
                        if not images_base64: # Only use as main description if no image was found yet
                             image_description = part.text
                        else: # If image(s) found, maybe append text as caption/detail
                             image_description = (image_description or "Accompanying text:") + f"\nPart {i} text: {part.text}"

                    else:
                        logger.warning(f"Unexpected part type in image response from part {i}: {part}")

            # If no images were found after checking all parts
            if not images_base64:
                 logger.warning("No image parts found in the response.")
                 # Ensure image_description has a useful message if no image was found
                 if image_description is None or image_description == "":
                     # Check if the failure was due to blocking feedback
                     if hasattr(image_response, 'prompt_feedback') and image_response.prompt_feedback:
                         logger.error(f"Image generation blocked. Prompt feedback: {image_response.prompt_feedback}")
                         image_description = f"Image generation blocked by safety filters. Feedback: {image_response.prompt_feedback}"
                     else:
                         logger.warning("No images or significant text description were successfully generated or extracted.")
                         image_description = "Could not generate image or a descriptive concept." # Default fallback if nothing useful came back

        except Exception as image_err:
            # This handles errors *during* the image generation API call itself
            logger.error(f"Error during image generation API call: {str(image_err)}", exc_info=True)
            images_base64 = [] # Ensure list is empty on error
            # Check if it's a known genai error type
            if isinstance(image_err, genai.types.BlockedPromptException):
                 safety_details = getattr(image_err.response, 'prompt_feedback', None)
                 logger.error(f"Image generation blocked: {safety_details}")
                 image_description = f'Image generation blocked. Safety feedback: {safety_details}'
            else:
                 image_description = f"Error requesting image API: {str(image_err)}"


        # --- Return Results ---
        # images_base64 will now potentially contain base64 strings if generation was successful
        # image_description will contain any accompanying text or an error/fallback message
        return jsonify({
            'title': title,
            'story': story_body,
            'images': images_base64, # This should now have data if generation works
            'image_description': image_description # This will have accompanying text or error/fallback
        })

    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"An unexpected error occurred during content generation process: {str(e)}", exc_info=True)
        if isinstance(e, genai.types.BlockedPromptException):
            safety_details = getattr(e.response, 'prompt_feedback', None)
            logger.error(f"Top-level request blocked: {safety_details}")
            return jsonify({'error': f'Content generation request blocked.', 'details': str(e), 'safety_feedback': str(safety_details)}), 400
        elif isinstance(e, genai.types.StopCandidateException):
            logger.error(f"Content generation stopped unexpectedly: {str(e)}")
            return jsonify({'error': f'Content generation stopped unexpectedly. Details: {str(e)}'}), 500
        else:
            return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

# Run the app
if __name__ == '__main__':
    # Use asyncio.run to properly run the async generate route if needed
    # (Flask's debug mode often handles this, but explicit is clearer)
    # For basic local development with debug=True, app.run is usually sufficient.
    # For production or complex setups, consider a proper ASGI server like uvicorn.
    logger.info("Starting Flask app...")
    app.run(debug=True, host='0.0.0.0', port=5000)