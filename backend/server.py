import os
import re
import requests
import yt_dlp
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import concurrent.futures
# import random
import difflib

app = Flask(__name__)
CORS(app)

# Configuration
DOWNLOAD_FOLDER = os.path.join(os.getcwd(), 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

GLOBAL_OFFSET = 0.1

def parse_lrc(lrc_content):
    lyrics = []
    if not lrc_content:
        return []
        
    lrc_regex = re.compile(r'\[(\d{2}):(\d{2})\.?(\d{2,3})?\](.*)')
    
    lines = lrc_content.split('\n')
    for line in lines:
        try:
            match = lrc_regex.match(line)
            if not match: continue
            
            minutos = int(match.group(1))
            segundos = int(match.group(2))
            frac_str = match.group(3) if match.group(3) else "0"
            
            if len(frac_str) == 2:
                frac_seg = int(frac_str) * 10
            else:
                frac_seg = int(frac_str)
                
            texto = match.group(4).strip()
            # Add GLOBAL_OFFSET to correct for typical YouTube intro delays (lyrics appearing "too early")
            total_seconds = (minutos * 60) + segundos + (frac_seg / 1000) + GLOBAL_OFFSET
            
            if texto:
                lyrics.append({"time": total_seconds, "text": texto})
        except Exception:
            continue
            
    lyrics.sort(key=lambda x: x["time"])
    return lyrics

@app.route('/search', methods=['GET'])
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        def fetch_youtube_candidates(q, limit=15):
            ydl_opts = {
                'format': 'bestaudio/best',
                'default_search': f'ytsearch{limit}',
                'quiet': True,
                'noplaylist': True,
                'extract_flat': False
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    result = ydl.extract_info(q, download=False)
                    if 'entries' in result:
                        return result['entries']
                except Exception as e:
                    pass
                    # print(f"Youtube Search Error: {e}")
            return []

        def fetch_lyrics_candidates_broad(q):
            session = requests.Session()
            try:
                # Basic broad search
                resp = session.get(f"https://lrclib.net/api/search?q={q}", timeout=5)
                return resp.json()
            except:
                return []
        
        # ... (unchanged helpers)

        def clean_youtube_title(title):
            # Remove specific keywords even without parentheses
            title = re.sub(r'(?i)\b(official|video|mv|m/v)\b', '', title)
            title = re.sub(r'(?i)\(.*?official.*?\)', '', title)
            title = re.sub(r'(?i)\(.*?video.*?\)', '', title)
            title = re.sub(r'(?i)\(.*?lyric.*?\)', '', title)
            title = re.sub(r'(?i)\(.*?visualizer.*?\)', '', title)
            title = re.sub(r'(?i)\(.*?audio.*?\)', '', title)
            title = re.sub(r'(?i)\(.*?live.*?\)', '', title)
            title = re.sub(r'(?i)\[.*?\]', '', title)        
            title = re.sub(r'(?i)ft\..*', '', title)
            title = re.sub(r'(?i)feat\..*', '', title)
            title = re.sub(r'[^\w\s]', '', title) # Remove punctuation
            return title.strip().lower()

        # --- EXECUTION ---
        video_candidates = []
        lyrics_candidates = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_yt = executor.submit(fetch_youtube_candidates, query, 15)
            future_lyrics = executor.submit(fetch_lyrics_candidates_broad, query)
            
            video_candidates = future_yt.result()
            lyrics_candidates = future_lyrics.result()

        if not video_candidates:
            return jsonify({"error": "Video not found / Video no encontrado"}), 404

        # --- TYPO CORRECTION (Refined Search) ---
        if video_candidates:
            top_vid_title = clean_youtube_title(video_candidates[0].get('title', ''))
            # print(f"Refining lyrics search using video title: {top_vid_title}")
            refined_lyrics = fetch_lyrics_candidates_broad(top_vid_title)
            if refined_lyrics:
                lyrics_candidates.extend(refined_lyrics)

        selected_video = None
        selected_lyrics = None
        best_diff_log = float('inf')

        # Matching function
        def find_best_match(videos, lyrics_list, relaxed_match=False):
            valid_l = [l for l in lyrics_list if l.get('syncedLyrics') and l.get('duration')]
            if not valid_l: return None, None, float('inf')

            sel_vid, sel_lyr, b_diff = None, None, float('inf')
            
            def similarity_score(s1, s2):
                if not s1 or not s2: return 0
                return difflib.SequenceMatcher(None, s1, s2).ratio()

            for v in videos:
                if not v.get('duration'): continue
                
                vid_title_clean = clean_youtube_title(v.get('title', ''))
                
                for l in valid_l:
                    diff = abs(v['duration'] - l['duration'])
                    
                    # Check text similarity if metadata is available in lyric object
                    l_track = clean_youtube_title(l.get('trackName', ''))
                    l_artist = clean_youtube_title(l.get('artistName', ''))
                    
                    text_match_bonus = 0.0
                    if l_track:
                        if l_track in vid_title_clean or vid_title_clean in l_track:
                            text_match_bonus = 2.0
                        elif similarity_score(l_track, vid_title_clean) > 0.6:
                            text_match_bonus = 1.0

                    tolerated_diff = 2.0 
                    if relaxed_match: tolerated_diff = 5.0
                    if text_match_bonus > 0: tolerated_diff += 3.0 

                    if diff < tolerated_diff:
                        current_score = diff - (text_match_bonus * 5)
                        best_score = b_diff - (0 if sel_vid else 100) 

                        if current_score < best_score or sel_vid is None:
                             sel_vid, sel_lyr, b_diff = v, l['syncedLyrics'], diff
            
            # Last ditch: Desperate duration match (ALWAYS RUN THIS, avoiding regressions for songs with long intros)
            if not sel_vid:
                 # Threshold: 15s for relaxed fallbacks (Netease/Mega), 8.0s for primary search
                 threshold = 15.0 if relaxed_match else 8.0
                 
                 for v in videos:
                    if not v.get('duration'): continue
                    for l in valid_l:
                        diff = abs(v['duration'] - l['duration'])
                        
                        # If we have a text match, be even more lenient
                        l_track = clean_youtube_title(l.get('trackName', ''))
                        text_match = False
                        if l_track and (l_track in vid_title_clean or vid_title_clean in l_track):
                             text_match = True
                        
                        final_threshold = threshold + (5.0 if text_match else 0)

                        if diff < final_threshold and diff < b_diff:
                             sel_vid, sel_lyr, b_diff = v, l['syncedLyrics'], diff

            return sel_vid, sel_lyr, b_diff

        # 1. ATTEMPT: Broad Search Matches
        # Increase relaxation for "Blood on the Dance Floor" cases where video might be slightly longer (intro)
        selected_video, selected_lyrics, b_diff = find_best_match(video_candidates, lyrics_candidates, relaxed_match=False)
        best_diff_log = b_diff

        # 1.1 VALIDATION: Duration Mismatch Fix & MV Avoidance
        if selected_video and selected_lyrics:
            lyric_obj = next((l for l in lyrics_candidates if l['syncedLyrics'] == selected_lyrics), None)
            if lyric_obj:
                l_dur = lyric_obj.get('duration', 0)
                v_dur = selected_video.get('duration', 0)
                raw_t = selected_video.get('title', '').lower()
                
                mismatch = abs(v_dur - l_dur) > 8.0
                is_mv = any(x in raw_t for x in ['official', 'mv', 'm/v'])
                
                # If mismatch OR it's an MV (which might have hidden intros), try to find Audio
                if mismatch or is_mv:
                    reason = "Duration Mismatch" if mismatch else "MV Detected"
                    # print(f"Validation Triggered: {reason}. (Vid: {v_dur}s, Lyr: {l_dur}s)")
                    # print(f"Original Video: {raw_t}")

                    audio_candidates = fetch_youtube_candidates(query + " audio", limit=5)
                    av, al, ad = find_best_match(audio_candidates, [lyric_obj], relaxed_match=True)
                    
                    if av:
                        # print(f"Audio candidate found: {av.get('title')} (Diff: {ad}s)")
                        
                        # LOGIC:
                        # 1. If it's an MV, we REALLY want to swap if the Audio is decent (diff < 5s).
                        #    We don't care if the MV 'matched' duration better, because MVs deceive.
                        # 2. If it was just a Duration Mismatch, we only swap if Audio is strictly better/good.
                        
                        should_swap = False
                        if is_mv and ad < 5.0:
                            should_swap = True
                            # print("Swapping because Original is MV and Audio is valid.")
                        elif ad < 8.0 and ad < b_diff + 5.0:
                             should_swap = True
                             # print("Swapping because Audio provides better/similar duration match.")

                        if should_swap:
                            selected_video = av
                            best_diff_log = ad
                            # print(f"Swapped to: {av.get('title')}")

        # 1.5 ATTEMPT: Smart Metadata 
        if not selected_video:
            # print("Trying Smart Metadata Search on Top 3 Videos...")
            for vid in video_candidates[:3]:
                if selected_video: break 
                
                vid_dur = vid.get('duration', 0)
                raw_title = vid.get('title', '')
                clean_t = clean_youtube_title(raw_title)
                
                meta_a = vid.get('artist') or vid.get('uploader')
                meta_t = vid.get('track')
                
                possible_pairs = []
                if meta_a and meta_t: possible_pairs.append((meta_t, meta_a))
                if "-" in clean_t:
                    parts = clean_t.split("-")
                    if len(parts) >= 2:
                        possible_pairs.append((parts[1].strip(), parts[0].strip()))
                        possible_pairs.append((parts[0].strip(), parts[1].strip()))
                possible_pairs.append((clean_t, meta_a))

                for (track_n, artist_n) in possible_pairs:
                    if not track_n or not artist_n: continue
                    
                    # Precise
                    l_precise = fetch_lrclib_precise(track_name=track_n, artist_name=artist_n, duration=vid_dur)
                    sv, sl, bd = find_best_match([vid], l_precise, relaxed_match=False)
                    if sv:
                        selected_video, selected_lyrics, best_diff_log = sv, sl, bd
                        break
                    
                    # Structured
                    if not selected_video:
                        l_struct = fetch_lrclib_structured(track_name=track_n, artist_name=artist_n)
                        sv, sl, bd = find_best_match([vid], l_struct, relaxed_match=False)
                        if sv:
                            selected_video, selected_lyrics, best_diff_log = sv, sl, bd
                            break
                        
        # 2. ATTEMPT: Netease
        if not selected_video:
            # print("Trying Netease Fallback...")
            l_net = fetch_netease_candidates(query)
            sv, sl, bd = find_best_match(video_candidates, l_net, relaxed_match=True)
            if sv: selected_video, selected_lyrics, best_diff_log = sv, sl, bd

        # 3. ATTEMPT: Megalobiz
        if not selected_video:
             # print("Trying Megalobiz Fallback...")
             l_mega = fetch_megalobiz_candidates(query)
             sv, sl, bd = find_best_match(video_candidates, l_mega, relaxed_match=True)
             if sv: selected_video, selected_lyrics, best_diff_log = sv, sl, bd

        # FINAL FALLBACK: If we have a video but NO lyrics
        if not selected_video:
             # Just pick the first video result as it's likely the right song
             selected_video = video_candidates[0]
        
        if not selected_lyrics:
            # print("No lyrics found. Using fallback placeholder.")
            selected_lyrics = "[00:00.00] 🎵 Lyrics not yet available / Letra no disponible 🎵\n[00:05.00] (Music is playing...)\n[99:00.00] End"

        # Download
        video_id = selected_video.get('id')
        filename = f"{video_id}.mp3"
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
        
        if not os.path.exists(filepath):
            ydl_download = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(DOWNLOAD_FOLDER, video_id),
                'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '128'}],
                'quiet': True, 'no_warnings': True
            }
            with yt_dlp.YoutubeDL(ydl_download) as ydl:
                ydl.download([selected_video.get('url')])

        return jsonify({
            "title": selected_video.get('title'),
            "artist": selected_video.get('uploader'),
            "duration": selected_video.get('duration'),
            "lyrics": parse_lrc(selected_lyrics),
            "audio_url": f"http://localhost:5001/stream/{filename}",
            "cover_url": selected_video.get('thumbnail')
        })

    except Exception as e:
        # print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/stream/<filename>')
def stream_audio(filename):
    return send_file(os.path.join(DOWNLOAD_FOLDER, filename))

@app.route('/shutdown', methods=['GET'])
def shutdown():
    print("Shutting down server...")
    os._exit(0)

if __name__ == '__main__':
    app.run(port=5001, debug=False)
