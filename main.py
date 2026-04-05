# ---------------------------------------------------------
# ✅ Mode: News Brief Pro (Final Fix: pythainlp + Duration + Ads Space)
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

# Environment Variables
HF_TOKEN = os.environ.get("HF_TOKEN")

# ⚙️ Google Cloud Storage Config
BUCKET_NAME = "n8n-video-storage-0123"
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
    if not query or "SELECT" in query or "INSERT" in query or "GALLERY" in query or len(query) < 3:
        return False
        
    print(f"🌍 Searching: {query[:30]}...")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=1))
            if results: return download_image_from_url(results[0]['image'], filename)
    except: pass
    return False

# ---------------------------------------------------------
# 🔤 Font & Text Utilities
# ---------------------------------------------------------
FONT_PATH = "Sarabun-Bold.ttf"
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Bold.ttf"

def get_font(fontsize):
    if not os.path.exists(FONT_PATH):
        print("📥 Downloading Sarabun-Bold.ttf...", flush=True)
        try:
            r = requests.get(FONT_URL, allow_redirects=True, timeout=15)
            with open(FONT_PATH, 'wb') as f: f.write(r.content)
        except Exception as e:
            print(f"❌ Font download failed: {e}")
            return ImageFont.load_default()
    try:
        return ImageFont.truetype(FONT_PATH, fontsize)
    except Exception:
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
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_width = bbox[2] - bbox[0]
            except AttributeError:
                text_width = draw.textlength(line, font=font)
                
            x = (size[0] - text_width) / 2
            draw.text((x-2, cur_y), line, font=font, fill="black")
            draw.text((x+2, cur_y), line, font=font, fill="black")
            draw.text((x, cur_y), line, font=font, fill="white")
            cur_y += line_height
            
        return ImageClip(np.array(img))
    except Exception as e:
        print(f"Error creating text clip: {e}")
        return None

# ---------------------------------------------------------
# 🔊 Audio & Components
# ---------------------------------------------------------
async def create_voice_safe(text, filename):
    try:
        communicate = edge_tts.Communicate(text, "th-TH-NiwatNeural", rate="+25%")
        await communicate.save(filename)
    except:
        try: tts = gTTS(text=text, lang='th'); tts.save(filename)
        except: pass

def create_watermark_clip(duration):
    try:
        logo_path = "my_logo.png" 
        if not os.path.exists(logo_path): return None
        return (ImageClip(logo_path).set_duration(duration)
                .resize(width=200).set_opacity(0.9).set_position(("right", "top")))
    except: return None

# ⭐ เพิ่มฟังก์ชันสร้างพื้นที่ Ads Area
def create_ads_clip(duration):
    ad_width = 720
    ad_path = "my_ads.png"

    try:
        if os.path.exists(ad_path):
            # 1. โหลดรูปและปรับขนาดเฉพาะความกว้างเป็น 720 (ส่วนสูงจะคงอัตราส่วนเดิมอัตโนมัติ ภาพไม่เบี้ยว)
            ad_clip = ImageClip(ad_path).resize(width=ad_width)
            
            # 2. ดึงความสูงจริงของรูปหลังจากย่อ/ขยายให้พอดี 720 แล้ว
            actual_height = ad_clip.h
            
            # 3. คำนวณแกน Y: สูงวิดีโอ (1280) - ความสูงจริง - ระยะห่างขอบล่าง (30)
            y_position = 1280 - actual_height - 30
            
            # 4. ตั้งค่าตำแหน่งและเวลา
            ad_clip = ad_clip.set_position(("center", y_position)).set_duration(duration)
            return ad_clip
        else:
            # ไม่มีรูปโฆษณา -> สร้าง Default Box (ปรับให้สูงขึ้นนิดนึงเป็น 300 จะได้ดูสวย)
            default_height = 300
            y_position = 1280 - default_height - 80
            
            img = Image.new('RGBA', (ad_width, default_height), (255, 255, 255, 180)) 
            draw = ImageDraw.Draw(img)
            font_size = 36
            font = get_font(font_size)
            
            text = f"พื้นที่โฆษณาว่าง\nกว้าง {ad_width} px สนใจ Inbox"
            
            # คำนวณกึ่งกลางเพื่อวาดข้อความ
            try:
                bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center")
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except AttributeError:
                text_w = 300
                text_h = 80
                
            x = (ad_width - text_w) / 2
            y = (default_height - text_h) / 2
            
            draw.multiline_text((x, y), text, font=font, fill="#333333", align="center")
            
            placeholder_clip = (ImageClip(np.array(img))
                                .set_position(("center", y_position))
                                .set_duration(duration))
            return placeholder_clip
    except Exception as e:
        print(f"Error creating ads clip: {e}")
        return None

