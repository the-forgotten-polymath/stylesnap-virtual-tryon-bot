import os
import requests
import cv2
import numpy as np
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from gradio_client import Client as GradioClient, file
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
import uvicorn

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Mount the static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory storage for tracking sessions
user_sessions = {}

# Twilio credentials loaded from .env file
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Gradio Client for Nymbo Virtual Try-On API
gradio_client = GradioClient("Nymbo/Virtual-Try-On")

# Ngrok URL loaded from .env file
IMAGE_URL = os.getenv("IMAGE_URL")

@app.get('/', response_class=HTMLResponse)
def index():
    return "This is the virtual try-on chatbot API."

@app.post('/webhook')
async def webhook(request: Request):
    form = await request.form()
    sender_number = form.get('From')  # User's WhatsApp number
    media_url = form.get('MediaUrl0')  # URL of the media if image is sent

    # Log the media URL
    print(f"Received media URL: {media_url}")

    # Create a response object for Twilio
    resp = MessagingResponse()

    # If no image is received, inform the user
    if media_url is None:
        resp.message("We didn't receive an image. Please try sending your image again.")
        return Response(content=str(resp), media_type="application/xml")

    # Step 1: Check if person image is uploaded
    if sender_number not in user_sessions:
        user_sessions[sender_number] = {}
        if media_url:
            user_sessions[sender_number]['person_image'] = media_url
            resp.message("Great! Now please send the image of the garment you want to try on.")
        else:
            resp.message("Please send your image to begin the virtual try-on process.")
    # Step 2: Check if garment image is uploaded
    elif 'person_image' in user_sessions[sender_number] and 'garment_image' not in user_sessions[sender_number]:
        if media_url:
            user_sessions[sender_number]['garment_image'] = media_url
            # Now both images are collected, send them to the Gradio API for virtual try-on
            try_on_image_url = send_to_gradio(user_sessions[sender_number]['person_image'], media_url)
            if try_on_image_url:
                # Send the image as a WhatsApp media message
                send_media_message(sender_number, try_on_image_url)
                resp.message("Here is your virtual try-on result!")
            else:
                resp.message("Sorry, something went wrong with the try-on process.")
            # Clear session after completion
            del user_sessions[sender_number]
        else:
            resp.message("Please send the garment image to complete the process.")
    else:
        # If both images have already been received, start the process again
        resp.message("Please send your image to begin the virtual try-on process.")

    return Response(content=str(resp), media_type="application/xml")

def send_to_gradio(person_image_url, garment_image_url):
    # Download both images from Twilio
    person_image_path = download_image(person_image_url, 'person_image.jpg')
    garment_image_path = download_image(garment_image_url, 'garment_image.jpg')

    if person_image_path is None or garment_image_path is None:
        print("Error: One of the images could not be downloaded.")
        return None

    try:
        # Interact with the Gradio API using the client
        result = gradio_client.predict(
            dict={"background": file(person_image_path), "layers": [], "composite": None},
            garm_img=file(garment_image_path),
            garment_des="A cool description of the garment",
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=30,
            seed=42,
            api_name="/tryon"
        )

        # Log the result for debugging
        print(f"API result: {result}")

        # Check if the result is returned correctly
        if result and len(result) > 0:
            try_on_image_path = result[0]  # First item in result is the output image path
            print(f"Generated try-on image path: {try_on_image_path}")

            # Ensure the static directory exists
            static_dir = 'static'
            if not os.path.exists(static_dir):
                os.makedirs(static_dir)
                print(f"Created directory: {static_dir}")

            # Make sure the path exists
            if os.path.exists(try_on_image_path):
                # Convert the image to PNG format and save it
                img = cv2.imread(try_on_image_path)
                target_path_png = os.path.join(static_dir, 'result.png')
                cv2.imwrite(target_path_png, img)
                print(f"Image saved to: {target_path_png}")

                # Return the public URL for the image as PNG
                return f"{IMAGE_URL}/static/result.png"
            else:
                print(f"Image not found at: {try_on_image_path}")
                return None

        print("No image returned from the API.")
        return None

    except Exception as e:
        print(f"Error interacting with Gradio API: {e}")
        return None

def send_media_message(to_number, media_url):
    message = client.messages.create(
        from_='whatsapp:+14155238886',  # Twilio sandbox number
        body="Here is your virtual try-on result:",
        media_url=[media_url],  # Public URL of the media
        to=to_number
    )
    print(f"Sent media message to {to_number}. Message SID: {message.sid}")

def download_image(media_url, filename):
    try:
        # Extract Message SID and Media SID from the URL
        message_sid = media_url.split('/')[-3]
        media_sid = media_url.split('/')[-1]

        # Log the message and media SIDs
        print(f"Message SID: {message_sid}, Media SID: {media_sid}")

        # Use Twilio client to fetch the media resource
        media = client.api.accounts(TWILIO_ACCOUNT_SID).messages(message_sid).media(media_sid).fetch()

        # Construct the actual media URL
        media_uri = media.uri.replace('.json', '')
        image_url = f"https://api.twilio.com{media_uri}"

        # Log the full URL being used for the image download
        print(f"Downloading image from: {image_url}")

        # Download the image with proper authorization (using Basic Auth)
        response = requests.get(image_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        
        if response.status_code == 200:
            # Save the image locally
            with open(filename, 'wb') as f:
                f.write(response.content)
            print(f"Image downloaded successfully as {filename}.")
            return filename
        else:
            print(f"Failed to download image: {response.status_code}")
            return None
    except Exception as err:
        print(f"Error downloading image from Twilio: {err}")
        return None

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8080)
