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

# 🟢 ท่าไม้ตายแก้บั๊ก ANTIALIAS สำหรับ MoviePy
import PIL.Image
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

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# ☁️ Helper Functions
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
    print(f"[{task_id}] 🚀 [GCS] เริ่มอัปโหลดไฟล์...")
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
    if not url or str(url).strip() == "": return False
    url = str(url).strip()
    
    # ดัก Error จาก HCTI ให้ใส่ .png
    if "hcti.io" in url and not url.endswith(('.png', '.jpg', '.webp')):
        url += '.png'
        
    print(f"[{task_id}] 📥 กำลังโหลดรูปภาพจาก: {url[:80]}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            with open(filename, 'wb') as f: f.write(r.content)
            return True
        else:
            print(f"[{task_id}] ❌ โหลดรูปพัง Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"[{task_id}] ❌ Error โหลดรูป: {e}")
        return False

async def create_voice_safe(text, filename, task_id):
    if not text or str(text).strip() == "": return False
    print(f"[{task_id}] 🎙️ กำลังสร้างเสียง (TTS)...")
    try:
        communicate = edge_tts.Communicate(str(text), "th-TH-NiwatNeural", rate="+0%")
        await communicate.save(filename)
        return True
    except Exception as e:
        print(f"[{task_id}] ❌ TTS Error: {e}")
        return False

# ---------------------------------------------------------
# 🎞️ ระบบ Render แบบแบ่ง Scene (คำถาม -> รอ -> เฉลย -> โฆษณา)
# ---------------------------------------------------------
def process_native_video(task_id, qa_url, ans_url, ad_img_url, script_qa, script_ans, script_ad, countdown_time, show_avatar):
    task_id = str(task_id)
    print(f"\n=======================================================")
    print(f"[{task_id}] 🎬 เริ่มสร้างวิดีโอ (Avatar: {show_avatar})")
    print(f"=======================================================")
    
    with render_semaphore:
        output_name = f"final_{task_id}.mp4"
        
        f_qa_img = f"qa_img_{task_id}.png"
        f_ans_img = f"ans_img_{task_id}.png"
        f_ad_img = f"ad_img_{task_id}.png"
        
        f_qa_aud = f"qa_aud_{task_id}.mp3"
        f_ans_aud = f"ans_aud_{task_id}.mp3"
        f_ad_aud = f"ad_aud_{task_id}.mp3"
        
        avatar_path = "my_avatar.mp4"

        try:
            # 1. โหลดรูปที่จำเป็น
            if not download_file_from_url(qa_url, f_qa_img, task_id): raise Exception("โหลดรูป Q&A ไม่สำเร็จ")
            if not download_file_from_url(ans_url, f_ans_img, task_id): raise Exception("โหลดรูปเฉลยไม่สำเร็จ")
            
            # เช็กว่ามีโฆษณาไหม?
            has_ad = download_file_from_url(ad_img_url, f_ad_img, task_id)

            # 2. สร้างเสียงพากย์ AI
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script_qa, f_qa_aud, task_id))
            loop.run_until_complete(create_voice_safe(script_ans, f_ans_aud, task_id))
            
            # ถ้ามีโฆษณา และมีสคริปต์โฆษณา ให้ทำเสียงพากย์โฆษณาด้วย
            has_ad_audio = False
            if has_ad and loop.run_until_complete(create_voice_safe(script_ad, f_ad_aud, task_id)):
                has_ad_audio = True
                
            loop.close()

            # 🎬 SCENE 1: คำถาม + เสียงอ่าน
            qa_aud_clip = AudioFileClip(f_qa_aud)
            scene1 = ImageClip(f_qa_img).set_duration(qa_aud_clip.duration).resize((720, 1280)).set_audio(qa_aud_clip)

            # 🎬 SCENE 2: รอคิด (ไม่มีตัวเลขนับถอยหลัง)
            print(f"[{task_id}] ⏳ สร้าง Scene รอคิด {countdown_time} วินาที...")
            scene2 = ImageClip(f_qa_img).set_duration(countdown_time).resize((720, 1280))
            sfx_path = "sfx_countdown.mp3"
            if os.path.exists(sfx_path):
                sfx_clip = AudioFileClip(sfx_path).subclip(0, min(countdown_time, AudioFileClip(sfx_path).duration))
                scene2 = scene2.set_audio(sfx_clip)

            # 🎬 SCENE 3: ภาพเฉลย + เสียงอธิบาย
            ans_aud_clip = AudioFileClip(f_ans_aud)
            scene3 = ImageClip(f_ans_img).set_duration(ans_aud_clip.duration).resize((720, 1280)).set_audio(ans_aud_clip)

            # 🔄 รวม Scene 1, 2, 3
            main_video = concatenate_videoclips([scene1, scene2, scene3])

            # 👤 แปะ Avatar
            if show_avatar and os.path.exists(avatar_path):
                print(f"[{task_id}] 👤 เจาะฉากเขียว Avatar ลงวิดีโอหลัก...")
                avatar_clip = VideoFileClip(avatar_path).loop(duration=main_video.duration).resize(height=500)
                avatar_clip = avatar_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                avatar_clip = avatar_clip.set_position(("center", "bottom"))
                main_video = CompositeVideoClip([main_video, avatar_clip])

            # 🎬 SCENE 4: โฆษณา (ต่อท้ายสุด ไม่มี Avatar บัง)
            if has_ad:
                print(f"[{task_id}] 📢 ต่อท้าย Scene โฆษณา...")
                scene4 = ImageClip(f_ad_img).resize((720, 1280))
                if has_ad_audio:
                    # ถ้ามีเสียงโฆษณา ให้โชว์รูปเท่าวินาทีของเสียง
                    ad_aud_clip = AudioFileClip(f_ad_aud)
                    scene4 = scene4.set_duration(ad_aud_clip.duration).set_audio(ad_aud_clip)
                else:
                    # ถ้าไม่มีเสียงโฆษณา โชว์รูปค้างไว้ 5 วินาที
                    scene4 = scene4.set_duration(5)
                    
                final_video = concatenate_videoclips([main_video, scene4])
                if has_ad_audio: ad_aud_clip.close()
            else:
                final_video = main_video

            # 🎬 สั่ง Render
            print(f"[{task_id}] ⚙️ 🎬 กำลังสั่ง Render Video...")
            final_video.write_videofile(output_name, fps=24, codec='libx264', preset='ultrafast', logger=None)
            
            # 🌐 ยิงกลับ n8n
            url = upload_to_gcs(output_name, task_id)
            if url:
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] 🎉 สร้างคลิปสมบูรณ์ ยิง Webhook แล้ว!")

            # 🧹 คืน RAM
            final_video.close(); main_video.close(); qa_aud_clip.close(); ans_aud_clip.close()

        except Exception as e:
            print(f"[{task_id}] ❌❌ Render พังกลางคัน: {e}")
        finally:
            files_to_remove = [f_qa_img, f_ans_img, f_ad_img, f_qa_aud, f_ans_aud, f_ad_aud, output_name]
            for f in files_to_remove:
                if os.path.exists(f):
                    try: os.remove(f)
                    except: pass
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    print(f"\n📨 รับ Request ใหม่: {task_id}")
    
    qa_url = data.get('qa_image_url')
    ans_url = data.get('ans_image_url')
    
    # 🟢 รับค่าโฆษณาจาก n8n
    ad_img_url = data.get('ad_image_url')
    script_ad = data.get('script_ad')
    
    script_qa = data.get('script_qa')
    script_ans = data.get('script_ans')
    countdown_time = int(data.get('countdown_time', 5))
    show_avatar = data.get('show_avatar', False)

    if not qa_url or not ans_url or not script_qa:
        print(f"[{task_id}] ❌ ข้อมูลไม่ครบ!")
        return jsonify({"error": "Missing params"}), 400

    threading.Thread(target=process_native_video, args=(
        task_id, qa_url, ans_url, ad_img_url, script_qa, script_ans, script_ad, countdown_time, show_avatar
    )).start()
    
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    print("🚀 สตาร์ทเซิร์ฟเวอร์ Python พร้อมรับงานแล้ว!")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))