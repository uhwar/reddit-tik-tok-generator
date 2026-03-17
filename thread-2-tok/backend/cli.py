#!/usr/bin/env python3
"""
Reddit Story Selector CLI
Interactive console application for selecting subreddits and stories by virality score.
"""

import os
import random
import sys
import yaml
import praw
from datetime import datetime, timezone
from dotenv import load_dotenv
from video_generator import (
    generate_video, list_background_videos, VOICE_OPTIONS,
    DURATION_MODES, estimate_duration_seconds
)

# Load environment variables
load_dotenv()

# Reddit API setup
reddit = praw.Reddit(
    client_id=os.getenv("CLIENT_ID"),
    client_secret=os.getenv("CLIENT_SECRET"),
    user_agent="thread-2-tok-cli/0.1"
)

# Configuration file path
SUBREDDITS_FILE = os.path.join(os.path.dirname(__file__), 'subreddits.yaml')

POPULAR_FETCH_TARGET = 10000   # per feed pull – totals ~30k stories per sub
NICHE_FETCH_TARGET = 10000
PAGE_SIZE = 10
MIN_COMMENTS = 50

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


def get_all_tags(config=None):
    """Get all unique tags from the configuration."""
    if config is None:
        config = load_subreddit_config()
    all_tags = set()
    for tags in config.values():
        # Convert all tags to strings in case YAML parses them as other types (e.g., booleans)
        all_tags.update([str(t).lower() for t in tags])
    return sorted(all_tags)


def get_subreddits_by_tag(tag, config=None):
    """Get list of subreddits that have the specified tag."""
    if config is None:
        config = load_subreddit_config()
    matching = []
    for subreddit, tags in config.items():
        if tag.lower() in [str(t).lower() for t in tags]:
            matching.append(subreddit)
    return matching


def _normalize(value, peak):
    return min(1.0, float(value) / peak) if peak else 0.0


def calculate_virality_score(post):
    """Updated virality scale with heavier weight on comments/replies."""
    upvotes = max(0, post.score)
    comments = max(0, post.num_comments)
    awards = max(0, getattr(post, 'total_awards_received', 0))
    age_hours = (datetime.now(timezone.utc) - datetime.fromtimestamp(post.created_utc, tz=timezone.utc)).total_seconds() / 3600

    comment_score = _normalize(comments, 400)  # 400+ comments -> 1.0
    vote_score = _normalize(upvotes, 20000)
    award_score = _normalize(awards * 5, 100)
    freshness = 1.0 if age_hours <= 4 else max(0.1, 1 - (age_hours / 72))
    discussion_ratio = _normalize(comments * 2, upvotes + 1)

    weighted = (
        comment_score * 0.35 +
        vote_score * 0.25 +
        discussion_ratio * 0.15 +
        freshness * 0.15 +
        award_score * 0.10
    )
    score = max(1, min(9, int(round(weighted * 9))))
    breakdown = {
        'upvotes': upvotes,
        'comments': comments,
        'awards': awards,
        'comment_weight': comment_score,
        'vote_weight': vote_score,
        'freshness': freshness,
        'total_interactions': upvotes + comments + (awards * 10),
    }
    return score, breakdown


def _build_story_dict(post, subreddit_name, config):
    """Helper to build a story dict from a PRAW post object."""
    virality_score, breakdown = calculate_virality_score(post)
    full_text = f"{post.title}. {post.selftext}"
    return {
        'title': post.title,
        'body': post.selftext[:300] + '...' if len(post.selftext) > 300 else post.selftext,
        'full_body': post.selftext,
        'subreddit': subreddit_name,
        'author': str(post.author),
        'score': post.score,
        'upvote_ratio': post.upvote_ratio,
        'num_comments': post.num_comments,
        'url': f'https://reddit.com{post.permalink}',
        'created_utc': post.created_utc,
        'virality_score': virality_score,
        'virality_breakdown': breakdown,
        'estimated_seconds': estimate_duration_seconds(full_text),
        'tags': config.get(subreddit_name, [])
    }


