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
import shutil

# 🚀 บังคับใช้ FFmpeg จาก static-ffmpeg
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = static_ffmpeg.run.get_command_path("ffmpeg")
    from moviepy.config import change_settings
    change_settings({"FFMPEG_BINARY": FFMPEG_PATH})
    print(f"✅ FFmpeg Path: {FFMPEG_PATH}")
except Exception as e:
    FFMPEG_PATH = "ffmpeg"

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

N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
KEY_FILE_PATH = "gcs_key.json"
render_semaphore = threading.Semaphore(1)

# ---------------------------------------------------------
# 🛠 ท่าไม้ตายสุดท้าย: แปลงวิดีโอเป็น Image Sequence (กันพัง 100%)
# ---------------------------------------------------------
def get_avatar_clip_stable(video_path, task_id, target_duration):
    print(f"[{task_id}] 🛠 กำลังแปลงวิดีโอเป็นชุดรูปภาพเพื่อความเสถียร...")
    temp_dir = f"frames_{task_id}"
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    try:
        # สั่ง FFmpeg ระเบิดวิดีโอเป็นรูปภาพ PNG
        cmd = [
            FFMPEG_PATH, '-y', '-i', video_path,
            '-vf', "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            f"{temp_dir}/frame_%04d.png"
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        
        # โหลดรูปภาพทั้งหมดมาทำเป็น Clip
        frames = [f"{temp_dir}/{img}" for img in sorted(os.listdir(temp_dir))]
        if not frames: return None
        
        # สร้าง Clip จากชุดรูปภาพ (FPS 24 ตามมาตรฐาน)
        clip = ImageSequenceClip(frames, fps=24).resize(height=600)
        clip = clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
        
        # จัดการเรื่องเวลา (Freeze Frame)
        if clip.duration < target_duration:
            f_dur = target_duration - clip.duration
            last_f = clip.to_ImageClip(t=clip.duration - 0.1).set_duration(f_dur)
            clip = concatenate_videoclips([clip, last_f])
        else:
            clip = clip.set_duration(target_duration)
            
        return clip
    except Exception as e:
        print(f"[{task_id}] ❌ แปลงรูปภาพพัง: {e}")
        return None
    finally:
        # เก็บ folder ไว้ก่อนจนกว่าจะเรนเดอร์เสร็จ (เดี๋ยวค่อยลบใน finally ใหญ่)
        pass

# ---------------------------------------------------------
# ☁️ Helper Functions
# ---------------------------------------------------------
def upload_to_gcs(source_file_name, task_id):
    try:
        gcs_json = os.environ.get("GCS_KEY_JSON")
        if gcs_json: client = storage.Client.from_service_account_info(json.loads(gcs_json))
        else: client = storage.Client.from_service_account_json(KEY_FILE_PATH)
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(os.path.basename(source_file_name))
        blob.upload_from_filename(source_file_name, timeout=300)
        return blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12), method="GET")
    except: return None

def download_file(url, filename):
    if not url or url == "None": return False
    try:
        if "hcti.io" in url and not url.endswith(('.png', '.jpg')): url += '.png'
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=120, stream=True)
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        return os.path.getsize(filename) > 10000
    except: return False

async def create_voice(text, filename):
    try:
        c = edge_tts.Communicate(str(text), "th-TH-NiwatNeural")
        await c.save(filename)
        return True
    except: return False

# ---------------------------------------------------------
# 🎞️ ระบบ Render แบบ "ระเบิดเฟรม" (Endgame Mode)
# ---------------------------------------------------------
def process_native_video(task_id, qa_url, ans_url, ad_img_url, avatar_url, script_qa, script_ans, script_ad, countdown_time, show_avatar):
    task_id = str(task_id)
    print(f"[{task_id}] 🎬 เริ่มงานด้วยวิธี Image Sequence...")
    
    with render_semaphore:
        output_name = f"final_{task_id}.mp4"
        f_qa_img, f_ans_img, f_ad_img = f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png"
        f_qa_aud, f_ans_aud, f_ad_aud = f"qa_{task_id}.mp3", f"ans_{task_id}.mp3", f"ad_{task_id}.mp3"
        f_av_raw = f"av_{task_id}.mp4"

        try:
            download_file(qa_url, f_qa_img); download_file(ans_url, f_ans_img)
            has_ad = download_file(ad_img_url, f_ad_img)
            has_did = download_file(avatar_url, f_av_raw)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice(script_qa, f_qa_aud))
            loop.run_until_complete(create_voice(script_ans, f_ans_aud))
            if has_ad and script_ad: loop.run_until_complete(create_voice(script_ad, f_ad_aud))
            loop.close()

            qa_clip = AudioFileClip(f_qa_aud)
            s1 = ImageClip(f_qa_img).set_duration(qa_clip.duration).resize((720, 1280)).set_audio(qa_clip)
            s2 = ImageClip(f_qa_img).set_duration(countdown_time).resize((720, 1280))
            if os.path.exists("sfx_countdown.mp3"):
                sfx = AudioFileClip("sfx_countdown.mp3").subclip(0, min(countdown_time, 5))
                s2 = s2.set_audio(sfx)
            ans_clip = AudioFileClip(f_ans_aud)
            s3 = ImageClip(f_ans_img).set_duration(ans_clip.duration).resize((720, 1280)).set_audio(ans_clip)
            main_vid = concatenate_videoclips([s1, s2, s3])

            final_main = main_vid
            if show_avatar:
                av_clip = None
                if has_did:
                    av_clip = get_avatar_clip_stable(f_av_raw, task_id, main_vid.duration)
                
                if not av_clip and os.path.exists("my_avatar.mp4"):
                    av_clip = get_avatar_clip_stable("my_avatar.mp4", task_id + "_fallback", main_vid.duration)

                if av_clip:
                    final_main = CompositeVideoClip([main_vid, av_clip.set_position(("center", "bottom"))])

            if has_ad:
                raw_ad = PIL.Image.open(f_ad_img).convert("RGB")
                bg_ad = raw_ad.resize((720, 1280), PIL.Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(25))
                bg_ad = ImageEnhance.Brightness(bg_ad).enhance(0.5)
                ad_bg = ImageClip(np.array(bg_ad))
                ad_fg = ImageClip(f_ad_img)
                if ad_fg.w / ad_fg.h > 720/1280: ad_fg = ad_fg.resize(width=720)
                else: ad_fg = ad_fg.resize(height=1000)
                s4 = CompositeVideoClip([ad_bg, ad_fg.set_position("center")])
                if os.path.exists(f_ad_aud): s4 = s4.set_duration(AudioFileClip(f_ad_aud).duration).set_audio(AudioFileClip(f_ad_aud))
                else: s4 = s4.set_duration(5)
                final_video = concatenate_videoclips([final_main, s4])
            else:
                final_video = final_main

            final_video.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            url = upload_to_gcs(output_name, task_id)
            if url: requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
            print(f"[{task_id}] 🎉 ภารกิจสำเร็จ!")

        except Exception as e: print(f"[{task_id}] ❌ พัง: {e}")
        finally:
            for f in [f_qa_img, f_ans_img, f_ad_img, f_qa_aud, f_ans_aud, f_ad_aud, f_av_raw, output_name]:
                if os.path.exists(f): os.remove(f)
            if os.path.exists(f"frames_{task_id}"): shutil.rmtree(f"frames_{task_id}")
            if os.path.exists(f"frames_{task_id}_fallback"): shutil.rmtree(f"frames_{task_id}_fallback")
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