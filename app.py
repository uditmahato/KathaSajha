from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import google.generativeai as genai
import os
from dotenv import load_dotenv
import base64
import logging
import asyncio
from PIL import Image
from io import BytesIO
from asgiref.wsgi import WsgiToAsgi  # Import WsgiToAsgi

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
    raise ValueError("GOOGLE_API_KEY not found in .env file")

# Configure Gemini APIs
story_model = None
image_model = None
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    story_model = genai.GenerativeModel('gemini-1.5-flash-latest')
    image_model = genai.GenerativeModel('gemini-2.0-flash-exp-image-generation')
    logger.info("Gemini models configured successfully.")
except Exception as e:
    logger.error(f"Failed to configure Gemini: {str(e)}")
    raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
async def generate():
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
        story_generation_prompt = f"Write a short story (around 300-400 words) for kids aged 10-12 based on this prompt: {prompt}. The story should use simple words and clear sentences that are easy for young readers to understand. It should have a clear title as the very first line, starting with '# ' (e.g., '# The Lost Kitten'). Ensure the story is fun, exciting, and creative, with a positive message or lesson. Divide the story into 3-5 distinct paragraphs, each separated by a blank line, and each paragraph should describe a key moment or scene suitable for a colorful illustration in a children's book."
        logger.info(f"Generating story...")
        try:
            story_response = await story_model.generate_content_async(story_generation_prompt)
            story_text = story_response.text
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

        # --- Split Story into Paragraphs ---
        paragraphs = [p.strip() for p in story_body.split('\n\n') if p.strip()]
        logger.info(f"Story split into {len(paragraphs)} paragraphs.")

        # --- Image Generation for Each Paragraph ---
        images_base64 = []
        image_descriptions = []

        for idx, paragraph in enumerate(paragraphs):
            # Summarize paragraph for image prompt
            summary_prompt = f"Summarize the following paragraph into a brief description (1-2 sentences) suitable for generating an illustration for a children's storybook: {paragraph}"
            try:
                summary_response = await story_model.generate_content_async(summary_prompt)
                paragraph_summary = summary_response.text.strip()
            except Exception as e:
                logger.warning(f"Error summarizing paragraph {idx}: {str(e)}")
                paragraph_summary = paragraph[:100] + "..."  # Fallback to truncated paragraph

            image_generation_prompt = f"Generate one illustration as an image for a children's story titled '{title}' aimed at kids aged 10-12, based on: '{prompt}'. Focus on the following scene: {paragraph_summary}. The image should be colorful, fun, and suitable for a storybook."
            logger.info(f"Attempting image generation for paragraph {idx}...")

            image_description = None
            try:
                image_response = await image_model.generate_content_async(
                    [image_generation_prompt],
                    generation_config={
                        "candidate_count": 1,
                        "response_modalities": ["TEXT", "IMAGE"]
                    }
                )
                logger.info(f"Image response received for paragraph {idx}: {image_response}")

                if image_response and image_response.parts:
                    logger.info(f"Processing image response parts for paragraph {idx}...")
                    for i, part in enumerate(image_response.parts):
                        logger.info(f"Processing image response part {i} for paragraph {idx}: {part}")
                        if part.inline_data and part.inline_data.mime_type.startswith('image/'):
                            try:
                                images_base64.append(base64.b64encode(part.inline_data.data).decode('utf-8'))
                                logger.info(f"Image data found and extracted for paragraph {idx} from part {i}.")
                                break
                            except Exception as decode_error:
                                logger.error(f"Error processing image data for paragraph {idx} from part {i}: {str(decode_error)}")
                                image_description = f"Error processing image: {str(decode_error)}"
                                continue
                        elif part.text:
                            logger.info(f"Text content received for paragraph {idx} from part {i}: {part.text}")
                            image_description = part.text
                        else:
                            logger.warning(f"Unexpected part type in image response for paragraph {idx} from part {i}: {part}")
                else:
                    logger.warning(f"No image parts found in response for paragraph {idx}.")
                    if hasattr(image_response, 'prompt_feedback') and image_response.prompt_feedback:
                        logger.error(f"Image generation blocked for paragraph {idx}. Prompt feedback: {image_response.prompt_feedback}")
                        image_description = f"Image generation blocked by safety filters. Feedback: {image_response.prompt_feedback}"
                    else:
                        image_description = "Could not generate image for this paragraph."

                image_descriptions.append(image_description if image_description else "No description available.")

            except Exception as image_err:
                logger.error(f"Error during image generation for paragraph {idx}: {str(image_err)}", exc_info=True)
                if isinstance(image_err, genai.types.BlockedPromptException):
                    safety_details = getattr(image_err.response, 'prompt_feedback', None)
                    logger.error(f"Image generation blocked for paragraph {idx}: {safety_details}")
                    image_description = f'Image generation blocked. Safety feedback: {safety_details}'
                else:
                    image_description = f"Error requesting image API: {str(image_err)}"
                images_base64.append(None)  # Placeholder for failed image
                image_descriptions.append(image_description)

        # --- Return Results ---
        return jsonify({
            'title': title,
            'story': story_body,
            'paragraphs': paragraphs,
            'images': images_base64,
            'image_descriptions': image_descriptions
        })

    except Exception as e:
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

# Wrap Flask app with WsgiToAsgi for Uvicorn
asgi_app = WsgiToAsgi(app)

if __name__ == '__main__':
    logger.info("Starting Flask app with Uvicorn...")
    import uvicorn
    uvicorn.run(asgi_app, host='0.0.0.0', port=5000)