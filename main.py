import sys
sys.stdout.reconfigure(line_buffering=True)
import os, threading, uuid, requests, asyncio, nest_asyncio, gc, json, numpy as np, time, subprocess, shutil
from flask import Flask, request, jsonify

app = Flask(__name__) 

import PIL.Image
from PIL import ImageFilter
if not hasattr(PIL.Image, 'ANTIALIAS'): 
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

# 🚀 ตั้งค่าเครื่องยนต์ FFmpeg
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = "ffmpeg"
    print(f"✅ FFmpeg System: Ready")
except: 
    FFMPEG_PATH = "ffmpeg"

from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
import moviepy.video.fx.all as vfx
import edge_tts
from google.cloud import storage, texttospeech
import datetime

nest_asyncio.apply()

# 🔗 Global Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
GCS_KEY_JSON = os.environ.get("GCS_KEY_JSON") 
render_semaphore = threading.Semaphore(1)

# 🎙️ ระบบเสียงพากย์ Google Cloud TTS (ไทยชัด 100% สำเนียงกรุงเทพ)
async def generate_voice(text, filename, use_premium, task_id):
    if not text or str(text).strip() == "": return False
    
    # 🔵 ชั้นที่ 1: Google Cloud TTS (พรีเมียม)
    if use_premium and GCS_KEY_JSON:
        try:
            print(f"[{task_id}] 🎙️ Google Cloud TTS (Standard-A) พากย์ไทยแท้...")
            client = texttospeech.TextToSpeechClient.from_service_account_info(json.loads(GCS_KEY_JSON))
            synthesis_input = texttospeech.SynthesisInput(text=str(text))
            
            # ใช้ th-TH-Standard-A เพื่อความชัวร์ว่ามีทุกโปรเจกต์ (สำเนียงคนไทยปกติ)
            voice = texttospeech.VoiceSelectionParams(
                language_code="th-TH",
                name="th-TH-Standard-A" 
            )
            
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                pitch=0.0,
                speaking_rate=1.05 # เร่งจังหวะนิดนึงให้คลิปไม่ยาน
            )
            
            response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
            with open(filename, "wb") as out:
                out.write(response.audio_content)
            return True
        except Exception as e:
            print(f"⚠️ Google TTS Fail: {e} -> สลับไปใช้ Edge-TTS สำรอง")

    # 🔴 ชั้นที่ 2: Edge-TTS (Fallback กันงานล่ม)
    try:
        print(f"[{task_id}] 🎙️ ใช้ Edge-TTS (สำรอง)...")
        await edge_tts.Communicate(str(text), "th-TH-NiwatNeural").save(filename)
        return True
    except Exception as e:
        print(f"❌ Critical Error: เจนเสียงไม่ได้เลย: {e}")
        return False

