import sys
sys.stdout.reconfigure(line_buffering=True)
import os, threading, uuid, requests, asyncio, nest_asyncio, gc, json, numpy as np, time, subprocess, shutil
from flask import Flask, request, jsonify

app = Flask(__name__) 

import PIL.Image
from PIL import ImageFilter, ImageEnhance
if not hasattr(PIL.Image, 'ANTIALIAS'): 
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

# 🚀 บังคับใช้ FFmpeg ให้เสถียรบน Railway
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = "ffmpeg"
    print(f"✅ Master Engine: FFmpeg Path Active")
except Exception as e:
    FFMPEG_PATH = "ffmpeg"

from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
import moviepy.video.fx.all as vfx
import edge_tts
from google.cloud import storage
import datetime

nest_asyncio.apply()

# 🔗 Configuration
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") # 🟢 ใช้ OpenAI แทนแล้ว
render_semaphore = threading.Semaphore(1) 

# 🎙️ ระบบเสียงพากย์ OpenAI TTS (สำเนียงไทยแท้ 100%)
async def generate_voice(text, filename, use_premium, task_id):
    if not text or str(text).strip() == "": return False
    try:
        if use_premium and OPENAI_API_KEY:
            print(f"[{task_id}] 🎙️ OpenAI TTS (Shimmer) กำลังพากย์ไทย...")
            url = "https://api.openai.com/v1/audio/speech"
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "tts-1",
                "input": str(text),
                "voice": "shimmer" # 🟢 เสียงผู้หญิงไทยที่นวลและเป็นธรรมชาติที่สุด
            }
            r = requests.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                with open(filename, 'wb') as f: f.write(r.content)
                return True
            print(f"[{task_id}] ⚠️ OpenAI Error ({r.status_code}): {r.text}")
        
        # Fallback: Edge-TTS (ฟรี)
        print(f"[{task_id}] 🎙️ สลับใช้ Edge-TTS...")
        await edge_tts.Communicate(str(text), "th-TH-NiwatNeural").save(filename)
        return True
    except Exception as e:
        print(f"❌ TTS Error: {e}")
        return False

# 🛠️ ระบบจัดการ Avatar (Image Sequence Stable)
def get_avatar_clip_stable(video_path, task_id, target_duration):
    temp_dir = f"frames_{task_id}"
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    try:
        cmd = [FFMPEG_PATH, '-y', '-i', video_path, '-vf', "scale=trunc(iw/2)*2:trunc(ih/2)*2", f"{temp_dir}/f_%04d.png"]
        subprocess.run(cmd, check=True, capture_output=True)
        frames = [f"{temp_dir}/{img}" for img in sorted(os.listdir(temp_dir))]
        if not frames: return None
        
        clip = ImageSequenceClip(frames, fps=24).resize(height=450)
        clip = clip.fx(vfx.mask_color, color=[0, 255, 0], thr=140, s=10) # เจาะเขียว
        
        if clip.duration < target_duration:
            clip = concatenate_videoclips([clip, clip.to_ImageClip(t=clip.duration-0.1).set_duration(target_duration-clip.duration)])
        else:
            clip = clip.set_duration(target_duration)
        return clip
    except: return None

