import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import threading
import uuid
import requests
import asyncio
import nest_asyncio
import gc
import json
import numpy as np

# 🟢 บรรทัดนี้แหละครับที่หายไป (พระเอกของเรา)
from flask import Flask, request, jsonify
app = Flask(__name__) 

# 🟢 ท่าไม้ตายแก้บั๊ก ANTIALIAS สำหรับ MoviePy
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

from moviepy.editor import *
import moviepy.video.fx.all as vfx

# AI & Media Libs
from moviepy.editor import *
import moviepy.video.fx.all as vfx
from PIL import Image, ImageDraw, ImageFont
import edge_tts

# Google Cloud
from google.cloud import storage
import datetime

nest_asyncio.apply()

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" # แก้ไขเป็น Webhook ของ n8n คุณตั้ม
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"

# 🚦 Queue Control
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# ☁️ Helper Functions (อัปโหลด, โหลดรูป, ทำเสียง, ทำตัวเลข)
# ---------------------------------------------------------
def get_gcs_client():
    gcs_json_content = os.environ.get("GCS_KEY_JSON")
    if gcs_json_content:
        try:
            info = json.loads(gcs_json_content)
            return storage.Client.from_service_account_info(info)
        except: return None
    elif os.path.exists(KEY_FILE_PATH):
        return storage.Client.from_service_account_json(KEY_FILE_PATH)
    return None

def upload_to_gcs(source_file_name):
    try:
        storage_client = get_gcs_client()
        if not storage_client: return None
        destination_blob_name = os.path.basename(source_file_name)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(source_file_name, timeout=300)
        url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12), method="GET")
        return url
    except Exception as e:
        print(f"❌ Upload Failed: {e}")
        return None

def download_image_from_url(url, filename):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            with open(filename, 'wb') as f: f.write(r.content)
            return True
    except: return False

async def create_voice_safe(text, filename):
    try:
        communicate = edge_tts.Communicate(text, "th-TH-NiwatNeural", rate="+0%")
        await communicate.save(filename)
    except Exception as e:
        print(f"Voice Error: {e}")

def get_font(fontsize):
    font_options = ["tahomabd.ttf", "tahoma.ttf", "Sarabun-Bold.ttf"]
    for font_p in font_options:
        if os.path.exists(font_p):
            return ImageFont.truetype(font_p, fontsize)
    return ImageFont.load_default()

def create_timer_clip(number, size=(720, 1280)):
    try:
        img = Image.new('RGBA', size, (0,0,0,0))
        draw = ImageDraw.Draw(img)
        font = get_font(200)
        text = str(number)
        text_width = draw.textlength(text, font=font)
        x = (size[0] - text_width) / 2
        y = (size[1] - 250) / 2
        outline_range = 8
        for dx in range(-outline_range, outline_range+1):
            for dy in range(-outline_range, outline_range+1):
                draw.text((x+dx, y+dy), text, font=font, fill="black")
        draw.text((x, y), text, font=font, fill="#ff4757")
        return ImageClip(np.array(img))
    except: return None

# ---------------------------------------------------------
# 🎞️ ระบบ Render Q&A อัตโนมัติ
# ---------------------------------------------------------
def process_native_video(task_id, bg_url, qa_url, script, countdown_time, start_time, show_avatar):
    task_id = str(task_id)
    print(f"[{task_id}] ⏳ Native Render queued (Avatar: {show_avatar})...")
    
    with render_semaphore:
        output_name = f"final_native_{task_id}.mp4"
        bg_file = f"bg_{task_id}.jpg"
        qa_file = f"qa_{task_id}.png"
        audio_file = f"voice_{task_id}.mp3"
        avatar_path = "my_avatar.mp4" 

        try:
            download_image_from_url(bg_url, bg_file)
            download_image_from_url(qa_url, qa_file)
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script, audio_file))
            loop.close()

            audio = AudioFileClip(audio_file)
            duration = audio.duration
            
            bg_clip = ImageClip(bg_file).set_duration(duration).resize((720, 1280))
            qa_clip = ImageClip(qa_file).set_duration(duration).resize(width=680).set_position(("center", 150))
            
            layers = [bg_clip, qa_clip]

            if show_avatar and os.path.exists(avatar_path):
                print(f"[{task_id}] 👤 Applying Green Screen Avatar...")
                avatar_clip = VideoFileClip(avatar_path).loop(duration=duration).resize(height=500)
                avatar_clip = avatar_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                avatar_clip = avatar_clip.set_position(("center", "bottom"))
                layers.append(avatar_clip)

            timer_clips = []
            for i in range(countdown_time, 0, -1):
                tc = create_timer_clip(i, size=(720, 1280))
                if tc:
                    tc = tc.set_start(start_time + (countdown_time - i)).set_duration(1)
                    timer_clips.append(tc)
            layers.extend(timer_clips)

            sfx_path = "sfx_countdown.mp3"
            if os.path.exists(sfx_path):
                sfx_clip = AudioFileClip(sfx_path).subclip(0, min(countdown_time, AudioFileClip(sfx_path).duration)).set_start(start_time).volumex(0.6)
                final_audio = CompositeAudioClip([audio, sfx_clip])
            else:
                final_audio = audio

            print(f"[{task_id}] 🎬 Rendering video...")
            final_video = CompositeVideoClip(layers).set_audio(final_audio)
            final_video.write_videofile(output_name, fps=24, codec='libx264', preset='ultrafast', logger=None)
            
            url = upload_to_gcs(output_name)
            if url:
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] ✅ Uploaded and Webhook sent!")

            final_video.close(); bg_clip.close(); qa_clip.close(); audio.close()
            if show_avatar and os.path.exists(avatar_path): avatar_clip.close()
            for t in timer_clips: t.close()
            if os.path.exists(sfx_path): sfx_clip.close()

        except Exception as e:
            print(f"❌ Native Render Error [{task_id}]: {e}")
        finally:
            for f in [bg_file, qa_file, output_name]:
                try: os.remove(f)
                except: pass
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    bg_url = data.get('bg_image_url')
    qa_url = data.get('qa_image_url')
    script = data.get('script', 'ไม่มีสคริปต์')
    countdown_time = int(data.get('countdown_time', 5))
    start_time = float(data.get('start_time', 10.0))
    show_avatar = data.get('show_avatar', False)

    if not bg_url or not qa_url:
        return jsonify({"error": "Missing image URLs"}), 400

    threading.Thread(target=process_native_video, args=(task_id, bg_url, qa_url, script, countdown_time, start_time, show_avatar)).start()
    return jsonify({"status": "processing", "task_id": task_id, "mode": "native"}), 202

# 🟢 อีกจุดที่สำคัญ ต้องมีตัวสั่งรัน Server ครับ
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))