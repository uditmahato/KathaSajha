# KathaSajha - AI Story Generator

KathaSajha (कथा साझा) is a Flask-based web application that generates illustrated children's stories from user prompts. Users input a story idea, and the app creates a multi-paragraph tale with AI-generated images, downloadable as a PDF. The frontend features a parchment-themed UI with Tailwind CSS, custom fonts, and client-side PDF generation, while the Flask backend handles story and image generation.

## Features

- **Story Generation**: Enter a prompt to generate a unique story with multiple paragraphs.
- **Illustrations**: Each paragraph is paired with an AI-generated image (or a default image if generation fails).
- **PDF Download**: Export stories and images as a formatted PDF using `html2pdf.js`.
- **Responsive Design**: Mobile-friendly layout with a vintage book aesthetic.
- **Sample Prompt**: Pre-fill with an example prompt ("Leo the Lion and Lily the Lost Girl") for testing.
- **Custom Styling**: Uses Tailwind CSS and custom CSS for spinner, image-text layout, and PDF formatting.

## Project Structure

```
kathasajha/
├── app.py                # Flask backend with /generate endpoint
├── templates/
│   └── index.html        # Frontend HTML with JS and Tailwind CSS
├── static/
│   └── css/
│       └── styles.css    # Custom CSS for spinner, story layout, and print styles
├── .env                  # Environment variables (e.g., AI API keys)
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
```

## Setup

### Prerequisites

- **Python 3.8+**: For running the Flask backend.
- **Node.js**: Optional, for local development with tools like `http-server`.
- **Web Browser**: Chrome, Firefox, or Safari recommended.
- **Internet Connection**: For CDN dependencies (Tailwind, Font Awesome, etc.) and AI API calls.

### Installation

1. **Clone the Repository**:
   ```bash
   git clone <repository-url>
   cd kathasajha
   ```

2. **Set Up a Virtual Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   Example `requirements.txt`:
   ```
   flask==2.3.3
   python-dotenv==1.0.0
   requests==2.31.0  # For AI API calls, adjust based on app.py
   ```

4. **Create `.env`**:
   - Create a `.env` file in the project root.
   - Add API keys for AI services  **Required** AI services (e.g., OpenAI, DALL·E, or equivalent).
   Example `.env`:
   ```
   FLASK_ENV=development
   OPENAI_API_KEY=your_openai_key
   IMAGE_API_KEY=your_image_gen_key
   ```

5. **Run the Flask App**:
   ```bash
   flask run
   ```
   - Access the app at `http://localhost:5000`.

6. **Frontend Dependencies** (loaded via CDN in `index.html`):
   - Tailwind CSS: `https://cdn.tailwindcss.com`
   - Font Awesome: `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css`
   - Google Fonts: Merriweather and Playfair Display
   - html2pdf.js: `https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js`

## Usage

1. **Open the App**:
   - Navigate to `http://localhost:5000` in your browser.

2. **Enter a Prompt**:
   - In the "Your Story Idea" textarea, enter a story idea (e.g., "Leo the Lion and Lily the Lost Girl").
   - Click "Show Example" to load a sample prompt.

3. **Generate Story**:
   - Click "Generate Story" to send the prompt to the `/generate` endpoint.
   - A loading spinner appears while the story and images are generated.

4. **View and Download**:
   - The generated story appears with each paragraph paired with an image.
   - Click "Save Story" to download a PDF.

## Technical Details

### Backend (`app.py`)

- **Flask App**:
  - Handles the `/generate` POST endpoint, accepting `{ prompt: "story idea" }`.
  - Uses AI services to generate story text and images.
  - Returns JSON:
    ```json
    {
      "title": "Story Title",
      "story": "Paragraph 1\n\nParagraph 2\n\n...",
      "images": ["base64_image_1", "base64_image_2", ...]
    }
    ```
- **Assumption**: Integrates with AI APIs (e.g., OpenAI for text, DALL·E for images).

### Frontend (`templates/index.html`)

- **HTML Structure**:
  - Header with branding.
  - Main section with story generator form and story display.
  - Footer with social media links and copyright.
- **JavaScript**:
  - Handles form submission, API calls, and story rendering.
  - Uses `fetch` to call `/generate`.
  - Renders paragraphs with images, using a default image for missing/invalid images.
  - Generates PDFs with `html2pdf.js` (letter-sized, portrait).
- **Image Handling**:
  - Each paragraph has an image from the `images` array.
  - Uses a default base64 image if an image is missing or invalid (e.g., `BLOCKED:` or `ERROR:`).
- **Styling**:
  - Tailwind CSS with custom colors (`parchment`, `sepia`, `oldgold`), fonts, and shadows.
  - Custom CSS in `static/css/styles.css` for spinner, image-text layout, and print styles.

### CSS (`static/css/styles.css`)

- Defines styles for:
  - Loading spinner animation.
  - Story paragraph sections (image above text, centered).
  - Image containers (max-width 350px, shadows).
  - Print media rules to prevent page breaks within image-text pairs.

### Known Issues and Fixes

- **Issue**: Some PDF paragraphs had "No image available" placeholders.
  - **Fix**: Updated `index.html` to use a default base64 image for missing/invalid images, ensuring every paragraph has an image.
- **Recommendation**: Update `app.py` to generate images for all paragraphs to avoid relying on the default image.

### Default Image

- Current default: 1x1 grey pixel for demonstration:
  ```javascript
  const defaultImage = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC9gF0Y3X9NgAAAABJRU5ErkJggg==';
  ```
- **Action**: Replace with a base64-encoded jungle-themed image. Use an online tool or Python script to convert an image to base64.

## Development Notes

- **Server-Side**:
  - Ensure `app.py` generates images for all paragraphs.
  - Handle AI API errors (e.g., rate limits, content violations) with retries or fallback images.
- **Testing**:
  - Test with prompts like "Leo the Lion and Lily the Lost Girl".
  - Verify PDF output for consistent images and page breaks.
- **Improvements**:
  - Add a "Regenerate Images" button.
  - Show progress in the loading indicator (e.g., "Generating image 3 of 5").
  - Validate prompt length/content client-side.

## Contributing

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/your-feature`).
3. Commit changes (`git commit -m "Add your feature"`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a pull request.

## License

© 2025 KathaSajha. All rights reserved.

## Contact

For questions or feedback, create an issue in the repository.