# Subreddits with large enough post volumes to support wider scraping
_POPULAR_SUBS = {
    'amItheasshole', 'relationship_advice', 'tifu', 'askreddit',
    'nosleep', 'antiwork', 'offmychest', 'trueoffmychest',
    'maliciouscompliance', 'pettyrevenge', 'prorevenge', 'confession',
    'confessions', 'funny', 'todayilearned',
}


def _fetch_sub_posts(sub, limit):
    """Pull posts from multiple feeds until we approach the desired volume."""
    seen = set()
    posts = []

    feeds = [
        lambda l: sub.hot(limit=l),
        lambda l: sub.top(time_filter='week', limit=l),
        lambda l: sub.top(time_filter='month', limit=l),
        lambda l: sub.top(time_filter='year', limit=max(10, l // 2)),
        lambda l: sub.new(limit=max(10, l // 2)),
    ]

    per_feed = min(100, max(50, limit))

    for fetch in feeds:
        try:
            for p in fetch(per_feed):
                if p.id in seen:
                    continue
                seen.add(p.id)
                posts.append(p)
                if len(posts) >= limit:
                    return posts
        except Exception:
            continue
    return posts


def fetch_stories_by_tag(tag, min_virality=0, max_seconds=None, allow_split=False):
    """
    Fetch stories from all subreddits matching a tag.
    Popular subs: hot+top(week/month/year)+new with limit=50 -> ~150-200 candidates.
    Niche subs:   hot+top(week/month/year)+new with limit=15 -> ~40-60 candidates.
    If nothing meets min_virality, returns the best available anyway.
    """
    config = load_subreddit_config()
    subreddits = get_subreddits_by_tag(tag, config)

    if not subreddits:
        print(f"No subreddits found with tag '{tag}'")
        return []

    stories = []
    seen_ids = set()

    duration_cap = max_seconds if (max_seconds and not allow_split) else None
    skipped_by_length = 0
    skipped_by_comments = 0

    for subreddit_name in subreddits:
        try:
            sub = reddit.subreddit(subreddit_name)
            is_popular = subreddit_name.lower() in _POPULAR_SUBS
            limit = POPULAR_FETCH_TARGET if is_popular else NICHE_FETCH_TARGET
            # Increase limits for girly tags
            if tag in ['girly_general', 'girly_targeted'] and subreddit_name.lower() in [s.lower() for s in _POPULAR_SUBS]:
                limit = max(limit, 500)  # Increase popular subs for girly
            elif tag == 'girly_targeted':
                limit = max(limit, 200)  # Increase niche women subs
            print(f"  Fetching r/{subreddit_name} ({'popular' if is_popular else 'niche'}, limit={limit})...")
            feed = _fetch_sub_posts(sub, limit)
            for post in feed:
                if post.id in seen_ids:
                    continue
                if not post.selftext or post.selftext.strip() in ('', '[removed]', '[deleted]'):
                    continue
                if post.num_comments < MIN_COMMENTS:
                    skipped_by_comments += 1
                    continue
                seen_ids.add(post.id)
                story = _build_story_dict(post, subreddit_name, config)
                # Apply girly filter for girly_general tag only
                if tag == 'girly_general':
                    title_lower = story['title'].lower()
                    female_patterns = [
                        r'\bf\d+',  # F25, F 25, etc.
                        r'\bfemale',  # Female
                        r'\bwoman',  # Woman
                        r'\bgirl',  # Girl
                        r'\d+\s*f\b',  # 25F, 25 F
                        r'\(f[^m]',  # (F, (F25, not (M
                        r'^\s*f\d+',  # F25 at start
                        r'^\s*female',  # Female at start
                    ]
                    import re
                    # Must match female pattern AND not match male pattern
                    has_female = any(re.search(pattern, title_lower) for pattern in female_patterns)
                    has_male = re.search(r'\bm\d+', title_lower) or re.search(r'\bmale', title_lower) or re.search(r'\(m', title_lower)
                    if not has_female or has_male:
                        continue
                if duration_cap and story['estimated_seconds'] > duration_cap:
                    skipped_by_length += 1
                    continue
                stories.append(story)
        except Exception as e:
            print(f"  Skipping r/{subreddit_name}: {e}")
            continue

    print(f"  Total kept: {len(stories)} | skipped short-comments: {skipped_by_comments} | over-length: {skipped_by_length}")

    # Sort best first
    stories.sort(key=lambda x: (x['virality_score'], x['score']), reverse=True)

    # Filter by min_virality - but if nothing passes, return best available with a warning
    filtered = [s for s in stories if s['virality_score'] >= min_virality]
    if not filtered and stories:
        print(f"  No stories hit virality {min_virality}. Best available score: {stories[0]['virality_score']}/9")
        return stories

    return filtered


def display_tags():
    """Display all available tags."""
    config = load_subreddit_config()
    tags = get_all_tags(config)
    
    print("\n" + "="*50)
    print("AVAILABLE TAGS")
    print("="*50)
    
    for i, tag in enumerate(tags, 1):
        subreddits = get_subreddits_by_tag(tag, config)
        print(f"{i:2}. {tag:15} ({len(subreddits)} subreddits)")
    
    return tags


def display_stories(stories, max_seconds=120, duration_label="Under 2 minutes", offset=0, randomize=False):
    """Display up to PAGE_SIZE stories (sequential or random)."""
    if not stories:
        print("\nNo stories found matching criteria.")
        return None

    if randomize:
        sample_count = min(PAGE_SIZE, len(stories))
        page = random.sample(stories, sample_count)
        offset_label = "RANDOMIZED PICKS"
    else:
        page = stories[offset:offset + PAGE_SIZE]
        if not page:
            print("\nNo more stories available.")
            return None
        showing_end = min(offset + PAGE_SIZE, len(stories))
        offset_label = f"STORIES {offset+1}-{showing_end} of {len(stories)}"
    print("\n" + "="*80)
    print(f"{offset_label}  |  Mode: {duration_label}")
    print("="*80)

    allow_split_mode = DURATION_MODES.get(
        next((k for k, v in DURATION_MODES.items() if v['label'] == duration_label), "1"), {}
    ).get('allow_split', False)

    for i, story in enumerate(page, 1):
        vb = story['virality_breakdown']

        raw = f"{story['title']}. {story.get('full_body', story['body'])}"
        est_secs = estimate_duration_seconds(raw)
        if allow_split_mode:
            parts = max(1, int(est_secs // max_seconds) + (1 if est_secs % max_seconds > 5 else 0))
        else:
            if est_secs > max_seconds:
                continue
            parts = 1
        parts_label = f"videos: {parts}"

        print(f"\n{'='*80}")
        print(f"[{i}]  VIRALITY: {story['virality_score']}/9  |  r/{story['subreddit']}  |  {duration_label} -> {parts_label}")
        print(f"{'='*80}")
        print(f"TITLE:   {story['title']}")
        print(f"AUTHOR:  u/{story['author']}")
        print(f"STATS:   {story['score']} upvotes | {story['num_comments']} comments | {story['upvote_ratio']*100:.0f}% upvoted | {vb['awards']} awards")
        est_mins = est_secs / 60
        print(f"TOTAL INTERACTIONS: {vb['total_interactions']:,} | EST. LENGTH: ~{est_mins:.1f} min")
        print(f"PREVIEW:")
        preview = story['body'][:300] if len(story['body']) > 300 else story['body']
        print(f"  \"{preview}\"")
        print(f"{'-'*80}")

    return page


def select_story_interactive(stories, has_more=False, multi=False, allow_random=False):
    """Let user select a story from the displayed page.
    Returns (selection, action) where action can be 'more', 'random', or None.
    If multi=True, selection is a list.
    """
    if not stories:
        return [] if multi else (None), None

    more_hint = ", (m)ore" if has_more else ""
    random_hint = ", (r)andom" if allow_random else ""
    batch_hint = " or (b)atch select" if multi else ""
    prompt = f"\nSelect story number (1-{len(stories)}){more_hint}{random_hint}{batch_hint} or 'q' to quit: "

    while True:
        try:
            choice = input(prompt).strip().lower()
            if choice == 'q':
                return [] if multi else (None), None
            if choice == 'm' and has_more:
                return [] if multi else (None), 'more'
            if choice == 'r' and allow_random:
                return [] if multi else (None), 'random'
            if choice == 'b' and multi:
                return select_multiple_stories(stories), None
            idx = int(choice) - 1
            if 0 <= idx < len(stories):
                return [stories[idx]] if multi else stories[idx], None
            else:
                print(f"Invalid selection. Please choose 1-{len(stories)}.")
        except ValueError:
            print("Invalid input. Please enter a number or 'q'.")


def select_multiple_stories(stories):
    """Allow user to select multiple stories by entering numbers separated by commas or ranges."""
    print("\nBatch selection mode:")
    print("  - Enter numbers separated by commas: 1,3,5")
    print("  - Enter ranges: 1-3")
    print("  - Mix: 1,3-5,7")
    print("  - 'all' to select all stories on this page")
    print("  - 'back' to return to single selection")
    
    while True:
        choice = input("\nEnter selections: ").strip().lower()
        if choice == 'back':
            return []
        if choice == 'all':
            return stories
        
        try:
            selected = []
            parts = choice.split(',')
            for part in parts:
                part = part.strip()
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    for i in range(start, end + 1):
                        if 1 <= i <= len(stories):
                            selected.append(stories[i-1])
                else:
                    i = int(part)
                    if 1 <= i <= len(stories):
                        selected.append(stories[i-1])
            
            if selected:
                print(f"Selected {len(selected)} stories.")
                return selected
            else:
                print("No valid selections. Try again.")
        except ValueError:
            print("Invalid format. Use numbers like 1,3,5 or ranges like 1-3.")


def view_full_story(story):
    """Display full story content."""
    print("\n" + "="*80)
    print(f"FULL STORY - Virality: {story['virality_score']}/9")
    print("="*80)
    print(f"Title: {story['title']}")
    print(f"Subreddit: r/{story['subreddit']}")
    print(f"URL: {story['url']}")
    print(f"Author: u/{story['author']}")
    print(f"Stats: {story['score']} upvotes | {story['num_comments']} comments | {story['upvote_ratio']*100:.1f}% ratio")
    print("-"*80)
    print(story['full_body'])
    print("="*80)


def main_menu():
    """Main CLI menu."""
    while True:
        print("\n" + "="*50)
        print("REDDIT STORY SELECTOR CLI")
        print("="*50)
        print("1. Browse stories by tag")
        print("2. List all tags")
        print("3. List all subreddits")
        print("4. Search specific subreddit")
        print("5. Exit")
        print("-"*50)
        
        choice = input("Select option (1-5): ").strip()
        
        if choice == "1":
            browse_by_tag()
        elif choice == "2":
            display_tags()
        elif choice == "3":
            list_all_subreddits()
        elif choice == "4":
            search_subreddit()
        elif choice == "5":
            print("\nGoodbye!")
            sys.exit(0)
        else:
            print("Invalid option. Please try again.")


def _select_duration_mode():
    """Ask the user to pick a video duration mode.
    Returns (duration_key, max_seconds, label, allow_split).
    """
    print("\n" + "="*60)
    print("VIDEO LENGTH PREFERENCE")
    print("="*60)
    print("  1. Under 2 minutes  (1 video, story trimmed to fit)")
    print("  2. Under 3 minutes  (1 video, story trimmed to fit)")
    print("  3. Under 5 minutes  (splits into multiple parts if needed)")
    choice = input("\nSelect (1-3, default 1): ").strip() or "1"
    if choice not in DURATION_MODES:
        print("Invalid choice, defaulting to under 2 minutes.")
        choice = "1"
    mode = DURATION_MODES[choice]
    print(f"Selected: {mode['label']}")
    return choice, mode['max_seconds'], mode['label'], mode['allow_split']


def browse_by_tag():
    """Browse stories by selecting a tag."""

    # Ask duration preference before fetching — used for part-count labels
    # and passed straight through to video generation
    duration_key, max_seconds, duration_label, allow_split = _select_duration_mode()

    tags = display_tags()
    
    if not tags:
        print("No tags found in configuration.")
        return
    
    tag_input = input("\nEnter tag name or number: ").strip().lower()
    
    if tag_input.isdigit():
        choice = int(tag_input) - 1
        if 0 <= choice < len(tags):
            selected_tag = tags[choice]
        else:
            print("Invalid number.")
            return
    else:
        if tag_input in [t.lower() for t in tags]:
            selected_tag = tag_input
        else:
            print("Invalid tag name.")
            return

    # Step 3: Fetch and display stories
    min_virality = int(input("Minimum virality score (1-9, default 4): ") or "4")
    print(f"\nFetching stories for tag '{selected_tag}'...")
    stories = fetch_stories_by_tag(
        selected_tag,
        min_virality=min_virality,
        max_seconds=max_seconds,
        allow_split=allow_split,
    )

    if not stories:
        print("\nNo stories found. Try a different tag or check your Reddit API credentials.")
        return

    offset = 0
    random_mode = False
    while True:
        page = display_stories(
            stories,
            max_seconds=max_seconds,
            duration_label=duration_label,
            offset=offset,
            randomize=random_mode,
        )
        if not page:
            print("No more stories to show.")
            break

        has_more = (offset + PAGE_SIZE) < len(stories) and not random_mode
        selected_stories, action = select_story_interactive(
            page,
            has_more=has_more,
            multi=True,
            allow_random=True,
        )

        if action == 'more':
            offset += PAGE_SIZE
            random_mode = False
            continue
        if action == 'random':
            random_mode = True
            continue
        random_mode = False

        if not selected_stories:
            break

        # If single story selected, show full story first
        if len(selected_stories) == 1:
            view_full_story(selected_stories[0])
            # Post-view options
            while True:
                action = input("\n[Options] (s)ave to file, (g)enerate video, (b)ack to list, (q)uit: ").strip().lower()
                if action == 's':
                    save_story_to_file(selected_stories[0])
                elif action == 'g':
                    generate_video_interactive(story=selected_stories[0], duration_key=duration_key, allow_split=allow_split)
                elif action == 'b':
                    break
                elif action == 'q':
                    sys.exit(0)
                else:
                    print("Invalid option.")
            # After returning from post-view, go back to same page
            continue
        else:
            # Multiple stories selected - go straight to batch generation
            print(f"\nBatch selection: {len(selected_stories)} stories")
            for i, s in enumerate(selected_stories, 1):
                print(f"  {i}. {s['title'][:50]}...")
            generate_video_interactive(stories=selected_stories, duration_key=duration_key, allow_split=allow_split)
            # After batch render, return to list
            continue


def list_all_subreddits():
    """Display all subreddits and their tags."""
    config = load_subreddit_config()
    
    print("\n" + "="*50)
    print("ALL SUBREDDITS")
    print("="*50)
    
    for subreddit, tags in sorted(config.items()):
        print(f"r/{subreddit:20} - tags: {', '.join(tags)}")


def search_subreddit():
    """Search a specific subreddit directly."""
    subreddit_name = input("Enter subreddit name (without r/): ").strip()
    
    if not subreddit_name:
        print("Invalid subreddit name.")
        return
    
    try:
        subreddit = reddit.subreddit(subreddit_name)
        posts = list(subreddit.hot(limit=15))
        
        stories = []
        for post in posts:
            if not post.selftext or len(post.selftext) < 100:
                continue
            
            virality_score, components = calculate_virality_score(post)
            stories.append({
                'title': post.title,
                'body': post.selftext[:200] + "..." if len(post.selftext) > 200 else post.selftext,
                'full_body': post.selftext,
                'subreddit': subreddit_name,
                'author': str(post.author),
                'score': post.score,
                'upvote_ratio': post.upvote_ratio,
                'num_comments': post.num_comments,
                'url': f"https://reddit.com{post.permalink}",
                'created_utc': post.created_utc,
                'virality_score': virality_score,
                'virality_breakdown': components,
                'tags': load_subreddit_config().get(subreddit_name, [])
            })
        
        stories.sort(key=lambda x: (x['virality_score'], x['score']), reverse=True)
        top_stories = display_stories(stories)
        
        if top_stories:
            story = select_story_interactive(top_stories)
            if story:
                view_full_story(story)
    
    except Exception as e:
        print(f"Error accessing r/{subreddit_name}: {e}")


def generate_video_interactive(story=None, stories=None, duration_key=None, allow_split=False):
    """Interactive video generation flow: pick voice, background, render.
    If duration_key is provided (pre-selected at browse time), skip asking again.
    If stories is provided (list), render batch; otherwise render single story.
    """
    print("\n" + "="*60)
    print("VIDEO GENERATOR")
    print("="*60)

    # Determine if batch or single
    is_batch = stories is not None
    if is_batch:
        stories_to_render = stories
        print(f"\nBatch mode: {len(stories_to_render)} stories selected")
    else:
        stories_to_render = [story]
        # Estimate story length upfront
        import re as _re
        raw_text = f"{story['title']}. {story['full_body']}"
        raw_text = _re.sub(r'\*+|#+\s*|\[.*?\]\(.*?\)', '', raw_text)
        raw_text = _re.sub(r'\n+', ' ', raw_text).strip()
        estimated_secs = estimate_duration_seconds(raw_text)
        estimated_mins = estimated_secs / 60
        print(f"\nEstimated story length: ~{estimated_mins:.1f} minutes ({int(estimated_secs)}s)")

    # Duration mode — use pre-selected if available, otherwise ask
    if duration_key and duration_key in DURATION_MODES:
        max_seconds = DURATION_MODES[duration_key]['max_seconds']
        allow_split = DURATION_MODES[duration_key]['allow_split']
        print(f"Duration mode: {DURATION_MODES[duration_key]['label']} (pre-selected)")
    else:
        print("\nDuration Mode:")
        print("  1. Under 2 minutes  (1 video, story trimmed to fit)")
        print("  2. Under 3 minutes  (1 video, story trimmed to fit)")
        print("  3. Under 5 minutes  (splits into multiple parts if needed)")
        duration_key = input("\nSelect duration mode (1-3, default 1): ").strip() or "1"
        if duration_key not in DURATION_MODES:
            duration_key = "1"
        max_seconds = DURATION_MODES[duration_key]['max_seconds']
        allow_split = DURATION_MODES[duration_key]['allow_split']
        print(f"Selected: {DURATION_MODES[duration_key]['label']}")

    # Step 2: Pick a voice
    print("\nAvailable Voices:")
    for key, v in VOICE_OPTIONS.items():
        print(f"  {key}. {v['name']}")
    voice_key = input("\nSelect voice (1-14, default 1): ").strip() or "1"
    if voice_key not in VOICE_OPTIONS:
        print("Invalid choice, defaulting to voice 1.")
        voice_key = "1"
    print(f"Selected: {VOICE_OPTIONS[voice_key]['name']}")

    # Step 3: Pick a background video
    bg_videos = list_background_videos()
    if not bg_videos:
        print("\nNo background videos found!")
        print("Drop an MP4 file into: backend/background_videos/")
        print("Then try again.")
        return

    print(f"\nAvailable Background Videos:")
    for i, v in enumerate(bg_videos, 1):
        print(f"  {i}. {v}")

    if len(bg_videos) == 1:
        bg_choice = "1"
        print(f"Auto-selected: {bg_videos[0]}")
    else:
        bg_choice = input(f"\nSelect background video (1-{len(bg_videos)}, default 1): ").strip() or "1"

    try:
        bg_filename = bg_videos[int(bg_choice) - 1]
    except (ValueError, IndexError):
        print("Invalid choice, using first video.")
        bg_filename = bg_videos[0]

    print(f"Selected: {bg_filename}")

    # Step 4: Confirm and render
    if is_batch:
        print(f"\nReady to generate batch:")
        print(f"  Stories:  {len(stories_to_render)} selected")
        print(f"  Voice:    {VOICE_OPTIONS[voice_key]['name']}")
        print(f"  Duration: {DURATION_MODES[duration_key]['label']}")
        print(f"  BG:       {bg_filename}")
        print(f"  Output:   output_videos/")
        confirm = input("\nStart batch rendering? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Cancelled.")
            return

        print("\nStarting batch render...")
        all_outputs = []
        for i, s in enumerate(stories_to_render, 1):
            print(f"\n[{i}/{len(stories_to_render)}] Rendering: {s['title'][:50]}...")
            outputs = generate_video(s, voice_key, bg_filename, max_seconds=max_seconds, allow_split=allow_split)
            if outputs:
                all_outputs.extend(outputs)
                print(f"  -> {len(outputs)} video(s) saved")
            else:
                print(f"  -> FAILED")
        
        if all_outputs:
            print(f"\nBatch complete! {len(all_outputs)} total video(s) saved:")
            for p in all_outputs:
                print(f"  {p}")
        else:
            print("\nBatch failed. Check errors above.")
    else:
        will_split = estimated_secs > max_seconds
        parts_count = int(estimated_secs // max_seconds) + 1 if will_split else 1
        print(f"\nReady to generate:")
        print(f"  Story:    {story['title'][:60]}")
        print(f"  Voice:    {VOICE_OPTIONS[voice_key]['name']}")
        print(f"  Duration: {DURATION_MODES[duration_key]['label']}")
        print(f"  Parts:    {parts_count} video{'s' if parts_count > 1 else ''}")
        print(f"  BG:       {bg_filename}")
        print(f"  Output:   output_videos/")
        confirm = input("\nStart rendering? (y/n): ").strip().lower()
        if confirm != 'y':
            print("Cancelled.")
            return

        print("\nGenerating video... (this may take a few minutes)")
        output_paths = generate_video(story, voice_key, bg_filename, max_seconds=max_seconds, allow_split=allow_split)

        if output_paths:
            print(f"\nDone! {len(output_paths)} video(s) saved:")
            for p in output_paths:
                print(f"  {p}")
        else:
            print("\nVideo generation failed. Check errors above.")


def save_story_to_file(story):
    """Save selected story to a text file."""
    filename = input("Enter filename (default: story.txt): ").strip() or "story.txt"
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"Title: {story['title']}\n")
            f.write(f"Subreddit: r/{story['subreddit']}\n")
            f.write(f"URL: {story['url']}\n")
            f.write(f"Author: u/{story['author']}\n")
            f.write(f"Virality Score: {story['virality_score']}/9\n")
            f.write(f"Upvotes: {story['score']}\n")
            f.write(f"Comments: {story['num_comments']}\n")
            f.write(f"Upvote Ratio: {story['upvote_ratio']*100:.1f}%\n")
            f.write("="*80 + "\n\n")
            f.write(story['full_body'])
        
        print(f"Story saved to {filename}")
    except Exception as e:
        print(f"Error saving file: {e}")


if __name__ == "__main__":
    try:
        browse_by_tag()
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)
