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
import time
import subprocess

# 🚀 ท่าไม้ตายปิดบัญชี: ดึง Path FFmpeg ที่ใช้งานได้จริงมาบังคับ MoviePy
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = static_ffmpeg.run.get_command_path("ffmpeg")
    print(f"✅ FFmpeg Path Located: {FFMPEG_PATH}")
    
    # บังคับให้ MoviePy ใช้ตัวนี้เท่านั้น
    from moviepy.config import change_settings
    change_settings({"FFMPEG_BINARY": FFMPEG_PATH})
except Exception as e:
    print(f"⚠️ Warning static-ffmpeg: {e}")
    FFMPEG_PATH = "ffmpeg"

# 🟢 ประกาศ Flask App
from flask import Flask, request, jsonify
app = Flask(__name__) 

import PIL.Image
from PIL import ImageFilter, ImageEnhance
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

from moviepy.editor import *
import moviepy.video.fx.all as vfx
import edge_tts
from google.cloud import storage
import datetime

nest_asyncio.apply()

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# 🛠 ฟังก์ชันล้างไฟล์เน่า (Force Baseline Profile)
# ---------------------------------------------------------
def repair_video_file(input_path, output_path, task_id):
    print(f"[{task_id}] 🛠 กำลังล้างไฟล์วิดีโอด้วยโหมด Safe-Format...")
    try:
        # บังคับ Scale เลขคู่, Profile Baseline, Pix_fmt YUV420P เพื่อความเข้ากันได้สูงสุด
        cmd = [
            FFMPEG_PATH, '-y', '-i', input_path,
            '-vf', "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-strict', 'experimental',
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[{task_id}] ❌ FFmpeg Repair Fail: {result.stderr}")
            return False
        
        time.sleep(2) # รอให้ระบบคลายล็อกไฟล์
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            print(f"[{task_id}] ✅ ซ่อมไฟล์สำเร็จ (ขนาด: {os.path.getsize(output_path)} bytes)")
            return True
        return False
    except Exception as e:
        print(f"[{task_id}] ❌ ระบบซ่อมพัง: {e}")
        return False

# ---------------------------------------------------------
# ☁️ Helper Functions
# ---------------------------------------------------------
def get_gcs_client(task_id):
    gcs_json_content = os.environ.get("GCS_KEY_JSON")
    if gcs_json_content:
        try:
            info = json.loads(gcs_json_content)
            return storage.Client.from_service_account_info(info)
        except: return None
    elif os.path.exists(KEY_FILE_PATH):
        return storage.Client.from_service_account_json(KEY_FILE_PATH)
    return None

def upload_to_gcs(source_file_name, task_id):
    print(f"[{task_id}] 🚀 [GCS] เริ่มอัปโหลด...")
    try:
        storage_client = get_gcs_client(task_id)
        if not storage_client: return None
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(os.path.basename(source_file_name))
        blob.upload_from_filename(source_file_name, timeout=300)
        return blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12), method="GET")
    except Exception as e:
        print(f"[{task_id}] ❌ GCS Fail: {e}")
        return None

