from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import praw  # Python Reddit Wrapper
from gtts import gTTS
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip
from dotenv import load_dotenv
import os
import random
import yaml
from datetime import datetime, timezone
import math

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
CORS(app)  # Enable Cross-Origin Resource Sharing for React

# Reddit API setup
reddit = praw.Reddit(
    client_id=os.getenv("CLIENT_ID"),
    client_secret=os.getenv("CLIENT_SECRET"),
    user_agent="thread-2-tok/0.1 by u/Complex_Balance4016"
)

# Load subreddit configuration with tags
SUBREDDITS_FILE = os.path.join(os.path.dirname(__file__), 'subreddits.yaml')

def load_subreddit_config():
    """Load subreddit configuration from YAML file."""
    try:
        with open(SUBREDDITS_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Warning: {SUBREDDITS_FILE} not found. Using empty config.")
        return {}
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}")
        return {}

def get_subreddits_by_tag(tag, config=None):
    """Get list of subreddits that have the specified tag."""
    if config is None:
        config = load_subreddit_config()
    matching = []
    for subreddit, tags in config.items():
        if tag.lower() in [t.lower() for t in tags]:
            matching.append(subreddit)
    return matching

def get_all_tags(config=None):
    """Get all unique tags from the configuration."""
    if config is None:
        config = load_subreddit_config()
    all_tags = set()
    for tags in config.values():
        all_tags.update([t.lower() for t in tags])
    return sorted(all_tags)

def calculate_virality_score(post):
    """
    Calculate a virality score (0-9) for a Reddit post based on multiple metrics.
    
    Metrics and weights:
    - Engagement Rate (comments/upvotes ratio): 30%
    - Upvote Velocity (score relative to subreddit avg): 25%
    - Upvote Ratio (quality indicator): 20%
    - Time Decay (freshness, newer posts score higher): 15%
    - Post Length Score (optimal length for engagement): 10%
    
    Each metric is normalized to 0-9 scale before combining.
    """
    score_components = {}
    
    # 1. Engagement Rate (comments to upvotes ratio)
    # Higher ratio = more engaging/discussion-worthy content
    if post.score > 0:
        engagement_ratio = post.num_comments / post.score
        # Normalize: 0.01 = low engagement, 0.1+ = very high engagement
        engagement_score = min(9, max(0, engagement_ratio * 90))
    else:
        engagement_score = 0
    score_components['engagement'] = engagement_score
    
    # 2. Upvote Velocity (normalized score)
    # Compare post score to typical subreddit performance
    # Assuming avg post has ~100 upvotes in these story subreddits
    avg_expected_score = 100
    velocity_score = min(9, max(0, (post.score / avg_expected_score) * 4.5))
    score_components['velocity'] = velocity_score
    
    # 3. Upvote Ratio (quality indicator)
    # Higher ratio = less controversial, more universally liked
    ratio_score = min(9, max(0, (post.upvote_ratio - 0.5) * 18))
    score_components['quality'] = ratio_score
    
    # 4. Time Decay (freshness bonus)
    # Newer posts get a slight boost
    post_age_hours = (datetime.now(timezone.utc) - datetime.fromtimestamp(post.created_utc, tz=timezone.utc)).total_seconds() / 3600
    if post_age_hours < 2:
        freshness_score = 9
    elif post_age_hours < 6:
        freshness_score = 8
    elif post_age_hours < 12:
        freshness_score = 7
    elif post_age_hours < 24:
        freshness_score = 6
    elif post_age_hours < 48:
        freshness_score = 4
    else:
        freshness_score = 2
    score_components['freshness'] = freshness_score
    
    # 5. Post Length Score (optimal for TikTok narration)
    # Sweet spot: 500-2000 chars for ~1-3 minute videos
    char_count = len(post.selftext)
    if 500 <= char_count <= 1500:
        length_score = 9
    elif 1500 < char_count <= 2500:
        length_score = 8
    elif 300 <= char_count < 500:
        length_score = 6
    elif 2500 < char_count <= 3500:
        length_score = 6
    elif char_count > 3500:
        length_score = 4  # Too long, would need heavy editing
    else:
        length_score = 3  # Too short
    score_components['length'] = length_score
    
    # Calculate weighted final score
    weights = {
        'engagement': 0.30,
        'velocity': 0.25,
        'quality': 0.20,
        'freshness': 0.15,
        'length': 0.10
    }
    
    final_score = sum(score_components[k] * weights[k] for k in weights)
    
    # Round to nearest integer and clamp to 0-9
    return round(min(9, max(0, final_score))), score_components

