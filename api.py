import os
from fastapi import FastAPI
from fastapi import WebSocket, WebSocketDisconnect
import uvicorn
import asyncio
import base64
from recallai import RecallAI
from pyngrok import ngrok
from openai import OpenAIRealtime
from pydub import AudioSegment
import io
import time

# Initialize FastAPI app
app = FastAPI()
recallai = RecallAI()
oai_realtime_ws = OpenAIRealtime()

# Global variable to store audio data
audio_buffer = ''

def convert_audio_to_mp3(audio_data):
    """
    Convert base64-encoded PCM16 audio to MP3 format with lower pitch and slower speed.
    """
    # Decode the base64-encoded PCM16 audio
    audio_bytes = base64.b64decode(audio_data)
    
    # Convert from PCM16 to MP3 with appropriate settings
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="raw", frame_rate=48000, channels=1, sample_width=2)
    audio = audio.set_frame_rate(16000).set_channels(1)  # Adjust frame rate to slow down audio
    audio = audio._spawn(audio.raw_data, overrides={'frame_rate': int(audio.frame_rate * 0.5)})  # Further slow down and lower pitch
    audio = audio.set_frame_rate(11025)  # Set final frame rate to make it even slower and lower pitch
    
    # Export the audio as MP3
    mp3_buffer = io.BytesIO()
    audio.export(mp3_buffer, format="mp3")
    
    # Get base64-encoded MP3 data
    mp3_data = base64.b64encode(mp3_buffer.getvalue()).decode('utf-8')
    
    return mp3_data

async def realtime_message_handler(message):
    """
    Handle real-time messages from the OpenAI WebSocket.
    """
    global audio_buffer
    print()
    if message.get("type") == "response.audio.delta":
        content = message.get("delta", None) # str of base64 audio data
        if content is not None:
            audio_buffer += content
    elif message.get("type") == "response.audio.done":
        print(f"Sending audio to bot {recallai.id}. Base64 audio size: {len(audio_buffer)} characters")
        
        start = time.time()
        converted_audio = convert_audio_to_mp3(audio_buffer)
        end = time.time()
        print(f"Time taken to convert audio: {end - start} seconds")
        
        result = recallai.output_audio(converted_audio)
        print(f"Result from output_audio: {result}")
        audio_buffer = ''

@app.on_event("startup")
async def startup_event():
    """
    Initialize the Recall.ai bot when the FastAPI app starts.
    """
    global recallai
    meeting_url = os.getenv('ZOOM_MEETING_URL')
    recallai.create(meeting_url)
    print(f'Recall.ai Bot ID: {recallai.id}')

@app.websocket("/audio")
async def audio_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for handling audio data.
    """
    await websocket.accept()

    if not oai_realtime_ws.ws or not oai_realtime_ws.ws.open:
        print("Realtime WebSocket not connected, connecting...")
        await oai_realtime_ws.connect()

    # Create a partial function with the websocket parameter
    handler = lambda message: realtime_message_handler(message)
    
    # Start receiving messages from RealtimeWebSocket in the background
    receive_task = asyncio.create_task(oai_realtime_ws.receive_messages(handler))

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            elif message["type"] == "websocket.receive":
                if "bytes" in message:
                    audio_data = message["bytes"]
                    base64_audio = base64.b64encode(audio_data).decode('utf-8')
                    await oai_realtime_ws.send_audio(base64_audio)
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    finally:
        print("WebSocket connection closed")
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    # Create an ngrok tunnel on port 8080
    http_tunnel = ngrok.connect(8080)
    print(f"Webhook URL: {http_tunnel.public_url}")
    os.environ['WEBHOOK_URL'] = http_tunnel.public_url

    try:
        # Run the FastAPI app
        uvicorn.run("api:app", host="0.0.0.0", port=8080, reload=True)
    finally:
        # Ensure the ngrok tunnel is closed when the application exits
        ngrok.disconnect(http_tunnel.public_url)
        
        # Remove the bot from Recall.ai
        recallai.remove()