# ---------------------------------------------------------
# 🎞️ Main Process Logic
# ---------------------------------------------------------
def process_video_background(task_id, scenes, topic):
    print(f"[{task_id}] 🎬 Starting Process (Topic: {topic})...")
    output_filename = f"video_{task_id}.mp4"
    
    master_image_path = f"master_{task_id}.jpg"
    is_master_valid = False 
    
    print(f"[{task_id}] 🖼️ Fetching Master Image for topic: {topic}")
    if search_real_image(topic, master_image_path):
        is_master_valid = True
        smart_resize_image(master_image_path)
        print(f"[{task_id}] ✅ Master Image Set from Topic!")
    else:
        print(f"[{task_id}] ⚠️ Topic search failed. Creating placeholder.")
        Image.new('RGB', (720, 1280), (20,20,20)).save(master_image_path)
        is_master_valid = False

    try:
        valid_clips = []
        for i, scene in enumerate(scenes):
            gc.collect()
            print(f"[{task_id}] Processing Scene {i+1}/{len(scenes)}...")
            
            img_file = f"temp_{task_id}_{i}.jpg"
            audio_file = f"temp_{task_id}_{i}.mp3"
            clip_output = f"clip_{task_id}_{i}.mp4"

            prompt = scene.get('image_url') or scene.get('imageUrl') or ''
            used_specific_image = False
            
            if prompt and "SELECT" not in prompt and "GALLERY" not in prompt and len(prompt) > 5:
                if "http" in prompt:
                    if download_image_from_url(prompt, img_file): used_specific_image = True
                elif not used_specific_image:
                    if search_real_image(prompt, img_file): used_specific_image = True

            if used_specific_image:
                smart_resize_image(img_file)
                if not is_master_valid:
                    shutil.copy(img_file, master_image_path)
                    is_master_valid = True
            else:
                shutil.copy(master_image_path, img_file)

            script_text = scene.get('script') or scene.get('caption') or "No content."
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script_text, audio_file))

            if os.path.exists(audio_file) and os.path.exists(img_file):
                try:
                    audio = AudioFileClip(audio_file)
                    dur = audio.duration
                    
                    img_clip = ImageClip(img_file).set_duration(dur)
                    
                    chunks = wrap_and_chunk_thai_text(script_text, max_chars_per_line=32, max_lines=2)
                    total_chars = max(sum(len(c.replace('\n', '')) for c in chunks), 1)
                    
                    sub_clips = []
                    current_time = 0.0
                    for chunk in chunks:
                        chunk_duration = (len(chunk.replace('\n', '')) / total_chars) * dur
                        tc = create_text_clip(chunk, size=(720, 1280))
                        if tc is not None:
                            tc = tc.set_start(current_time).set_duration(chunk_duration)
                            sub_clips.append(tc)
                        current_time += chunk_duration
                    
                    watermark = create_watermark_clip(dur)
                    ads_clip = create_ads_clip(dur) # ⭐ เรียกใช้งานโฆษณา
                    
                    # รวมเลเยอร์ทั้งหมด (เพิ่ม ads_clip ต่อท้าย)
                    layers = [img_clip] + sub_clips
                    if watermark: layers.append(watermark)
                    if ads_clip: layers.append(ads_clip)
                    
                    video = CompositeVideoClip(layers).set_audio(audio)
                    video.write_videofile(clip_output, fps=15, codec='libx264', audio_codec='aac', preset='ultrafast', threads=2, logger=None)
                    valid_clips.append(clip_output)
                    
                    video.close(); audio.close(); img_clip.close()
                except Exception as e: print(f"Scene Error: {e}")

        if valid_clips:
            print(f"[{task_id}] 🎞️ Merging {len(valid_clips)} clips...")
            clips = [VideoFileClip(c) for c in valid_clips]
            final = concatenate_videoclips(clips, method="compose")
            
            total_duration = int(final.duration) 
            
            final.write_videofile(output_filename, fps=15, bitrate="2000k", preset='ultrafast')
            
            url = upload_to_gcs(output_filename)
            if url:
                try:
                    payload = {
                        'id': task_id, 
                        'video_url': url, 
                        'status': 'success',
                        'video_duration': total_duration 
                    }
                    requests.post(N8N_WEBHOOK_URL, json=payload, timeout=20)
                    print(f"[{task_id}] ✅ Callback sent (Duration: {total_duration}s)!")
                except Exception as e: print(f"Webhook Error: {e}")
            
            final.close()
            for c in clips: c.close()

    except Exception as e: print(f"Error: {e}")
    finally:
        try:
            for f in os.listdir():
                if task_id in f and f.endswith(('.jpg', '.mp3', '.mp4')):
                    try: os.remove(f)
                    except: pass
        except: pass

@app.route('/create-video', methods=['POST'])
def api_create_video():
    data = request.json
    scenes = data.get('scenes', [])
    task_id = data.get('task_id') or str(uuid.uuid4())
    topic = data.get('topic') or ""
    
    if not scenes: return jsonify({"error": "No scenes provided"}), 400
    
    print(f"🚀 Received Task: {task_id} | Topic: {topic}")
    thread = threading.Thread(target=process_video_background, args=(task_id, scenes, topic))
    thread.start()
    
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)