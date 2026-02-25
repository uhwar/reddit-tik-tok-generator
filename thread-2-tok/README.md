# Reddit Story to Short Video Generator

** work in progress **

### Project Motivation

Social media platforms like TikTok, YouTube Shorts, and Instagram Reels are dominating the entertainment space with quick, engaging content that captures millions of views. A popular trend involves creators narrating dramatic Reddit stories (like r/AmItheAsshole posts) over dynamic visuals such as Minecraft parkour. These videos have proven to be highly lucrative for content creators.

As a CS student, I thought:
**“Why spend hours creating these videos manually when I can automate the process?”**


## What Does This Project Do?

This application automates the process of creating short-form videos by:
1. **Scraping Reddit Stories**: Fetches popular stories from threads like r/AmItheAsshole.
2. **Generating Narration**: Converts the story text into AI-generated narrator audio.
3. **Creating Videos**: Combines narration with a Minecraft parkour or other background video.
4. **Providing Downloads**: Outputs a completed, ready-to-post video in MP4 format.


## Key Features

- **Multi-Platform Compatibility**: Produces videos optimized for TikTok, YouTube Shorts, Instagram Reels, etc.
- **Reddit Integration**: Dynamically fetches top stories from subreddits like r/AmItheAsshole.
- **Text-to-Speech**: Generates smooth, natural-sounding voiceovers using gTTS.
- **Video Automation**: Combines narration and engaging backgrounds into a single polished video.
- **Effortless Output**: Users can generate videos with minimal effort and share them instantly.


## Technologies Used

### Backend:
- **Python**:
  - **PRAW**: Reddit API integration for story scraping.
  - **gTTS**: Converts text to audio for narration.
  - **moviepy**: Handles video editing, background cropping, and audio integration.
  - **Flask**: Provides a simple API for interacting with the app.

### Tools:
- **FFmpeg**: Processes video and audio files efficiently.


## How It Works

1. **Story Selection**:
   - Fetches a random popular story from a subreddit like r/AmItheAsshole.
2. **Narration**:
   - Converts the story's title and body into a smooth voiceover using gTTS.
3. **Video Creation**:
   - Combines the narration with a dynamic background video.
   - Crops the video to a 9:16 aspect ratio for TikTok compatibility.
4. **Output**:
   - Generates a ready-to-post video in MP4 format.

## How to Use

1. **Setup**:
   - Clone the repository.
   - Navigate to the `backend` directory and set up the virtual environment:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     pip install -r requirements.txt
     ```

2. **Environment Variables**:
   - Create a `.env` file in the `backend` directory with the following keys:
     ```plaintext
     CLIENT_ID=your_reddit_client_id
     CLIENT_SECRET=your_reddit_client_secret
     REDIRECT_URI=your_redirect_uri
     ```
   - Replace the placeholders with your Reddit API credentials.
     
3. **Background Video Usage**:
   
- **Download the `.zip` File:**
  
   Download the background video file (`minecraft_background.mp4.zip`) at:
  
   **OR upload your own mp4 file for the video background in `backend/static` and move on to step 4**
  
- **Unzip the File:**
   - **On Windows:** Right-click the `.zip` file and select **Extract All...**.
   - **On macOS:** Double-click the `.zip` file to extract its contents.
   - **On Linux:** Use the `unzip` command in the terminal:
     ```bash
     unzip minecraft_background.mp4.zip
     ```
  
- **Place the Video:**
   Ensure the extracted file (`minecraft_background.mp4`) is placed in the correct directory as specified by the application configuration (e.g., `backend/static`).

4. **Run the App**:
   - Navigate to the root directory:
     ```bash
     backend/venv/bin/python backend/app.py
     ```
   - The script will fetch a story, generate narration, and create the video.

5. **Output**:
   - The final video (`generated_video.mp4`) and audio (`narration.mp3`) files will appear in the root directory.

## 📜 License

This project is open-source under the MIT License.
