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
from PIL import ImageDraw, ImageFont
import edge_tts

# Google Cloud
from google.cloud import storage
import datetime

nest_asyncio.apply()

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"

# 🚦 Queue Control
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# ☁️ Helper Functions (อัปโหลด, โหลดรูป, ทำเสียง, ทำตัวเลข)
# ---------------------------------------------------------
def get_gcs_client(task_id):
    print(f"[{task_id}] ☁️ [GCS] กำลังตรวจสอบสิทธิ์การเข้าถึง Google Cloud...")
    gcs_json_content = os.environ.get("GCS_KEY_JSON")
    
    if gcs_json_content:
        print(f"[{task_id}] ☁️ [GCS] พบ GCS_KEY_JSON ใน Environment Variables")
        try:
            info = json.loads(gcs_json_content)
            return storage.Client.from_service_account_info(info)
        except Exception as e:
            print(f"[{task_id}] ❌ [GCS] โหลด credentials พัง: {e}")
            return None
    elif os.path.exists(KEY_FILE_PATH):
        print(f"[{task_id}] ☁️ [GCS] พบไฟล์ {KEY_FILE_PATH} ในระบบ")
        return storage.Client.from_service_account_json(KEY_FILE_PATH)
        
    print(f"[{task_id}] ❌ [GCS] ไม่พบ Key สำหรับเข้าถึง Google Cloud ทั้งใน ENV และไฟล์!")
    return None

def upload_to_gcs(source_file_name, task_id):
    print(f"[{task_id}] 🚀 [GCS] กำลังเตรียมอัปโหลดไฟล์ {source_file_name} ขึ้น Bucket: {BUCKET_NAME}")
    try:
        storage_client = get_gcs_client(task_id)
        if not storage_client: 
            print(f"[{task_id}] ❌ [GCS] เชื่อมต่อ Google Cloud ไม่สำเร็จ ยกเลิกการอัปโหลด")
            return None
            
        destination_blob_name = os.path.basename(source_file_name)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_blob_name)
        
        print(f"[{task_id}] 🚀 [GCS] เริ่มยิงข้อมูลขึ้นเซิร์ฟเวอร์ Google...")
        blob.upload_from_filename(source_file_name, timeout=300)
        print(f"[{task_id}] ✅ [GCS] อัปโหลดไฟล์สำเร็จ! กำลังสร้าง Signed URL...")
        
        url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12), method="GET")
        print(f"[{task_id}] 🔗 [GCS] สร้าง URL สำเร็จ: {url[:60]}... (ซ่อนความยาว)")
        return url
    except Exception as e:
        print(f"[{task_id}] ❌ [GCS] Upload Failed: {e}")
        return None

# 🟢 อัปเกรดระบบโหลดรูป: ต่อท้าย .png, ปลอมตัวเนียนขึ้น, และแฉ Error จากเว็บ!
def download_image_from_url(url, filename, task_id):
    # คลีนช่องว่างและบังคับใส่ .png ถ้าไม่มี
    url = url.strip()
    if not url.endswith('.png') and not url.endswith('.jpg') and not url.endswith('.webp'):
        url += '.png'
        
    print(f"[{task_id}] 📥 [Download] กำลังโหลดรูปจาก: {url}")
    try:
        # ใช้ User-Agent ของ Firefox ให้ดูเหมือนคนเล่นเว็บทั่วไป
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'}
        r = requests.get(url, headers=headers, timeout=20)
        
        if r.status_code == 200:
            with open(filename, 'wb') as f: f.write(r.content)
            print(f"[{task_id}] ✅ [Download] โหลดรูปลง Server สำเร็จ: {filename} (ขนาด {len(r.content)} bytes)")
            return True
        else:
            print(f"[{task_id}] ❌ [Download] โหลดรูปพัง! Status Code: {r.status_code}")
            # 🟢 ทีเด็ด: พ่นข้อความที่เซิร์ฟเวอร์ด่ากลับมาให้เราอ่าน
            print(f"[{task_id}] 🔍 สาเหตุจากเซิร์ฟเวอร์ HCTI: {r.text[:300]}") 
            return False
    except Exception as e:
        print(f"[{task_id}] ❌ [Download] Error ระหว่างโหลดรูป: {e}")
        return False

async def create_voice_safe(text, filename, task_id):
    print(f"[{task_id}] 🎙️ [TTS] กำลังสร้างเสียงพากย์ด้วย AI...")
    try:
        communicate = edge_tts.Communicate(text, "th-TH-NiwatNeural", rate="+0%")
        await communicate.save(filename)
        print(f"[{task_id}] ✅ [TTS] สร้างไฟล์เสียงสำเร็จ: {filename}")
    except Exception as e:
        print(f"[{task_id}] ❌ [TTS] Voice Error: {e}")

def get_font(fontsize):
    font_options = ["tahomabd.ttf", "tahoma.ttf", "Sarabun-Bold.ttf"]
    for font_p in font_options:
        if os.path.exists(font_p):
            return ImageFont.truetype(font_p, fontsize)
    return ImageFont.load_default()

