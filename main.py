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
import time # <-- เพิ่มเข้ามาเพื่อใช้หน่วงเวลา

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
        with requests.get(url, headers=headers, timeout=90, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        # 🟢 หน่วงเวลาสักนิดเพื่อให้ OS เขียนไฟล์เสร็จชัวร์ๆ
        time.sleep(3) 
        
        if os.path.exists(filename) and os.path.getsize(filename) > 20000: # ต้องใหญ่นิดนึงถึงจะเป็นวิดีโอจริง
            print(f"[{task_id}] ✅ ดาวน์โหลดไฟล์สำเร็จ (ขนาด: {os.path.getsize(filename)} bytes)")
            return True
        return False
    except Exception as e:
        print(f"[{task_id}] ❌ ดาวน์โหลดไม่สำเร็จ: {e}")
        return False

async def create_voice_safe(text, filename, task_id):
    if not text or str(text).strip() == "": return False
    print(f"[{task_id}] 🎙️ กำลังสร้างเสียงพากย์ (TTS)...")
    try:
        communicate = edge_tts.Communicate(str(text), "th-TH-NiwatNeural")
        await communicate.save(filename)
        return True
    except Exception as e:
        print(f"[{task_id}] ❌ TTS Error: {e}")
        return False

# ---------------------------------------------------------
# 🎞️ ระบบ Render แบบ Ultra Defensive (D-ID Safe)
# ---------------------------------------------------------
def process_native_video(task_id, qa_url, ans_url, ad_img_url, avatar_video_url, script_qa, script_ans, script_ad, countdown_time, show_avatar):
    task_id = str(task_id)
    print(f"\n=======================================================")
    print(f"[{task_id}] 🎬 เริ่มสร้างวิดีโอ (Avatar Mode: {show_avatar})")
    print(f"=======================================================")
    
    with render_semaphore:
        output_name = f"final_{task_id}.mp4"
        f_qa_img, f_ans_img, f_ad_img = f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png"
        f_qa_aud, f_ans_aud, f_ad_aud = f"qa_{task_id}.mp3", f"ans_{task_id}.mp3", f"ad_{task_id}.mp3"
        f_avatar_vid = f"did_avatar_{task_id}.mp4"

        try:
            # 1. โหลดทรัพยากร
            download_file_from_url(qa_url, f_qa_img, task_id)
            download_file_from_url(ans_url, f_ans_img, task_id)
            has_ad = download_file_from_url(ad_img_url, f_ad_img, task_id)
            has_did_video = download_file_from_url(avatar_video_url, f_avatar_vid, task_id)

            # 2. ทำเสียงพากย์
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script_qa, f_qa_aud, task_id))
            loop.run_until_complete(create_voice_safe(script_ans, f_ans_aud, task_id))
            has_ad_audio = False
            if has_ad and script_ad:
                has_ad_audio = loop.run_until_complete(create_voice_safe(script_ad, f_ad_aud, task_id))
            loop.close()

            # --- สร้าง Scene หลัก ---
            qa_aud_clip = AudioFileClip(f_qa_aud)
            scene1 = ImageClip(f_qa_img).set_duration(qa_aud_clip.duration).resize((720, 1280)).set_audio(qa_aud_clip)

            scene2 = ImageClip(f_qa_img).set_duration(countdown_time).resize((720, 1280))
            if os.path.exists("sfx_countdown.mp3"):
                sfx = AudioFileClip("sfx_countdown.mp3").subclip(0, min(countdown_time, 5))
                scene2 = scene2.set_audio(sfx)

            ans_aud_clip = AudioFileClip(f_ans_aud)
            scene3 = ImageClip(f_ans_img).set_duration(ans_aud_clip.duration).resize((720, 1280)).set_audio(ans_aud_clip)

            main_video = concatenate_videoclips([scene1, scene2, scene3])

            # 👤 การจัดการ Avatar แบบอึดพิเศษ (Fallback System)
            final_main = main_video
            if show_avatar:
                target_avatar = None
                
                # ลองพยายามเปิดไฟล์ D-ID อย่างระมัดระวัง
                if has_did_video:
                    try:
                        print(f"[{task_id}] 👤 พยายามเปิดไฟล์วิดีโอจาก D-ID...")
                        avatar_test = VideoFileClip(f_avatar_vid)
                        # ถ้าอ่านได้ถึงตรงนี้ แปลว่าไฟล์ปกติ
                        avatar_test.close()
                        target_avatar = f_avatar_vid
                    except Exception as e:
                        print(f"[{task_id}] ⚠️ ไฟล์ D-ID มีปัญหา ({e}), จะสลับไปใช้ไฟล์สำรองแทน")
                
                # ถ้าไฟล์ D-ID เสีย หรือไม่มี ให้ใช้ Fallback ในเครื่อง
                if not target_avatar and os.path.exists("my_avatar.mp4"):
                    print(f"[{task_id}] 👤 ใช้ไฟล์สำรอง (my_avatar.mp4) แทน")
                    target_avatar = "my_avatar.mp4"

                if target_avatar:
                    try:
                        avatar_raw = VideoFileClip(target_avatar).resize(height=600)
                        avatar_raw = avatar_raw.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                        
                        # Logic Freeze Frame
                        if avatar_raw.duration < main_video.duration:
                            freeze_duration = main_video.duration - avatar_raw.duration
                            last_frame = avatar_raw.to_ImageClip(t=avatar_raw.duration - 0.1).set_duration(freeze_duration)
                            avatar_clip = concatenate_videoclips([avatar_raw, last_frame])
                        else:
                            avatar_clip = avatar_raw.set_duration(main_video.duration)
                            
                        avatar_clip = avatar_clip.set_position(("center", "bottom"))
                        final_main = CompositeVideoClip([main_video, avatar_clip])
                    except Exception as e:
                        print(f"[{task_id}] ❌ พยายามแปะอวตารแล้วแต่พังซ้ำซ้อน ({e}), จะเรนเดอร์แบบไม่มีอวตาร")

            # 🎬 SCENE 4: โฆษณา (พื้นหลังเบลอ)
            if has_ad:
                print(f"[{task_id}] 📢 กำลังทำ Scene โฆษณาแบบเบลอพื้นหลัง...")
                raw_img = PIL.Image.open(f_ad_img).convert("RGB")
                bg_img = raw_img.resize((720, 1280), PIL.Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=25))
                bg_img = ImageEnhance.Brightness(bg_img).enhance(0.5)
                ad_bg_clip = ImageClip(np.array(bg_img))
                
                ad_main = ImageClip(f_ad_img)
                if ad_main.w / ad_main.h > 720 / 1280: ad_main = ad_main.resize(width=720)
                else: ad_main = ad_main.resize(height=1000)
                
                scene4 = CompositeVideoClip([ad_bg_clip, ad_main.set_position("center")])
                
                ad_aud_clip = None
                if has_ad_audio:
                    ad_aud_clip = AudioFileClip(f_ad_aud)
                    scene4 = scene4.set_duration(ad_aud_clip.duration).set_audio(ad_aud_clip)
                else: scene4 = scene4.set_duration(5)
                    
                final_video = concatenate_videoclips([final_main, scene4])
            else:
                final_video = final_main

            # 🎬 สั่ง Render
            print(f"[{task_id}] ⚙️ 🎬 กำลัง Render วิดีโอหลัก...")
            final_video.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            url = upload_to_gcs(output_name, task_id)
            if url:
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] 🎉 สำเร็จเรียบร้อย!")

            final_video.close(); main_video.close(); qa_aud_clip.close(); ans_aud_clip.close()
            if has_ad and 'ad_aud_clip' in locals() and ad_aud_clip: ad_aud_clip.close()

        except Exception as e:
            print(f"[{task_id}] ❌❌ Render พัง: {e}")
        finally:
            for f in [f_qa_img, f_ans_img, f_ad_img, f_qa_aud, f_ans_aud, f_ad_aud, f_avatar_vid, output_name]:
                if os.path.exists(f):
                    try: os.remove(f)
                    except: pass
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    print(f"\n📨 งานใหม่: {task_id}")
    threading.Thread(target=process_native_video, args=(
        task_id, data.get('qa_image_url'), data.get('ans_image_url'),
        data.get('ad_image_url'), data.get('avatar_video_url'),
        data.get('script_qa'), data.get('script_ans'), data.get('script_ad'),
        int(data.get('countdown_time', 5)), data.get('show_avatar', False)
    )).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    print("🚀 ระบบ Video Engine (Ultra Defensive) พร้อมลุย!")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))