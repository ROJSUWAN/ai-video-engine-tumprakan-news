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

# 🟢 ประกาศ Flask App
from flask import Flask, request, jsonify
app = Flask(__name__) 

# 🟢 แก้บั๊ก ANTIALIAS และเตรียมเครื่องมือเบลอภาพ
import PIL.Image
from PIL import ImageFilter, ImageEnhance
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

# AI & Media Libs
from moviepy.editor import *
import moviepy.video.fx.all as vfx
import edge_tts

# Google Cloud
from google.cloud import storage
import datetime

nest_asyncio.apply()

# 🔗 Config (คุณตั้มเช็ก URL Webhook อีกทีนะครับ)
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# ☁️ Helper Functions (GCS / Download / TTS)
# ---------------------------------------------------------
def get_gcs_client(task_id):
    gcs_json_content = os.environ.get("GCS_KEY_JSON")
    if gcs_json_content:
        try:
            info = json.loads(gcs_json_content)
            return storage.Client.from_service_account_info(info)
        except Exception as e:
            print(f"[{task_id}] ❌ [GCS] Error: {e}")
            return None
    elif os.path.exists(KEY_FILE_PATH):
        return storage.Client.from_service_account_json(KEY_FILE_PATH)
    return None

def upload_to_gcs(source_file_name, task_id):
    print(f"[{task_id}] 🚀 [GCS] เริ่มอัปโหลดไฟล์ไปที่ Google Cloud...")
    try:
        storage_client = get_gcs_client(task_id)
        if not storage_client: return None
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(os.path.basename(source_file_name))
        blob.upload_from_filename(source_file_name, timeout=300)
        url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12), method="GET")
        return url
    except Exception as e:
        print(f"[{task_id}] ❌ [GCS] Upload Failed: {e}")
        return None

