# ---------------------------------------------------------
# ✅ Mode: News Tamprakan (Normal Speed + New Bucket) - FIXED VERSION
# ---------------------------------------------------------
import sys
# บังคับให้ Python พ่น Log ออกมาทันที
sys.stdout.reconfigure(line_buffering=True)

import os
import shutil
import threading
import uuid
import time
import requests
import asyncio
import nest_asyncio
import gc
import json
import numpy as np
from flask import Flask, request, jsonify

# AI & Media Libs
from duckduckgo_search import DDGS
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts
from gtts import gTTS

# ⭐ Import PyThaiNLP ไว้ระดับบนสุด บังคับใช้ตัดคำภาษาไทย
from pythainlp.tokenize import word_tokenize

# Google Cloud
from google.cloud import storage
import datetime

nest_asyncio.apply()
app = Flask(__name__)

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed"

# ⚙️ Google Cloud Storage Config
BUCKET_NAME = "n8n-video-tamprakan-news" 
KEY_FILE_PATH = "gcs_key.json"

# ---------------------------------------------------------
# ☁️ Upload Function
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
        print(f"✅ Upload Success: {url}")
        return url
    except Exception as e:
        print(f"❌ Upload Failed: {e}")
        return None

# ---------------------------------------------------------
# 🎨 Helper Functions (Image)
# ---------------------------------------------------------
def smart_resize_image(img_path):
    try:
        target_size = (720, 1280)
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            if img.size == target_size: return True
            
            bg = img.copy()
            bg_ratio = target_size[0] / target_size[1]
            img_ratio = img.width / img.height
            
            if img_ratio > bg_ratio:
                resize_height = target_size[1]
                resize_width = int(resize_height * img_ratio)
            else:
                resize_width = target_size[0]
                resize_height = int(resize_width / img_ratio)
                
            bg = bg.resize((resize_width, resize_height), Image.Resampling.LANCZOS)
            left = (bg.width - target_size[0]) // 2
            top = (bg.height - target_size[1]) // 2
            bg = bg.crop((left, top, left + target_size[0], top + target_size[1]))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=40))
            
            img.thumbnail((720, 1280), Image.Resampling.LANCZOS)
            x = (target_size[0] - img.width) // 2
            y = (target_size[1] - img.height) // 2
            bg.paste(img, (x, y))
            bg.save(img_path)
            return True
    except: return False

def download_image_from_url(url, filename):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            with open(filename, 'wb') as f: f.write(r.content)
            smart_resize_image(filename)
            return True
    except: pass
    return False

def search_real_image(query, filename):
    if not query or any(x in query.upper() for x in ["SELECT", "INSERT", "GALLERY"]) or len(query) < 3:
        return False
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=1))
            if results: return download_image_from_url(results[0]['image'], filename)
    except: pass
    return False

# ---------------------------------------------------------
# 🔤 Font & Text Utilities
# ---------------------------------------------------------
def get_font(fontsize):
    font_options = ["tahomabd.ttf", "tahoma.ttf", "Sarabun-Bold.ttf"]
    for font_p in font_options:
        if os.path.exists(font_p):
            return ImageFont.truetype(font_p, fontsize)
    
    FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Bold.ttf"
    try:
        r = requests.get(FONT_URL, timeout=15)
        with open("Sarabun-Bold.ttf", 'wb') as f: f.write(r.content)
        return ImageFont.truetype("Sarabun-Bold.ttf", fontsize)
    except:
        return ImageFont.load_default()

def wrap_and_chunk_thai_text(text, max_chars_per_line=32, max_lines=2):
    words = word_tokenize(text, engine="newmm")
    chunks, current_chunk, current_line = [], [], ""
    for word in words:
        if len(current_line) + len(word) <= max_chars_per_line:
            current_line += word
        else:
            if current_line: current_chunk.append(current_line)
            current_line = word
            if len(current_chunk) == max_lines:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
    if current_line: current_chunk.append(current_line)
    if current_chunk: chunks.append("\n".join(current_chunk))
    return chunks

def create_text_clip(text_chunk, size=(720, 1280)):
    try:
        img = Image.new('RGBA', size, (0,0,0,0))
        draw = ImageDraw.Draw(img)
        font_size = 36
        font = get_font(font_size)
        lines = text_chunk.split('\n')
        line_height = font_size + 15
        total_height = len(lines) * line_height
        margin_top = 150 
        start_y = margin_top 
        draw.rectangle([20, start_y - 15, size[0]-20, start_y + total_height + 15], fill=(0,0,0,160))
        cur_y = start_y
        for line in lines:
            text_width = draw.textlength(line, font=font)
            x = (size[0] - text_width) / 2
            draw.text((x-2, cur_y), line, font=font, fill="black")
            draw.text((x+2, cur_y), line, font=font, fill="black")
            draw.text((x, cur_y), line, font=font, fill="white")
            cur_y += line_height
        return ImageClip(np.array(img))
    except: return None

