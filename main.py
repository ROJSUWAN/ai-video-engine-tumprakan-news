import sys
sys.stdout.reconfigure(line_buffering=True)
import os, threading, uuid, requests, asyncio, nest_asyncio, gc, json, numpy as np, time, subprocess, shutil
from flask import Flask, request, jsonify
app = Flask(__name__) 

import PIL.Image
from PIL import ImageFilter, ImageEnhance
if not hasattr(PIL.Image, 'ANTIALIAS'): PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

# 🚀 บังคับใช้ FFmpeg จาก static-ffmpeg
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    FFMPEG_PATH = static_ffmpeg.run.get_command_path("ffmpeg")
    from moviepy.config import change_settings
    change_settings({"FFMPEG_BINARY": FFMPEG_PATH})
    print(f"✅ Master Engine: FFmpeg Path Located at {FFMPEG_PATH}")
except: FFMPEG_PATH = "ffmpeg"

from moviepy.editor import *
from moviepy.audio.fx.all import audio_loop
import moviepy.video.fx.all as vfx
import edge_tts
from google.cloud import storage
import datetime
nest_asyncio.apply()

# 🔗 Config
N8N_WEBHOOK_URL = "https://primary-production-f87f.up.railway.app/webhook/video-completed" 
BUCKET_NAME = "n8n-video-tumprakan-news" 
ELEVEN_API_KEY = os.environ.get("ELEVEN_API_KEY") 

# 🎙️ ระบบเสียงพากย์ Hybrid
async def generate_voice(text, filename, use_eleven, task_id):
    if not text: return False
    try:
        if use_eleven and ELEVEN_API_KEY:
            print(f"[{task_id}] 🎙️ ElevenLabs พากย์อยู่...")
            url = f"https://api.elevenlabs.io/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"
            headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
            r = requests.post(url, json={"text": str(text), "model_id": "eleven_multilingual_v2"}, headers=headers)
            if r.status_code == 200:
                with open(filename, 'wb') as f: f.write(r.content)
                return True
        print(f"[{task_id}] 🎙️ Edge-TTS พากย์อยู่...")
        await edge_tts.Communicate(str(text), "th-TH-NiwatNeural").save(filename)
        return True
    except: return False

# 🛠️ ระบบซ่อมและดึงเฟรมอวตาร (Image Sequence Stable)
def get_avatar_clip_stable(video_path, task_id, target_duration):
    temp_dir = f"frames_{task_id}"
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    try:
        cmd = [FFMPEG_PATH, '-y', '-i', video_path, '-vf', "scale=trunc(iw/2)*2:trunc(ih/2)*2", f"{temp_dir}/f_%04d.png"]
        subprocess.run(cmd, check=True, capture_output=True)
        frames = [f"{temp_dir}/{img}" for img in sorted(os.listdir(temp_dir))]
        if not frames: return None
        clip = ImageSequenceClip(frames, fps=24).resize(height=450) # ย่อลงพอดีๆ ไม่บังจอ
        clip = clip.fx(vfx.mask_color, color=[0, 255, 0], thr=140, s=10) # เจาะเขียวกริ๊บ
        if clip.duration < target_duration:
            last_f = clip.to_ImageClip(t=clip.duration-0.1).set_duration(target_duration-clip.duration)
            clip = concatenate_videoclips([clip, last_f])
        else: clip = clip.set_duration(target_duration)
        return clip
    except: return None