def download_file_from_url(url, filename, task_id):
    if not url or str(url).strip() == "" or url == "None": return False
    url = str(url).strip()
    if "hcti.io" in url and not url.endswith(('.png', '.jpg', '.webp')):
        url += '.png'
    print(f"[{task_id}] 📥 กำลังดาวน์โหลด: {url[:60]}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            with open(filename, 'wb') as f: f.write(r.content)
            return True
        return False
    except: return False

async def create_voice_safe(text, filename, task_id):
    if not text or str(text).strip() == "": return False
    print(f"[{task_id}] 🎙️ กำลังสร้างเสียงพากย์ (TTS)...")
    try:
        communicate = edge_tts.Communicate(str(text), "th-TH-NiwatNeural", rate="+0%")
        await communicate.save(filename)
        return True
    except Exception as e:
        print(f"[{task_id}] ❌ TTS Error: {e}")
        return False

# ---------------------------------------------------------
# 🎞️ ระบบ Render วิดีโอ (4 ฉาก + D-ID Support)
# ---------------------------------------------------------
def process_native_video(task_id, qa_url, ans_url, ad_img_url, avatar_video_url, script_qa, script_ans, script_ad, countdown_time, show_avatar):
    task_id = str(task_id)
    print(f"\n" + "="*50)
    print(f"[{task_id}] 🎬 เริ่มสร้างวิดีโอ (Avatar: {show_avatar})")
    print("="*50)
    
    with render_semaphore:
        output_name = f"final_{task_id}.mp4"
        f_qa_img, f_ans_img, f_ad_img = f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png"
        f_qa_aud, f_ans_aud, f_ad_aud = f"qa_{task_id}.mp3", f"ans_{task_id}.mp3", f"ad_{task_id}.mp3"
        f_avatar_vid = f"did_avatar_{task_id}.mp4"

        try:
            # 1. โหลดทรัพยากรทั้งหมด
            if not download_file_from_url(qa_url, f_qa_img, task_id): raise Exception("โหลดรูป Q&A ไม่สำเร็จ")
            if not download_file_from_url(ans_url, f_ans_img, task_id): raise Exception("โหลดรูปเฉลยไม่สำเร็จ")
            has_ad = download_file_from_url(ad_img_url, f_ad_img, task_id)
            has_did_video = download_file_from_url(avatar_video_url, f_avatar_vid, task_id)

            # 2. สร้างเสียงพากย์ AI (QA, Ans, Ad)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script_qa, f_qa_aud, task_id))
            loop.run_until_complete(create_voice_safe(script_ans, f_ans_aud, task_id))
            has_ad_audio = False
            if has_ad and script_ad:
                has_ad_audio = loop.run_until_complete(create_voice_safe(script_ad, f_ad_aud, task_id))
            loop.close()

            # --- สร้าง Scene ---
            
            # 🎬 SCENE 1: โจทย์คำถาม
            qa_aud_clip = AudioFileClip(f_qa_aud)
            scene1 = ImageClip(f_qa_img).set_duration(qa_aud_clip.duration).resize((720, 1280)).set_audio(qa_aud_clip)

            # 🎬 SCENE 2: พักให้คิด (Countdown)
            scene2 = ImageClip(f_qa_img).set_duration(countdown_time).resize((720, 1280))
            if os.path.exists("sfx_countdown.mp3"):
                sfx = AudioFileClip("sfx_countdown.mp3").subclip(0, min(countdown_time, 5))
                scene2 = scene2.set_audio(sfx)

            # 🎬 SCENE 3: เฉลยและคำอธิบาย
            ans_aud_clip = AudioFileClip(f_ans_aud)
            scene3 = ImageClip(f_ans_img).set_duration(ans_aud_clip.duration).resize((720, 1280)).set_audio(ans_aud_clip)

            # รวม 3 ฉากหลัก
            main_video = concatenate_videoclips([scene1, scene2, scene3])

            # 👤 แปะ Avatar (ถ้าเลือกใช้)
            if show_avatar:
                target_avatar = None
                if has_did_video: target_avatar = f_avatar_vid
                elif os.path.exists("my_avatar.mp4"): target_avatar = "my_avatar.mp4"

                if target_avatar:
                    print(f"[{task_id}] 👤 กำลังประกอบร่าง Avatar...")
                    avatar_clip = VideoFileClip(target_avatar).resize(height=600)
                    avatar_clip = avatar_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                    avatar_clip = avatar_clip.loop(duration=main_video.duration).set_position(("center", "bottom"))
                    main_video = CompositeVideoClip([main_video, avatar_clip])

            # 🎬 SCENE 4: โฆษณา (พร้อมระบบพื้นหลังเบลอ)
            ad_aud_clip = None 
            if has_ad:
                print(f"[{task_id}] 📢 กำลังทำ Scene โฆษณาแบบเบลอพื้นหลัง...")
                raw_img = PIL.Image.open(f_ad_img).convert("RGB")
                # พื้นหลังเบลอ
                bg_img = raw_img.resize((720, 1280), PIL.Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=25))
                bg_img = ImageEnhance.Brightness(bg_img).enhance(0.5) # ลดแสงลง 50%
                ad_bg_clip = ImageClip(np.array(bg_img))
                # รูปโฆษณาหลัก
                ad_main_clip = ImageClip(f_ad_img)
                if ad_main_clip.w / ad_main_clip.h > 720 / 1280:
                    ad_main_clip = ad_main_clip.resize(width=720)
                else:
                    ad_main_clip = ad_main_clip.resize(height=1000)
                
                scene4 = CompositeVideoClip([ad_bg_clip, ad_main_clip.set_position("center")])
                
                if has_ad_audio:
                    ad_aud_clip = AudioFileClip(f_ad_aud)
                    scene4 = scene4.set_duration(ad_aud_clip.duration).set_audio(ad_aud_clip)
                else:
                    scene4 = scene4.set_duration(5)
                    
                final_video = concatenate_videoclips([main_video, scene4])
            else:
                final_video = main_video

            # ⚙️ สั่ง Render จริง
            print(f"[{task_id}] ⚙️ 🎬 เริ่มกระบวนการ Render...")
            final_video.write_videofile(output_name, fps=24, codec='libx264', preset='ultrafast', logger=None)
            
            # 🌐 อัปโหลดและยิง Webhook กลับไป n8n
            url = upload_to_gcs(output_name, task_id)
            if url:
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] 🎉 เสร็จสมบูรณ์! ยิง Webhook แล้ว")

            # 🧹 เคลียร์ Memory (สำคัญมาก!)
            final_video.close(); main_video.close(); qa_aud_clip.close(); ans_aud_clip.close()
            if ad_aud_clip: ad_aud_clip.close()

        except Exception as e:
            print(f"[{task_id}] ❌❌ Render พังกลางคัน: {e}")
        finally:
            # ลบไฟล์ชั่วคราวทิ้งทั้งหมด
            temp_files = [f_qa_img, f_ans_img, f_ad_img, f_qa_aud, f_ans_aud, f_ad_aud, f_avatar_vid, output_name]
            for f in temp_files:
                if os.path.exists(f):
                    try: os.remove(f)
                    except: pass
            gc.collect()

# ---------------------------------------------------------
# 🌐 Flask API Endpoints
# ---------------------------------------------------------
@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    print(f"\n📨 ได้รับงานใหม่: {task_id}")
    
    # แยก Thread ไปทำงานเบื้องหลัง
    threading.Thread(target=process_native_video, args=(
        task_id, 
        data.get('qa_image_url'), 
        data.get('ans_image_url'), 
        data.get('ad_image_url'), 
        data.get('avatar_video_url'), 
        data.get('script_qa'), 
        data.get('script_ans'), 
        data.get('script_ad'),
        int(data.get('countdown_time', 5)), 
        data.get('show_avatar', False)
    )).start()
    
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    print("🚀 ระบบ Video Engine (D-ID & Blur Ready) พร้อมทำงาน!")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))