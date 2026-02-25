from moviepy.editor import VideoFileClip, AudioFileClip
import random

def create_video_with_audio(input_video_file, input_audio_file, output_file):
    """Creates a video slice matching the audio duration and overlays the audio."""
    try:
        # Load the video file
        video = VideoFileClip(input_video_file)
        
        # Load the audio file
        audio = AudioFileClip(input_audio_file)
        
        # Get the duration of the audio file
        audio_duration = audio.duration
        
        # Ensure video duration is sufficient for the audio
        max_start_time = max(0, video.duration - audio_duration)
        
        # Randomly select a start time for the video slice
        start_time = random.uniform(0, max_start_time)
        end_time = start_time + audio_duration
        
        # Create a subclip of the video
        video_slice = video.subclip(start_time, end_time)
        
        # Overlay the audio onto the video
        video_with_audio = video_slice.set_audio(audio)
        
        # Save the final video
        video_with_audio.write_videofile(output_file, codec="libx264", audio_codec="aac")
        print(f"Video with audio created successfully: {output_file}")
    except Exception as e:
        print(f"Error creating video with audio: {e}")

if __name__ == "__main__":
    # Input video and audio files
    input_video = "backend/static/minecraft_background.mp4"  # Replace with your actual video path
    input_audio = "narration.mp3"  # Replace with your actual audio path
    output_video = "test_video_with_audio.mp4"

    # Create the video with audio overlay
    create_video_with_audio(input_video, input_audio, output_video)