# ---------------------------------------------------------
# 🎞️ MASTER RENDER ENGINE
# ---------------------------------------------------------
def process_master_video(task_id, qa_url, ans_url, ad_url, av_url, script_qa, script_ans, script_ad, countdown, use_eleven, show_avatar):
    task_id = str(task_id); output_name = f"final_{task_id}.mp4"
    print(f"\n==================== 🎬 เริ่มงาน: {task_id} (Avatar: {show_avatar}) ====================")
    
    with render_semaphore:
        try:
            # 1. โหลดทรัพยากร
            def dl(u, f): return requests.get(u).status_code == 200 and open(f, 'wb').write(requests.get(u).content)
            dl(qa_url, f"qa_{task_id}.png"); dl(ans_url, f"ans_{task_id}.png")
            has_ad = ad_url and dl(ad_url, f"ad_{task_id}.png")
            has_av_file = show_avatar and av_url and dl(av_url, f"av_{task_id}.mp4")

            # 2. เสียงพากย์
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            loop.run_until_complete(generate_voice(script_qa, f"v_qa_{task_id}.mp3", use_eleven, task_id))
            loop.run_until_complete(generate_voice(script_ans, f"v_ans_{task_id}.mp3", use_eleven, task_id))
            if has_ad: loop.run_until_complete(generate_voice(script_ad, f"v_ad_{task_id}.mp3", use_eleven, task_id))
            loop.close()

            # --- สร้าง Scene ---
            v_qa = AudioFileClip(f"v_qa_{task_id}.mp3")
            s1 = ImageClip(f"qa_{task_id}.png").set_duration(v_qa.duration).resize((1080, 1920))
            a1 = [v_qa]
            if os.path.exists("sfx_intro.mp3"): a1.append(AudioFileClip("sfx_intro.mp3").volumex(0.3).set_duration(v_qa.duration))
            s1 = s1.set_audio(CompositeAudioClip(a1))

            s2 = ImageClip(f"qa_{task_id}.png").set_duration(countdown).resize((1080, 1920))
            if os.path.exists("sfx_countdown.mp3"): s2 = s2.set_audio(AudioFileClip("sfx_countdown.mp3").set_duration(countdown))

            v_ans = AudioFileClip(f"v_ans_{task_id}.mp3")
            s3 = ImageClip(f"ans_{task_id}.png").set_duration(v_ans.duration).resize((1080, 1920))
            a3 = [v_ans]
            if os.path.exists("sfx_correct.mp3"): a3.append(AudioFileClip("sfx_correct.mp3").volumex(0.5).set_duration(v_ans.duration))
            s3 = s3.set_audio(CompositeAudioClip(a3))

            main_vid = concatenate_videoclips([s1, s2, s3])

            # 👤 ซ้อน Avatar (ถ้าพี่สั่งมา)
            final_main = main_vid
            if show_avatar:
                av_clip = None
                if has_av_file: av_clip = get_avatar_clip_stable(f"av_{task_id}.mp4", task_id, main_vid.duration)
                elif os.path.exists("my_avatar.mp4"): av_clip = get_avatar_clip_stable("my_avatar.mp4", task_id+"_f", main_vid.duration)
                if av_clip: final_main = CompositeVideoClip([main_vid, av_clip.set_position(("right", "bottom"))])

            # 📺 ฉากโฆษณา
            if has_ad:
                if os.path.exists("sfx_transition.mp3"):
                    final_main = concatenate_videoclips([final_main, ColorClip((1080,1920),(0,0,0)).set_duration(0.2).set_audio(AudioFileClip("sfx_transition.mp3"))])
                raw_ad = PIL.Image.open(f"ad_{task_id}.png").convert("RGB")
                bg = ImageClip(np.array(raw_ad.resize((1080, 1920)).filter(ImageFilter.GaussianBlur(25))))
                fg = ImageClip(f"ad_{task_id}.png").resize(width=1080) if raw_ad.size[0]/raw_ad.size[1] > 1080/1920 else ImageClip(f"ad_{task_id}.png").resize(height=1600)
                s4 = CompositeVideoClip([bg, fg.set_position("center")])
                v_ad = AudioFileClip(f"v_ad_{task_id}.mp3")
                s4 = s4.set_duration(v_ad.duration).set_audio(v_ad)
                final_video = concatenate_videoclips([final_main, s4])
            else: final_video = final_main

            # 🎵 BGM
            if os.path.exists("bgm_main.mp3"):
                bgm = audio_loop(AudioFileClip("bgm_main.mp3").volumex(0.12), duration=final_video.duration)
                final_video = final_video.set_audio(CompositeAudioClip([final_video.audio, bgm]))

            # ⚙️ Render
            final_video.write_videofile(output_name, fps=24, codec='libx264', audio_codec='aac', preset='ultrafast', logger=None)
            
            gcs_json = os.environ.get("GCS_KEY_JSON")
            client = storage.Client.from_service_account_info(json.loads(gcs_json))
            blob = client.bucket(BUCKET_NAME).blob(output_name)
            blob.upload_from_filename(output_name)
            url = blob.generate_signed_url(version="v4", expiration=datetime.timedelta(hours=12))
            requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
            print(f"[{task_id}] 🎉 เสร็จแล้วพี่!")

        except Exception as e: print(f"❌ Error: {e}")
        finally:
            for f in [f"qa_{task_id}.png", f"ans_{task_id}.png", f"ad_{task_id}.png", f"v_qa_{task_id}.mp3", f"v_ans_{task_id}.mp3", f"v_ad_{task_id}.mp3", f"av_{task_id}.mp4", output_name]:
                if os.path.exists(f): os.remove(f)
            if os.path.exists(f"frames_{task_id}"): shutil.rmtree(f"frames_{task_id}")
            gc.collect()

@app.route('/render-native', methods=['POST'])
def api_render():
    d = request.json; task_id = str(uuid.uuid4())
    threading.Thread(target=process_master_video, args=(
        task_id, d.get('qa_image_url'), d.get('ans_image_url'), d.get('ad_image_url'),
        d.get('avatar_video_url'), d.get('script_qa'), d.get('script_ans'), d.get('script_ad'),
        int(d.get('countdown_time', 5)), d.get('use_elevenlabs', False), d.get('show_avatar', False)
    )).start()
    return jsonify({"status": "processing", "task_id": task_id}), 202

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))