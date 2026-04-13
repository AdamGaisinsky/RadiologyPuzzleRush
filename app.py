import json
import os
import random
import time
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image

import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from streamlit_autorefresh import st_autorefresh


# =========================
# CONFIG
# =========================
IMAGE_FOLDER = "images"
ANNOTATION_FILE = "annotations.json"
PROGRESS_FILE = "progress.json"

SESSION_LENGTH_SECONDS = 20   # 3 minutes
TOLERANCE_PIXELS = 15          # forgiving click margin
FAST_RESPONSE_SECONDS = 5
MEDIUM_RESPONSE_SECONDS = 10


# =========================
# PAGE SETUP
# =========================
st.set_page_config(page_title="Radiology Puzzle Rush", layout="wide")
st.title("Radiology Puzzle Rush")


# =========================
# FILE HELPERS
# =========================
def load_annotations():
    with open(ANNOTATION_FILE, "r") as f:
        return json.load(f)


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return {}
    with open(PROGRESS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


# =========================
# SESSION STATE INIT
# =========================
def init_state():
    defaults = {
        "score": 0,
        "streak": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "attempted": 0,
        "clicked": None,
        "submitted": False,
        "feedback": None,
        "show_answer": False,
        "current_image": None,
        "session_started": False,
        "session_start_time": None,
        "session_end_time": None,
        "case_start_time": None,
        "seen_this_session": [],
        "missed_cases": [],
        "repeat_queue": [],
        "last_response_time": None,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


# =========================
# LOAD DATA
# =========================
annotations = load_annotations()
progress = load_progress()
image_files = list(annotations.keys())

if not image_files:
    st.error("No images found in annotations.json.")
    st.stop()


# =========================
# CASE SELECTION
# =========================
def current_allowed_difficulty():
    """Increase difficulty as session progresses."""
    if not st.session_state.session_started:
        return 1

    elapsed = time.time() - st.session_state.session_start_time
    fraction = elapsed / SESSION_LENGTH_SECONDS

    if fraction < 0.33:
        return 2   # easy start
    elif fraction < 0.66:
        return 3
    else:
        return 5   # allow all difficulties near the end


def get_due_cases():
    today = datetime.now().date()
    due_cases = []

    for image_name in image_files:
        case_progress = progress.get(image_name, {})
        next_due_str = case_progress.get("next_due")

        if not next_due_str:
            due_cases.append(image_name)
        else:
            try:
                next_due = datetime.strptime(next_due_str, "%Y-%m-%d").date()
                if next_due <= today:
                    due_cases.append(image_name)
            except ValueError:
                due_cases.append(image_name)

    return due_cases


def choose_next_case():
    allowed_difficulty = current_allowed_difficulty()

    # 1. If there are repeat cases from missed answers, prioritize them
    #if st.session_state.repeat_queue:
    #    return st.session_state.repeat_queue.pop(0)

    # 2. Prefer due cases
    #due_cases = get_due_cases()

    # 3. Filter by difficulty
    #candidates = [
    #    img for img in due_cases
    #    if annotations[img].get("difficulty", 1) <= allowed_difficulty
    #]

    # 4. Prefer unseen this session
    #unseen_candidates = [
    #    img for img in candidates
    #    if img not in st.session_state.seen_this_session
    #]

    #if unseen_candidates:
    #    return random.choice(unseen_candidates)

    #if candidates:
    #    return random.choice(candidates)

    # fallback
    #difficulty_fallback = [
    #    img for img in image_files
    #    if annotations[img].get("difficulty", 1) <= allowed_difficulty
    #]

    #if difficulty_fallback:
    #    return random.choice(difficulty_fallback)

    return random.choice(image_files)


def load_new_case():
    st.session_state.current_image = choose_next_case()
    st.session_state.clicked = None
    st.session_state.submitted = False
    st.session_state.feedback = None
    st.session_state.show_answer = False
    st.session_state.case_start_time = time.time()

    if st.session_state.current_image not in st.session_state.seen_this_session:
        st.session_state.seen_this_session.append(st.session_state.current_image)


# =========================
# SCORING / SRS
# =========================
def point_in_box(x, y, box, tolerance=TOLERANCE_PIXELS):
    x0 = box["x"] - tolerance
    y0 = box["y"] - tolerance
    x1 = box["x"] + box["width"] + tolerance
    y1 = box["y"] + box["height"] + tolerance
    return x0 <= x <= x1 and y0 <= y <= y1


def is_correct_click(x, y, boxes):
    for box in boxes:
        if point_in_box(x, y, box):
            return True
    return False


def update_spaced_repetition(image_name, was_correct, response_time):
    """Very simple beginner-friendly spaced repetition model."""
    today = datetime.now().date()
    case_progress = progress.get(image_name, {
        "repetitions": 0,
        "interval_days": 0,
        "ease": 2.5,
        "next_due": today.strftime("%Y-%m-%d")
    })

    repetitions = case_progress.get("repetitions", 0)
    interval_days = case_progress.get("interval_days", 0)
    ease = case_progress.get("ease", 2.5)

    if not was_correct:
        repetitions = 0
        interval_days = 0
        ease = max(1.3, ease - 0.2)
        next_due = today
    else:
        repetitions += 1

        if response_time <= FAST_RESPONSE_SECONDS:
            ease = min(3.0, ease + 0.1)
        elif response_time > MEDIUM_RESPONSE_SECONDS:
            ease = max(1.3, ease - 0.05)

        if repetitions == 1:
            interval_days = 1
        elif repetitions == 2:
            interval_days = 3
        else:
            interval_days = max(1, round(interval_days * ease))

        next_due = today + timedelta(days=interval_days)

    progress[image_name] = {
        "repetitions": repetitions,
        "interval_days": interval_days,
        "ease": round(ease, 2),
        "next_due": next_due.strftime("%Y-%m-%d")
    }

    save_progress(progress)


# =========================
# PLOTTING
# =========================
def draw_answer_overlay(image, boxes, clicked=None):
    fig, ax = plt.subplots()
    ax.imshow(image)

    for box in boxes:
        rect = patches.Rectangle(
            (box["x"], box["y"]),
            box["width"],
            box["height"],
            linewidth=2,
            edgecolor="red",
            facecolor="none"
        )
        ax.add_patch(rect)

    if clicked:
        ax.plot(clicked["x"], clicked["y"], marker="o", markersize=8)

    ax.axis("off")
    return fig


# =========================
# SESSION CONTROL
# =========================
def start_session():
    st.session_state.score = 0
    st.session_state.streak = 0
    st.session_state.correct_count = 0
    st.session_state.wrong_count = 0
    st.session_state.attempted = 0
    st.session_state.clicked = None
    st.session_state.submitted = False
    st.session_state.feedback = None
    st.session_state.show_answer = False
    st.session_state.session_started = True
    st.session_state.session_start_time = time.time()
    st.session_state.session_end_time = time.time() + SESSION_LENGTH_SECONDS
    st.session_state.seen_this_session = []
    st.session_state.missed_cases = []
    st.session_state.repeat_queue = []
    st.session_state.last_response_time = None
    load_new_case()


def end_session():
    st.session_state.session_started = False


# =========================
# START SCREEN
# =========================
if not st.session_state.session_started:
    st.subheader("Start a 3-minute training session")
    st.write("Click the abnormality as quickly and accurately as you can.")
    if st.button("Start Session"):
        start_session()
        st.rerun()
    st.stop()


# =========================
# AUTO REFRESH TIMER
# =========================
st_autorefresh(interval=1000, key="timer_refresh")

remaining = int(st.session_state.session_end_time - time.time())

if remaining <= 0:

    st.header("Session Complete")
    st.write(f"**Score:** {st.session_state.score}")
    st.write(f"**Correct:** {st.session_state.correct_count}")
    st.write(f"**Incorrect:** {st.session_state.wrong_count}")
    st.write(f"**Best streak:** {st.session_state.streak if st.session_state.correct_count > 0 else 0}")

    if st.session_state.attempted > 0:
        accuracy = 100 * st.session_state.correct_count / st.session_state.attempted
        st.write(f"**Accuracy:** {accuracy:.1f}%")

    if st.session_state.missed_cases:
        st.subheader("Missed cases this session")
        for missed in sorted(set(st.session_state.missed_cases)):
            st.write(f"- {missed}: {annotations[missed]['label']}")

    if st.button("Start New Session"):
        end_session()

    st.stop()


# =========================
# TOP STATS
# =========================
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Time Left", f"{remaining}s")
col2.metric("Score", st.session_state.score)
col3.metric("Streak", st.session_state.streak)
col4.metric("Correct", st.session_state.correct_count)
col5.metric("Incorrect", st.session_state.wrong_count)


# =========================
# CURRENT CASE
# =========================
if st.session_state.current_image is None:
    load_new_case()

image_name = st.session_state.current_image
case_data = annotations[image_name]
label = case_data.get("label", "abnormality")
difficulty = case_data.get("difficulty", 1)
boxes = case_data.get("boxes", [])

image_path = os.path.join(IMAGE_FOLDER, image_name)

if not os.path.exists(image_path):
    st.error(f"Image file not found: {image_path}")
    st.stop()

image = Image.open(image_path)

st.subheader(f"Case: {image_name}")
st.write(f"**Difficulty:** {difficulty}")


# =========================
# CLICKABLE IMAGE
# =========================
coords = None
coords = streamlit_image_coordinates(image, key=f"img_{image_name}")

if coords is not None and not st.session_state.submitted:
    st.session_state.clicked = coords

if st.session_state.clicked is not None:
    st.write(f"Clicked at: ({st.session_state.clicked['x']}, {st.session_state.clicked['y']})")


# =========================
# SUBMIT ANSWER
# =========================
#if st.button("Submit Answer", disabled=st.session_state.submitted):
    if st.session_state.clicked is None:
        st.warning("Please click on the image.")
    else:
        x = st.session_state.clicked["x"]
        y = st.session_state.clicked["y"]
        response_time = time.time() - st.session_state.case_start_time
        st.session_state.last_response_time = response_time

        correct = is_correct_click(x, y, boxes)

        st.session_state.attempted += 1
        st.session_state.submitted = True
        #st.session_state.show_answer = True

        st.session_state.clicked = None

        #update_spaced_repetition(image_name, correct, response_time)

        if correct:
            st.session_state.score += 1
            st.session_state.correct_count += 1
            st.session_state.streak += 1
            st.session_state.feedback = f"✅ Correct"
            correct = False
        else:
            st.session_state.wrong_count += 1
            st.session_state.streak = 0
            st.session_state.feedback = f"❌ Incorrect"
            st.session_state.missed_cases.append(image_name)

            # Re-show later in session
            #if image_name not in st.session_state.repeat_queue:
            #    st.session_state.repeat_queue.append(image_name)


# =========================
# FEEDBACK + OVERLAY
# =========================
if st.session_state.feedback:
    st.toast(st.session_state.feedback)

#if st.session_state.show_answer:
#    st.pyplot(draw_answer_overlay(image, boxes, st.session_state.clicked))


# =========================
# NEXT CASE
# =========================
if st.session_state.submitted:
    #if st.button("Next Case"):
    load_new_case()
    #st.rerun()


# =========================
# OPTIONAL SIDEBAR INFO
# =========================
#with st.sidebar:
#    st.header("Settings / Info")
#    st.write(f"Tolerance: {TOLERANCE_PIXELS} px")
#    st.write("Difficulty increases as the session goes on.")
#    st.write("Missed cases return later in the same session.")
#    st.write("Progress is saved in progress.json.")
