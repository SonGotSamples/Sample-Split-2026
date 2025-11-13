# 🎼 Sample Split Stem Processor

This project allows you to **split music tracks into individual stems** (vocals, drums, bass, etc.) and **automatically upload each stem** to specific YouTube channels based on the stem type.

---

## 🔧 Setup Instructions (Windows)

1. **Clone this repository** and open **CMD or PowerShell** inside the project folder.
2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   ```
3. **Activate the virtual environment:**
   ```bash
   venv\Scripts\activate
   ```
4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## ▶️ Running the FastAPI Server

After activating the environment, run the API server:

```bash
uvicorn tk:app --reload
```

This starts the server at:  
**http://127.0.0.1:8000**

---

## ⚙️ Running the Celery Worker

```bash
PYTHONPATH=$(pwd) celery -A celery_worker worker --loglevel=info
```

---

## 📁 Project Structure

- `tk.py`: Main FastAPI entry point.
- `content_download_*`: Each class handles a specific stem type (e.g., vocals, drums, sample split).
- `yt_video_multi.py`: Handles video upload to YouTube using the correct channel/token based on the stem.
- `shared_state.py`: Used to track progress across requests.
- `yt_tokens/`: Contains JSON token files for authenticated YouTube uploads.
- `separated/`: Directory where Demucs stores the extracted stems.
- `MP4/`: Final rendered video files.

---

## 💡 Notes

- This project uses [Demucs](https://github.com/facebookresearch/demucs) to separate stems.
- Make sure `ffmpeg` and `ImageMagick` are installed and working in your Windows system path.
- YouTube upload tokens must be authorized and placed inside the `yt_tokens` folder.

---

Generated for client use. Let us know if you face any issues setting it up.