# 🛠️ ระบบจัดการ Avatar (Stable Frame Method)
def get_avatar_clip_stable(video_path, task_id, target_duration):
    temp_dir = f"frames_{task_id}"
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    try:
        # ระเบิดวิดีโอเป็นรูปเพื่อกันปัญหาไฟล์ D-ID เน่า
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
# 🎞️ MASTER RENDER ENGINE (The Final One)
# ---------------------------------------------------------
def process_master_video(task_id, qa_url, ans_url, ad_url, av_url, script_qa, script_ans, script_ad, countdown, use_premium, show_avatar):
    task_id = str(task_id); output_name = f"final_{task_id}.mp4"
    print(f"[{task_id}] เริ่มงานเรนเดอร์ (Master Version)")

    with render_semaphore:
        try:
            # 1. Download
            def dl(u, f):
                if not u or str(u).lower() == "none" or str(u).strip() == "": return False
                r = requests.get(u, timeout=60)
                if r.status_code == 200:
                    with open(f, 'wb') as file: file.write(r.content)
                    return True
                return False

            dl(qa_url, f"qa_{task_id}.png"); dl(ans_url, f"ans_{task_id}.png")
            has_ad = dl(ad_url, f"ad_{task_id}.png")
            has_av = show_avatar and dl(av_url, f"av_{task_id}.mp4")

            # 2. Generate All Audios
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(generate_voice(script_qa, f"v_qa_{task_id}.mp3", use_premium, task_id))
            loop.run_until_complete(generate_voice(script_ans, f"v_ans_{task_id}.mp3", use_premium, task_id))
            if has_ad: loop.run_until_complete(generate_voice(script_ad, f"v_ad_{task_id}.mp3", use_premium, task_id))
            loop.close()

            # --- Scene 1: คำถาม (พากย์ + Intro) ---
            v_qa = AudioFileClip(f"v_qa_{task_id}.mp3")
            s1 = ImageClip(f"qa_{task_id}.png").set_duration(v_qa.duration).resize((1080, 1920))
            a1_ly = [v_qa]
            if os.path.exists("sfx_intro.mp3"):
                sfx = AudioFileClip("sfx_intro.mp3").volumex(0.3)
                a1_ly.append(sfx.subclip(0, v_qa.duration) if sfx.duration > v_qa.duration else sfx)
            s1 = s1.set_audio(CompositeAudioClip(a1_ly))

            # --- Scene 2: นับถอยหลัง (Countdown) ---
            s2 = ImageClip(f"qa_{task_id}.png").set_duration(countdown).resize((1080, 1920))
            if os.path.exists("sfx_countdown.mp3"):
                sfx_cd = AudioFileClip("sfx_countdown.mp3")
                s2 = s2.set_audio(audio_loop(sfx_cd, duration=countdown) if sfx_cd.duration < countdown else sfx_cd.subclip(0, countdown))

            # --- Scene 3: เฉลย (พากย์ + Correct) ---
            v_ans = AudioFileClip(f"v_ans_{task_id}.mp3")
            s3 = ImageClip(f"ans_{task_id}.png").set_duration(v_ans.duration).resize((1080, 1920))
            a3_ly = [v_ans]
            if os.path.exists("sfx_correct.mp3"):
                sfx_c = AudioFileClip("sfx_correct.mp3").volumex(0.5)
                a3_ly.append(sfx_c.subclip(0, v_ans.duration) if sfx_c.duration > v_ans.duration else sfx_c)
            s3 = s3.set_audio(CompositeAudioClip(a3_ly))

            final_v = concatenate_videoclips([s1, s2, s3])
            
            # 👤 ซ้อน Avatar
            if show_avatar:
                av = get_avatar_clip_stable(f"av_{task_id}.mp4", task_id, final_v.duration) if has_av else None
                if av: final_v = CompositeVideoClip([final_v, av.set_position(("right", "bottom"))])

            # 📺 ฉากโฆษณา
            if has_ad:
                ad_parts = []
                if os.path.exists("sfx_transition.mp3"):
                    ad_parts.append(ColorClip((1080,1920),(0,0,0)).set_duration(0.2).set_audio(AudioFileClip("sfx_transition.mp3")))
                
                raw_ad = PIL.Image.open(f"ad_{task_id}.png").convert("RGB")
                bg = ImageClip(np.array(raw_ad.resize((1080, 1920)).filter(ImageFilter.GaussianBlur(25))))
                fg = ImageClip(f"ad_{task_id}.png").resize(width=1080) if raw_ad.size[0]/raw_ad.size[1] > 1080/1920 else ImageClip(f"ad_{task_id}.png").resize(height=1600)
                v_ad = AudioFileClip(f"v_ad_{task_id}.mp3")
                s4 = CompositeVideoClip([bg, fg.set_position("center")]).set_duration(v_ad.duration).set_audio(v_ad)
                ad_parts.append(s4)
                final_v = concatenate_videoclips([final_v] + ad_parts)

            # 🎵 BGM คลอตลอดคลิป
            if os.path.exists("bgm_main.mp3"):
                bgm = audio_loop(AudioFileClip("bgm_main.mp3").volumex(0.12), duration=final_v.duration)
                final_v = final_v.set_audio(CompositeAudioClip([final_v.audio, bgm]))

            # ⚙️ เรนเดอร์
            final_v.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            # ☁️ อัปโหลด
            client = storage.Client.from_service_account_info(json.loads(GCS_KEY_JSON))
            blob = client.bucket(BUCKET_NAME).blob(output_name); blob.upload_from_filename(output_name)
            url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12))
            
            requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
            print(f"[{task_id}] 🎉 สำเร็จ! เสียงไทยแท้มาแล้ว!")

        except Exception as e: print(f"❌ Error: {e}")
        finally:
            for f in [f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png", f"v_qa_{task_id}.mp3", f"v_ans_{task_id}.mp3", f"v_ad_{task_id}.mp3", f"av_{task_id}.mp4", output_name]:
                if os.path.exists(f): os.remove(f)
            if os.path.exists(f"frames_{task_id}"): shutil.rmtree(f"frames_{task_id}")
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render():
    d = request.json; task_id = str(uuid.uuid4())
    use_p = d.get('use_premium_voice') or d.get('use_elevenlabs', False)
    threading.Thread(target=process_master_video, args=(
        task_id, d.get('qa_image_url'), d.get('ans_image_url'), d.get('ad_image_url'),
        d.get('avatar_video_url'), d.get('script_qa'), d.get('script_ans'), d.get('script_ad'),
        int(d.get('countdown_time', 5)), use_p, d.get('show_avatar', False)
    )).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))