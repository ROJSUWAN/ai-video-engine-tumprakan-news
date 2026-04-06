import moviepy.video.fx.all as vfx

# ---------------------------------------------------------
# 🎞️ ระบบ Render Q&A อัตโนมัติ (เลือกใส่ Avatar ได้)
# ---------------------------------------------------------
def process_native_video(task_id, bg_url, qa_url, script, countdown_time, start_time, show_avatar):
    task_id = str(task_id)
    print(f"[{task_id}] ⏳ Native Render queued (Avatar: {show_avatar})...")
    
    with render_semaphore:
        output_name = f"final_native_{task_id}.mp4"
        bg_file = f"bg_{task_id}.jpg"
        qa_file = f"qa_{task_id}.png"
        audio_file = f"voice_{task_id}.mp3"
        avatar_path = "my_avatar.mp4" # <--- ไฟล์ฉากเขียวที่คุณตั้มอัปโหลดไว้

        try:
            # 1. โหลดวัตถุดิบรูปลงมาที่ Server
            download_image_from_url(bg_url, bg_file)
            download_image_from_url(qa_url, qa_file)
            
            # 2. สร้างเสียงพากย์จาก Script
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(create_voice_safe(script, audio_file))
            loop.close()

            audio = AudioFileClip(audio_file)
            duration = audio.duration
            
            # 3. เตรียม Layer ต่างๆ
            # Layer 1: พื้นหลังจาก Kie AI
            bg_clip = ImageClip(bg_file).set_duration(duration).resize((720, 1280))
            
            # Layer 2: ป้ายคำถาม Q&A จาก HCTI (วางค่อนไปด้านบนนิดนึง)
            qa_clip = ImageClip(qa_file).set_duration(duration).resize(width=680).set_position(("center", 150))
            
            layers = [bg_clip, qa_clip]

            # Layer 3: Avatar ฉากเขียว (ใส่เฉพาะถ้าติ๊ก Form ว่า "ใช้" และมีไฟล์อยู่จริง)
            if show_avatar and os.path.exists(avatar_path):
                print(f"[{task_id}] 👤 Applying Green Screen Avatar...")
                avatar_clip = VideoFileClip(avatar_path).loop(duration=duration).resize(height=500)
                # 🟢 พระเอกอยู่ตรงนี้: เจาะฉากเขียว (#00FF00) ออกให้โปร่งใส
                avatar_clip = avatar_clip.fx(vfx.mask_color, color=[0, 255, 0], thr=100, s=5)
                # วางไว้ตรงกลาง ด้านล่างสุด
                avatar_clip = avatar_clip.set_position(("center", "bottom"))
                layers.append(avatar_clip)

            # Layer 4: ตัวเลขนับถอยหลัง
            timer_clips = []
            for i in range(countdown_time, 0, -1):
                tc = create_timer_clip(i, size=(720, 1280))
                if tc:
                    tc = tc.set_start(start_time + (countdown_time - i)).set_duration(1)
                    timer_clips.append(tc)
            layers.extend(timer_clips)

            # Layer 5: เสียง Effect นาฬิกาจับเวลา (ถ้ามี)
            sfx_path = "sfx_countdown.mp3"
            if os.path.exists(sfx_path):
                sfx_clip = AudioFileClip(sfx_path).subclip(0, min(countdown_time, AudioFileClip(sfx_path).duration)).set_start(start_time).volumex(0.6)
                final_audio = CompositeAudioClip([audio, sfx_clip])
            else:
                final_audio = audio

            # 4. ประกอบร่างทุก Layer
            print(f"[{task_id}] 🎬 Rendering video...")
            final_video = CompositeVideoClip(layers).set_audio(final_audio)
            
            # 5. สั่ง Export ไฟล์วิดีโอ (เรนเดอร์)
            final_video.write_videofile(output_name, fps=24, codec='libx264', preset='ultrafast', logger=None)
            
            # 6. อัปโหลดขึ้น Google Cloud และยิง Webhook แจ้งกลับ n8n
            url = upload_to_gcs(output_name)
            if url:
                requests.post(N8N_WEBHOOK_URL, json={'id': task_id, 'final_url': url, 'status': 'success'}, timeout=20)
                print(f"[{task_id}] ✅ Uploaded and Webhook sent!")

            # คืนพื้นที่ RAM
            final_video.close(); bg_clip.close(); qa_clip.close(); audio.close()
            if show_avatar and os.path.exists(avatar_path): avatar_clip.close()
            for t in timer_clips: t.close()
            if os.path.exists(sfx_path): sfx_clip.close()

        except Exception as e:
            print(f"❌ Native Render Error [{task_id}]: {e}")
        finally:
            try: os.remove(bg_file)
            except: pass
            try: os.remove(qa_file)
            except: pass
            try: os.remove(output_name)
            except: pass
            gc.collect()

# API รับคำสั่งจาก n8n
@app.route('/render-native', methods=['POST'])
def api_render_native():
    data = request.json
    task_id = str(uuid.uuid4())
    bg_url = data.get('bg_image_url')
    qa_url = data.get('qa_image_url')
    script = data.get('script', 'ไม่มีสคริปต์')
    countdown_time = int(data.get('countdown_time', 5))
    start_time = float(data.get('start_time', 10.0))
    show_avatar = data.get('show_avatar', False) # รับค่า True/False จาก n8n

    if not bg_url or not qa_url:
        return jsonify({"error": "Missing image URLs"}), 400

    threading.Thread(target=process_native_video, args=(task_id, bg_url, qa_url, script, countdown_time, start_time, show_avatar)).start()
    return jsonify({"status": "processing", "task_id": task_id, "mode": "native"}), 202