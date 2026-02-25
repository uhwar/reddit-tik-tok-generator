from moviepy.editor import VideoFileClip

def create_video_slice(input_file, output_file, start_time, duration):
    """Creates a short slice of the video."""
    try:
        # Load the video file
        video = VideoFileClip(input_file)
        
        # Calculate end time
        end_time = min(start_time + duration, video.duration)
        
        # Create a subclip
        video_slice = video.subclip(start_time, end_time)
        
        # Save the sliced video
        video_slice.write_videofile(output_file, codec="libx264")
        print(f"Video slice created successfully: {output_file}")
    except Exception as e:
        print(f"Error creating video slice: {e}")

if __name__ == "__main__":
    # Input and output video files
    input_video = "backend/static/reencoded_background.mp4"  # Replace with your actual path
    output_video = "video_slice_test.mp4"
    
    # Parameters for slicing
    start = 0  # Start at the beginning of the video
    slice_duration = 10  # Duration of the slice in seconds

    # Create the video slice
    create_video_slice(input_video, output_video, start, slice_duration)