def create_timer_clip(number, size=(720, 1280)):
    try:
        img = PIL.Image.new('RGBA', size, (0,0,0,0))
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
    print(f"\n=======================================================")
    print(f"[{task_id}] 🎬 เริ่มกระบวนการ Native Render (Avatar: {show_avatar})")
    print(f"=======================================================")
    
    with render_semaphore:
        print(f"[{task_id}] 🚦 เข้าสู่คิวการ Render...")
        output_name = f"final_native_{task_id}.mp4"
        qa_file = f"qa_{task_id}.png"
        audio_file = f"voice_{task_id}.mp3"
        avatar_path = "my_avatar.mp4" 

        try:
            # 1. โหลดรูปภาพ
            if not download_image_from_url(qa_url, qa_file, task_id):
                raise Exception(f"หยุดการทำงาน! ไม่สามารถดาวน์โหลดรูปภาพได้")
            
            # 2. สร้างเสียง
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script, audio_file, task_id))
            loop.close()

            audio = AudioFileClip(audio_file)
            duration = audio.duration
            print(f"[{task_id}] ⏱️ ความยาววิดีโอรวมจะอยู่ที่: {duration:.2f} วินาที")
            
            # 3. เตรียม Layer Q&A
            print(f"[{task_id}] 🛠️ กำลังจัดการ Layer รูปภาพหลัก...")
            qa_clip = ImageClip(qa_file).set_duration(duration).resize((720, 1280))
            layers = [qa_clip]

            # 4. ใส่ Avatar (ถ้าเลือก)
            if show_avatar and os.path.exists(avatar_path):
                print(f"[{task_id}] 👤 กำลังเจาะฉากเขียว Avatar และจัดวาง...")
                avatar_clip = VideoFileClip(avatar_path).loop(duration=duration).resize(height=500)
                avatar_clip = avatar_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                avatar_clip = avatar_clip.set_position(("center", "bottom"))
                layers.append(avatar_clip)

            # 5. ใส่เวลานับถอยหลัง
            print(f"[{task_id}] ⏲️ กำลังสร้างตัวเลขนับถอยหลัง {countdown_time} วินาที...")
            timer_clips = []
            for i in range(countdown_time, 0, -1):
                tc = create_timer_clip(i, size=(720, 1280))
                if tc:
                    tc = tc.set_start(start_time + (countdown_time - i)).set_duration(1)
                    timer_clips.append(tc)
            layers.extend(timer_clips)

            # 6. จัดการเสียง
            sfx_path = "sfx_countdown.mp3"
            if os.path.exists(sfx_path):
                print(f"[{task_id}] 🎵 พบไฟล์ Effect เสียงนับถอยหลัง กำลังนำมารวมร่าง...")
                sfx_clip = AudioFileClip(sfx_path).subclip(0, min(countdown_time, AudioFileClip(sfx_path).duration)).set_start(start_time).volumex(0.6)
                final_audio = CompositeAudioClip([audio, sfx_clip])
            else:
                final_audio = audio

            # 7. เรนเดอร์วิดีโอ
            print(f"[{task_id}] ⚙️ 🎬 กำลังสั่ง MoviePy Render วิดีโอ (อาจใช้เวลาสักครู่)...")
            final_video = CompositeVideoClip(layers).set_audio(final_audio)
            final_video.write_videofile(output_name, fps=24, codec='libx264', preset='ultrafast', logger=None)
            print(f"[{task_id}] ✅ 🎬 Render วิดีโอเสร็จสมบูรณ์! สร้างไฟล์ {output_name}")
            
            # 8. อัปโหลดขึ้น Google Cloud
            url = upload_to_gcs(output_name, task_id)
            if url:
                print(f"[{task_id}] 🌐 ยิง Webhook กลับไปหา n8n...")
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] 🎉🎉 จบงานสมบูรณ์แบบ! 🎉🎉")
            else:
                print(f"[{task_id}] ❌ อัปโหลด GCS ไม่สำเร็จ เลยไม่ยิง Webhook กลับไป")

            # 9. คืนพื้นที่
            print(f"[{task_id}] 🧹 กำลังล้างไฟล์ชั่วคราวและคืน RAM...")
            final_video.close(); qa_clip.close(); audio.close()
            if show_avatar and os.path.exists(avatar_path): avatar_clip.close()
            for t in timer_clips: t.close()
            if os.path.exists(sfx_path): sfx_clip.close()

        except Exception as e:
            print(f"[{task_id}] ❌❌ Native Render พังกลางคัน: {e}")
        finally:
            for f in [qa_file, output_name, audio_file]:
                try: os.remove(f)
                except: pass
            gc.collect()
            print(f"[{task_id}] 🏁 ปิด Task เรียบร้อย\n")

@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    print(f"\n📨 ได้รับ Request ใหม่จาก n8n: {task_id}")
    bg_url = data.get('bg_image_url') 
    qa_url = data.get('qa_image_url')
    script = data.get('script', 'ไม่มีสคริปต์')
    countdown_time = int(data.get('countdown_time', 5))
    start_time = float(data.get('start_time', 10.0))
    show_avatar = data.get('show_avatar', False)

    if not qa_url:
        print(f"[{task_id}] ❌ n8n ส่งข้อมูลมาไม่ครบ (ไม่มี qa_image_url) ปฏิเสธการรับงาน!")
        return jsonify({"error": "Missing QA image URL"}), 400

    threading.Thread(target=process_native_video, args=(task_id, bg_url, qa_url, script, countdown_time, start_time, show_avatar)).start()
    return jsonify({"status": "processing", "task_id": task_id, "mode": "native"}), 202

if __name__ == '__main__':
    print("🚀 สตาร์ทเซิร์ฟเวอร์ Python พร้อมรับงานแล้ว!")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))