def fetch_stories_by_tag(tag, limit=10, min_virality=0):
    """
    Fetch stories from subreddits matching a tag, scored by virality.
    
    Args:
        tag: The tag to search for (e.g., 'horror', 'funny')
        limit: Number of posts to fetch per subreddit
        min_virality: Minimum virality score (0-9) to include
    
    Returns:
        List of story dicts with virality scores, sorted by score descending
    """
    config = load_subreddit_config()
    subreddits = get_subreddits_by_tag(tag, config)
    
    if not subreddits:
        return []
    
    stories = []
    
    for subreddit_name in subreddits:
        try:
            subreddit = reddit.subreddit(subreddit_name)
            # Fetch from hot and top to get variety
            posts = list(subreddit.hot(limit=limit)) + list(subreddit.top(time_filter='day', limit=limit//2))
            
            for post in posts:
                if not post.selftext or len(post.selftext) < 100:
                    continue
                    
                virality_score, components = calculate_virality_score(post)
                
                if virality_score >= min_virality:
                    stories.append({
                        'title': post.title,
                        'body': post.selftext,
                        'subreddit': subreddit_name,
                        'author': str(post.author),
                        'score': post.score,
                        'upvote_ratio': post.upvote_ratio,
                        'num_comments': post.num_comments,
                        'url': f"https://reddit.com{post.permalink}",
                        'created_utc': post.created_utc,
                        'virality_score': virality_score,
                        'virality_breakdown': components,
                        'tags': config.get(subreddit_name, [])
                    })
        except Exception as e:
            print(f"Error fetching from r/{subreddit_name}: {e}")
            continue
    
    # Sort by virality score descending, then by upvotes
    stories.sort(key=lambda x: (x['virality_score'], x['score']), reverse=True)
    
    return stories

def fetch_story(subreddit="AmItheAsshole", prefer_high_virality=True):
    """
    Fetch a story from a subreddit, optionally prioritizing high virality.
    """
    subreddit_obj = reddit.subreddit(subreddit)
    posts = [post for post in subreddit_obj.hot(limit=20) if post.selftext and len(post.selftext) >= 100]
    
    if not posts:
        return None
    
    if prefer_high_virality:
        # Score all posts and pick the best one
        scored_posts = []
        for post in posts:
            score, _ = calculate_virality_score(post)
            scored_posts.append((post, score))
        
        # Sort by virality score and pick top 3, then random from those
        scored_posts.sort(key=lambda x: x[1], reverse=True)
        top_posts = scored_posts[:3]
        selected_post, virality = random.choice(top_posts)
    else:
        selected_post = random.choice(posts)
        virality, _ = calculate_virality_score(selected_post)
    
    config = load_subreddit_config()
    
    return {
        "title": selected_post.title,
        "body": selected_post.selftext,
        "subreddit": subreddit,
        "score": selected_post.score,
        "upvote_ratio": selected_post.upvote_ratio,
        "num_comments": selected_post.num_comments,
        "virality_score": virality,
        "tags": config.get(subreddit, [])
    }

# Helper function to generate narration audio
def generate_narration(text, output_file="narration.mp3"):
    """Generate audio from text using gTTS and save as an MP3."""
    try:
        output_file = os.path.join(os.getcwd(), output_file)
        tts = gTTS(text)
        tts.save(output_file)
        return output_file
    except Exception as e:
        print(f"Error generating narration: {e}")
        return None

# Helper function to create a TikTok-compatible video
def create_video(input_video_file, input_audio_file, output_file):
    """Creates a TikTok-style video with a 9:16 aspect ratio and overlays the audio."""
    try:
        output_path = os.path.join(os.getcwd(), output_file)

        # Load video and audio
        video = VideoFileClip(input_video_file)
        audio = AudioFileClip(input_audio_file)

        # Calculate audio duration and select a video slice
        audio_duration = audio.duration
        max_start_time = max(0, video.duration - audio_duration)
        start_time = random.uniform(0, max_start_time)
        end_time = start_time + audio_duration
        video_slice = video.subclip(start_time, end_time)

        # Crop video to fit TikTok's 9:16 aspect ratio
        target_aspect_ratio = 9 / 16
        video_width, video_height = video_slice.size
        current_aspect_ratio = video_width / video_height

        if current_aspect_ratio > target_aspect_ratio:
            # Crop width (landscape video)
            new_width = int(video_height * target_aspect_ratio)
            crop_x1 = (video_width - new_width) // 2
            crop_x2 = crop_x1 + new_width
            video_cropped = video_slice.crop(x1=crop_x1, x2=crop_x2)
        else:
            # Crop height (portrait video)
            new_height = int(video_width / target_aspect_ratio)
            crop_y1 = (video_height - new_height) // 2
            crop_y2 = crop_y1 + new_height
            video_cropped = video_slice.crop(y1=crop_y1, y2=crop_y2)

        # Add audio to the cropped video
        video_with_audio = video_cropped.set_audio(audio)

        # Write the final video
        video_with_audio.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile="temp-audio.m4a",
            remove_temp=True,
            fps=24
        )

        # Ensure the file exists before returning
        return output_path if os.path.exists(output_path) else None
    except Exception as e:
        print(f"Error creating video: {e}")
        return None

# API Endpoints

@app.route('/api/tags', methods=['GET'])
def get_tags():
    """Get all available tags from the subreddit configuration."""
    tags = get_all_tags()
    return jsonify({"tags": tags})

@app.route('/api/subreddits', methods=['GET'])
def get_subreddits():
    """Get all subreddits and their tags."""
    config = load_subreddit_config()
    return jsonify({"subreddits": config})

@app.route('/api/stories/by-tag/<tag>', methods=['GET'])
def get_stories_by_tag_endpoint(tag):
    """
    Fetch stories from subreddits matching a specific tag.
    
    Query params:
    - limit: Number of posts per subreddit (default: 10)
    - min_virality: Minimum virality score 0-9 (default: 0)
    - top_only: If true, return only the single highest-virality story
    """
    limit = request.args.get('limit', 10, type=int)
    min_virality = request.args.get('min_virality', 0, type=int)
    top_only = request.args.get('top_only', 'false').lower() == 'true'
    
    stories = fetch_stories_by_tag(tag, limit=limit, min_virality=min_virality)
    
    if top_only and stories:
        stories = [stories[0]]
    
    return jsonify({
        "tag": tag,
        "count": len(stories),
        "stories": stories
    })

@app.route('/api/story/random', methods=['GET'])
def get_random_story():
    """
    Get a random story from a specific subreddit or by tag.
    
    Query params:
    - subreddit: Specific subreddit name (default: AmItheAsshole)
    - tag: If provided, picks from subreddits with this tag instead
    - prefer_high_virality: If true, uses virality scoring (default: true)
    """
    subreddit = request.args.get('subreddit', 'AmItheAsshole')
    tag = request.args.get('tag')
    prefer_high_virality = request.args.get('prefer_high_virality', 'true').lower() == 'true'
    
    if tag:
        # Fetch by tag and pick best one
        stories = fetch_stories_by_tag(tag, limit=10, min_virality=0)
        if not stories:
            return jsonify({"error": f"No stories found for tag '{tag}'"}), 404
        story = stories[0]  # Already sorted by virality
    else:
        story = fetch_story(subreddit, prefer_high_virality=prefer_high_virality)
    
    if story:
        return jsonify(story)
    return jsonify({"error": "No story found"}), 404

@app.route('/api/virality/analyze', methods=['POST'])
def analyze_virality():
    """
    Analyze the virality potential of provided text.
    
    Body: {"title": "...", "body": "..."}
    Returns virality score estimate based on text characteristics.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    title = data.get('title', '')
    body = data.get('body', '')
    full_text = f"{title} {body}"
    
    # Simple text-based virality indicators
    char_count = len(body)
    word_count = len(body.split())
    has_hook = any(word in title.lower() for word in ['aita', 'tifu', 'update', 'revenge', 'crazy', 'insane', 'shocking'])
    
    # Score based on length and hooks
    if 500 <= char_count <= 1500:
        length_score = 9
    elif 1500 < char_count <= 2500:
        length_score = 8
    elif 300 <= char_count < 500:
        length_score = 6
    elif 2500 < char_count <= 3500:
        length_score = 6
    elif char_count > 3500:
        length_score = 4
    else:
        length_score = 3
    
    hook_bonus = 2 if has_hook else 0
    estimated_score = min(9, length_score + hook_bonus)
    
    return jsonify({
        "estimated_virality_score": estimated_score,
        "character_count": char_count,
        "word_count": word_count,
        "has_hook_words": has_hook,
        "length_score": length_score
    })

if __name__ == "__main__":
    print("Fetching a story from Reddit and generating video...")

    # Fetch a story from the specified subreddit
    subreddit_name = "AmItheAsshole"
    story = fetch_story(subreddit_name)

    if story:
        print("Story fetched: ", story)
        # Combine the title and body for narration
        narration_text = f"{story['title']} {story['body']}"

        # File paths
        input_video = os.path.join(os.getcwd(), "backend/static/minecraft_background.mp4")  # Path to test video
        input_audio = os.path.join(os.getcwd(), "narration.mp3")  # Path to generated narration audio
        output_video = os.path.join(os.getcwd(), "generated_video.mp4")  # Output video file name

        # Generate narration audio from the fetched story
        narration_path = generate_narration(narration_text, input_audio)

        # Create the video with the narration audio
        if narration_path and os.path.exists(input_video):
            print("Creating video...")
            video_path = create_video(input_video, input_audio, output_video)
            if video_path:
                print(f"Video successfully created: {video_path}")
            else:
                print("Error: Video generation failed.")
        else:
            print("Error: Input video or narration file not found.")
    else:
        print(f"No stories found in the subreddit '{subreddit_name}'.")