# ---------------------------------------------------------
# 🎞️ MASTER RENDER ENGINE (OpenAI Version)
# ---------------------------------------------------------
def process_master_video(task_id, qa_url, ans_url, ad_url, av_url, script_qa, script_ans, script_ad, countdown, use_premium, show_avatar):
    task_id = str(task_id); output_name = f"final_{task_id}.mp4"
    print(f"\n==================== 🎬 เริ่มงาน: {task_id} ====================")

    with render_semaphore:
        try:
            # 1. Download Resources
            def dl(u, f): 
                if not u or str(u).lower() == "none" or str(u).strip() == "": return False
                try:
                    r = requests.get(u, timeout=30)
                    if r.status_code == 200:
                        with open(f, 'wb') as file: file.write(r.content)
                        return True
                except: return False
                return False

            dl(qa_url, f"qa_{task_id}.png"); dl(ans_url, f"ans_{task_id}.png")
            has_ad = dl(ad_url, f"ad_{task_id}.png")
            has_av_file = show_avatar and dl(av_url, f"av_{task_id}.mp4")

            # 2. Generate Audio (OpenAI)
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(generate_voice(script_qa, f"v_qa_{task_id}.mp3", use_premium, task_id))
            loop.run_until_complete(generate_voice(script_ans, f"v_ans_{task_id}.mp3", use_premium, task_id))
            if has_ad: loop.run_until_complete(generate_voice(script_ad, f"v_ad_{task_id}.mp3", use_premium, task_id))
            loop.close()

            # --- Scene 1: โจทย์ (พากย์ + Intro SFX) ---
            v_qa = AudioFileClip(f"v_qa_{task_id}.mp3")
            s1 = ImageClip(f"qa_{task_id}.png").set_duration(v_qa.duration).resize((1080, 1920))
            a1_list = [v_qa]
            if os.path.exists("sfx_intro.mp3"):
                sfx_intro = AudioFileClip("sfx_intro.mp3").volumex(0.3)
                # 🛑 แก้ปัญหา SFX สั้นกว่าพากย์
                s1_sfx = sfx_intro.subclip(0, v_qa.duration) if sfx_intro.duration > v_qa.duration else sfx_intro
                a1_list.append(s1_sfx)
            s1 = s1.set_audio(CompositeAudioClip(a1_list))

            # --- Scene 2: นับถอยหลัง (Countdown SFX) ---
            s2 = ImageClip(f"qa_{task_id}.png").set_duration(countdown).resize((1080, 1920))
            if os.path.exists("sfx_countdown.mp3"):
                sfx_cd = AudioFileClip("sfx_countdown.mp3")
                # 🛑 วนลูปถ้า SFX สั้นกว่าเวลาถอยหลัง
                a2_final = audio_loop(sfx_cd, duration=countdown) if sfx_cd.duration < countdown else sfx_cd.subclip(0, countdown)
                s2 = s2.set_audio(a2_final)

            # --- Scene 3: เฉลย (พากย์ + Correct SFX) ---
            v_ans = AudioFileClip(f"v_ans_{task_id}.mp3")
            s3 = ImageClip(f"ans_{task_id}.png").set_duration(v_ans.duration).resize((1080, 1920))
            a3_list = [v_ans]
            if os.path.exists("sfx_correct.mp3"):
                sfx_cor = AudioFileClip("sfx_correct.mp3").volumex(0.5)
                s3_sfx = sfx_cor.subclip(0, v_ans.duration) if sfx_cor.duration > v_ans.duration else sfx_cor
                a3_list.append(s3_sfx)
            s3 = s3.set_audio(CompositeAudioClip(a3_list))

            main_vid = concatenate_videoclips([s1, s2, s3])

            # 👤 ซ้อน Avatar
            final_main = main_vid
            if show_avatar:
                av = None
                if has_av_file: av = get_avatar_clip_stable(f"av_{task_id}.mp4", task_id, main_vid.duration)
                elif os.path.exists("my_avatar.mp4"): av = get_avatar_clip_stable("my_avatar.mp4", task_id+"_f", main_vid.duration)
                if av: final_main = CompositeVideoClip([main_vid, av.set_position(("right", "bottom"))])

            # 📺 โฆษณา
            if has_ad:
                ad_chain = []
                if os.path.exists("sfx_transition.mp3"):
                    ad_chain.append(ColorClip((1080,1920),(0,0,0)).set_duration(0.2).set_audio(AudioFileClip("sfx_transition.mp3")))
                
                raw_ad = PIL.Image.open(f"ad_{task_id}.png").convert("RGB")
                bg = ImageClip(np.array(raw_ad.resize((1080, 1920)).filter(ImageFilter.GaussianBlur(25))))
                fg = ImageClip(f"ad_{task_id}.png").resize(width=1080) if raw_ad.size[0]/raw_ad.size[1] > 1080/1920 else ImageClip(f"ad_{task_id}.png").resize(height=1600)
                s4 = CompositeVideoClip([bg, fg.set_position("center")])
                v_ad = AudioFileClip(f"v_ad_{task_id}.mp3")
                s4 = s4.set_duration(v_ad.duration).set_audio(v_ad)
                ad_chain.append(s4)
                final_video = concatenate_videoclips([final_main] + ad_chain)
            else: 
                final_video = final_main

            # 🎵 ใส่ BGM คลอทั้งคลิป (bgm_main)
            if os.path.exists("bgm_main.mp3"):
                bgm = audio_loop(AudioFileClip("bgm_main.mp3").volumex(0.12), duration=final_video.duration)
                final_video = final_video.set_audio(CompositeAudioClip([final_video.audio, bgm]))

            # ⚙️ เรนเดอร์
            final_video.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            # ☁️ อัปโหลด
            gcs_json = os.environ.get("GCS_KEY_JSON")
            client = storage.Client.from_service_account_info(json.loads(gcs_json))
            blob = client.bucket(BUCKET_NAME).blob(output_name); blob.upload_from_filename(output_name)
            url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12))
            
            requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
            print(f"[{task_id}] 🎉 ภารกิจสำเร็จ!")

        except Exception as e: print(f"❌ Error: {e}")
        finally:
            for f in [f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png", f"v_qa_{task_id}.mp3", f"v_ans_{task_id}.mp3", f"v_ad_{task_id}.mp3", f"av_{task_id}.mp4", output_name]:
                if os.path.exists(f): os.remove(f)
            for d in [f"frames_{task_id}", f"frames_{task_id}_f"]:
                if os.path.exists(d): shutil.rmtree(d)
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render():
    d = request.json; task_id = str(uuid.uuid4())
    # รองรับพารามิเตอร์ทั้งชื่อเก่าและใหม่
    use_p = d.get('use_premium_voice') or d.get('use_elevenlabs', False)
    threading.Thread(target=process_master_video, args=(
        task_id, d.get('qa_image_url'), d.get('ans_image_url'), d.get('ad_image_url'),
        d.get('avatar_video_url'), d.get('script_qa'), d.get('script_ans'), d.get('script_ad'),
        int(d.get('countdown_time', 5)), use_p, d.get('show_avatar', False)
    )).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))