def download_file(url, filename, task_id):
    if not url or str(url).strip() == "" or url == "None": return False
    url = str(url).strip()
    if "hcti.io" in url and not url.endswith(('.png', '.jpg', '.webp')): url += '.png'
    
    print(f"[{task_id}] 📥 ดาวน์โหลด: {url[:60]}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        with requests.get(url, headers=headers, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        
        # เช็กขนาดไฟล์ (ต้องเกิน 50KB สำหรับวิดีโอ)
        if os.path.exists(filename) and os.path.getsize(filename) > 50000:
            return True
        print(f"[{task_id}] ⚠️ ไฟล์ที่โหลดมาขนาดเล็กเกินไป ({os.path.getsize(filename)} bytes)")
        return False
    except Exception as e:
        print(f"[{task_id}] ❌ โหลดล้มเหลว: {e}")
        return False

async def create_voice(text, filename, task_id):
    if not text: return False
    try:
        communicate = edge_tts.Communicate(str(text), "th-TH-NiwatNeural")
        await communicate.save(filename)
        return True
    except: return False

# ---------------------------------------------------------
# 🎞️ ระบบ Render แบบ Ultra-Safe (D-ID GOD MODE)
# ---------------------------------------------------------
def process_native_video(task_id, qa_url, ans_url, ad_img_url, avatar_url, script_qa, script_ans, script_ad, countdown_time, show_avatar):
    task_id = str(task_id)
    print(f"\n==================== งานใหม่: {task_id} ====================")
    
    with render_semaphore:
        output_name = f"final_{task_id}.mp4"
        f_qa_img, f_ans_img, f_ad_img = f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png"
        f_qa_aud, f_ans_aud, f_ad_aud = f"qa_{task_id}.mp3", f"ans_{task_id}.mp3", f"ad_{task_id}.mp3"
        f_av_raw, f_av_fixed = f"raw_av_{task_id}.mp4", f"fixed_av_{task_id}.mp4"

        try:
            # 1. เตรียมทรัพยากร
            download_file(qa_url, f_qa_img, task_id)
            download_file(ans_url, f_ans_img, task_id)
            has_ad = download_file(ad_img_url, f_ad_img, task_id)
            has_did = download_file(avatar_url, f_av_raw, task_id)

            # 2. ทำเสียง (Async)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice(script_qa, f_qa_aud, task_id))
            loop.run_until_complete(create_voice(script_ans, f_ans_aud, task_id))
            if has_ad and script_ad: loop.run_until_complete(create_voice(script_ad, f_ad_aud, task_id))
            loop.close()

            # 🎬 สร้างฉากหลัก
            qa_clip = AudioFileClip(f_qa_aud)
            s1 = ImageClip(f_qa_img).set_duration(qa_clip.duration).resize((720, 1280)).set_audio(qa_clip)
            s2 = ImageClip(f_qa_img).set_duration(countdown_time).resize((720, 1280))
            if os.path.exists("sfx_countdown.mp3"):
                sfx = AudioFileClip("sfx_countdown.mp3").subclip(0, min(countdown_time, 5))
                s2 = s2.set_audio(sfx)
            ans_clip = AudioFileClip(f_ans_aud)
            s3 = ImageClip(f_ans_img).set_duration(ans_clip.duration).resize((720, 1280)).set_audio(ans_clip)
            main_vid = concatenate_videoclips([s1, s2, s3])

            # 👤 การจัดการ Avatar (Hybrid Fallback)
            final_vid = main_vid
            if show_avatar:
                target_av = None
                if has_did:
                    if repair_video_file(f_av_raw, f_av_fixed, task_id):
                        target_av = f_av_fixed
                
                # ถ้าซ่อมแล้วยังใช้ไม่ได้ หรือไม่มีจาก D-ID ให้ถอยไปหา my_avatar.mp4
                if not target_av and os.path.exists("my_avatar.mp4"):
                    print(f"[{task_id}] 👤 ใช้ไฟล์สำรองถาวร (my_avatar.mp4)")
                    target_av = "my_avatar.mp4"

                if target_av:
                    try:
                        # เปิดแบบไม่เอาเสียง และใส่ลูปกันเหนียว
                        av_clip = VideoFileClip(target_av, audio=False).resize(height=600)
                        av_clip = av_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                        
                        # Logic Freeze Frame
                        if av_clip.duration < main_vid.duration:
                            f_dur = main_vid.duration - av_clip.duration
                            last_f = av_clip.to_ImageClip(t=av_clip.duration - 0.1).set_duration(f_dur)
                            av_clip = concatenate_videoclips([av_clip, last_f])
                        else:
                            av_clip = av_clip.set_duration(main_vid.duration)
                        
                        av_clip = av_clip.set_position(("center", "bottom"))
                        final_vid = CompositeVideoClip([main_vid, av_clip])
                    except Exception as av_e:
                        print(f"[{task_id}] ❌ แปะอวตารไม่ได้แม้วิธีสุดท้าย: {av_e}")

            # 🎬 Scene โฆษณาเบลอ
            if has_ad:
                raw_ad = PIL.Image.open(f_ad_img).convert("RGB")
                bg_ad = raw_ad.resize((720, 1280), PIL.Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(25))
                bg_ad = ImageEnhance.Brightness(bg_ad).enhance(0.5)
                ad_bg = ImageClip(np.array(bg_ad))
                ad_fg = ImageClip(f_ad_img)
                if ad_fg.w / ad_fg.h > 720/1280: ad_fg = ad_fg.resize(width=720)
                else: ad_fg = ad_fg.resize(height=1000)
                s4 = CompositeVideoClip([ad_bg, ad_fg.set_position("center")])
                if os.path.exists(f_ad_aud):
                    ad_aud = AudioFileClip(f_ad_aud)
                    s4 = s4.set_duration(ad_aud.duration).set_audio(ad_aud)
                else: s4 = s4.set_duration(5)
                final_vid = concatenate_videoclips([final_vid, s4])

            # ⚙️ เรนเดอร์จริง
            print(f"[{task_id}] ⚙️ เริ่ม Render...")
            final_vid.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            url = upload_to_gcs(output_name, task_id)
            if url: requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
            print(f"[{task_id}] 🎉 ภารกิจเสร็จสิ้น!")

        except Exception as e: print(f"[{task_id}] ❌ Render ล้มเหลว: {e}")
        finally:
            for f in [f_qa_img, f_ans_img, f_ad_img, f_qa_aud, f_ans_aud, f_ad_aud, f_av_raw, f_av_fixed, output_name]:
                if os.path.exists(f):
                    try: os.remove(f)
                    except: pass
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    threading.Thread(target=process_native_video, args=(
        task_id, data.get('qa_image_url'), data.get('ans_image_url'),
        data.get('ad_image_url'), data.get('avatar_url') or data.get('avatar_video_url'),
        data.get('script_qa'), data.get('script_ans'), data.get('script_ad'),
        int(data.get('countdown_time', 5)), data.get('show_avatar', False)
    )).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))