# ---------------------------------------------------------
# 🔊 Audio (Normal Speed Fix)
# ---------------------------------------------------------
async def create_voice_safe(text, filename):
    try:
        communicate = edge_tts.Communicate(text, "th-TH-NiwatNeural", rate="+0%")
        await communicate.save(filename)
    except:
        try: tts = gTTS(text=text, lang='th'); tts.save(filename)
        except: pass

def create_watermark_clip(duration):
    logo_path = "my_logo.png" 
    if not os.path.exists(logo_path): return None
    try:
        return (ImageClip(logo_path).set_duration(duration)
                .resize(width=200).set_opacity(0.9).set_position(("right", "top")))
    except: return None

def create_ads_clip(duration):
    ad_path = "my_ads.png"
    if not os.path.exists(ad_path): return None
    try:
        ad_clip = ImageClip(ad_path).resize(width=720)
        y_position = 1280 - ad_clip.h - 30
        return ad_clip.set_position(("center", y_position)).set_duration(duration)
    except: return None

# ---------------------------------------------------------
# 🎞️ Main Process
# ---------------------------------------------------------
def process_video_background(task_id, scenes, topic):
    # ตรวจสอบประเภทข้อมูล task_id ให้เป็น string เสมอ
    task_id = str(task_id)
    print(f"[{task_id}] 🎬 Starting Process (Normal Speed)...")
    output_filename = f"video_{task_id}.mp4"
    master_image_path = f"master_{task_id}.jpg"
    
    if not search_real_image(topic, master_image_path):
        Image.new('RGB', (720, 1280), (20,20,20)).save(master_image_path)

    try:
        valid_clips = []
        for i, scene in enumerate(scenes):
            gc.collect()
            img_file = f"temp_{task_id}_{i}.jpg"
            audio_file = f"temp_{task_id}_{i}.mp3"
            clip_output = f"clip_{task_id}_{i}.mp4"

            prompt = scene.get('image_url') or ""
            if prompt.startswith("http"):
                download_image_from_url(prompt, img_file)
            else:
                shutil.copy(master_image_path, img_file)

            script_text = scene.get('script') or "ไม่มีเนื้อหาข่าว"
            
            # ✅ ปรับปรุงระบบ Async Loop สำหรับ Thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script_text, audio_file))
            loop.close()

            if os.path.exists(audio_file) and os.path.exists(img_file):
                audio = AudioFileClip(audio_file)
                dur = audio.duration
                img_clip = ImageClip(img_file).set_duration(dur)
                
                chunks = wrap_and_chunk_thai_text(script_text)
                total_chars = max(sum(len(c) for c in chunks), 1)
                sub_clips = []
                current_time = 0.0
                for chunk in chunks:
                    chunk_duration = (len(chunk) / total_chars) * dur
                    tc = create_text_clip(chunk)
                    if tc: sub_clips.append(tc.set_start(current_time).set_duration(chunk_duration))
                    current_time += chunk_duration

                layers = [img_clip] + sub_clips
                wm = create_watermark_clip(dur)
                ad = create_ads_clip(dur)
                if wm: layers.append(wm)
                if ad: layers.append(ad)

                video = CompositeVideoClip(layers).set_audio(audio)
                video.write_videofile(clip_output, fps=15, codec='libx264', preset='ultrafast', logger=None)
                valid_clips.append(clip_output)
                video.close(); audio.close()

        if valid_clips:
            clips = [VideoFileClip(c) for c in valid_clips]
            final = concatenate_videoclips(clips, method="compose")
            final.write_videofile(output_filename, fps=15, preset='ultrafast')
            
            url = upload_to_gcs(output_filename)
            if url:
                payload = {'id': task_id, 'video_url': url, 'status': 'success', 'video_duration': int(final.duration)}
                requests.post(N8N_WEBHOOK_URL, json=payload, timeout=20)
            
            final.close()
            for c in clips: c.close()

    except Exception as e: 
        print(f"❌ Error in process_video_background: {e}")
    finally:
        # ✅ ล้างไฟล์ขยะโดยบังคับ task_id เป็น string
        for f in os.listdir():
            if str(task_id) in f:
                try: os.remove(f)
                except: pass

@app.route('/create-video', methods=['POST'])
def api_create_video():
    data = request.json
    scenes = data.get('scenes', [])
    # ✅ ป้องกัน TypeError: บังคับ task_id จาก n8n ให้เป็น string ทันที
    task_id = str(data.get('task_id') or uuid.uuid4())
    topic = data.get('topic') or ""
    
    if not scenes: return jsonify({"error": "No scenes"}), 400
    
    threading.Thread(target=process_video_background, args=(task_id, scenes, topic)).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))