# Prateek's Photo Sorter: user guide

Sort a messy folder of photos and videos into neat folders while you watch. Claude looks at each photo and picks a folder for it: people, food, travel, nature, documents and screenshots, pets, or other. Exact copies go into their own Duplicates folder. Nothing moves until you say so, and one click puts everything back.

**Setup takes about 10 minutes, once.** After that, it's one command and a few key presses.

---

## What you need

- **A Mac.** It doesn't work on Windows or Linux.
- **A paid Claude plan (Pro or Max) with Claude Code signed in.** Sorting uses your plan's normal usage. No API key, and no extra bill.
- **Homebrew, Python 3.11 or newer, and ffmpeg.** Steps 1 and 2 install them.

---

## Set up (once)

### Step 1: Install Homebrew
Homebrew installs free Mac tools from Terminal.
1. Open **Terminal**: press `Cmd + Space`, type `Terminal`, then press `Return`.
2. Go to **[brew.sh](https://brew.sh)**, copy the install command at the top, paste it into Terminal, and press `Return`.
3. Type your Mac password when it asks. Nothing shows as you type; that's normal.
4. When it finishes, run the two commands it prints under **"Next steps"**.

Already have Homebrew? Skip to step 2.

### Step 2: Install Python and ffmpeg
Paste this into Terminal and press `Return`:
```bash
brew install python ffmpeg
```

### Step 3: Install Claude Code and sign in
1. Install Claude Code by following **[claude.com/claude-code](https://claude.com/claude-code)**.
2. In Terminal, type `claude` and press `Return`. Sign in with your Claude account when it asks.
3. Once you're signed in, type `/exit` and press `Return`.

### Step 4: Download Photo Sorter
1. On this page, click the green **Code** button, then **Download ZIP**.
2. Open your **Downloads** folder and double-click the ZIP to unzip it.
3. Move the unzipped folder somewhere you'll find it again, for example your **Documents** folder.

---

## Sort a folder

### Step 5: Start the app
1. Open **Terminal**.
2. Type `cd ` with a space after it, drag the Photo Sorter folder into the Terminal window, and press `Return`.
3. Type this and press `Return`:
   ```bash
   python3 photo_sorter.py
   ```
4. Your browser opens the app. Keep the Terminal window open while you use it. Closing Terminal stops the app.

> If the browser doesn't open, copy the link Terminal printed (it starts with `http://127.0.0.1`) and paste it into your browser.

### Step 6: Pick a folder
- Click **Choose folder** and pick the messy folder, or paste its path into the box and press `Return`.
- It uses the photos and videos directly inside that folder, up to 1,500. Files inside subfolders are left alone.
- If your Mac asks whether Terminal may access the folder or drive, click **Allow**.

All your photos gather into a ball. **Nothing has moved yet.**

### Step 7: Sort
- Press `Space`, or click **Sort**.
- Photos fly into folders as Claude sorts them, with a coin "ka-ching" for each one.
- About 1,000 photos take 1–3 minutes, depending on how busy Claude is.
- **Still nothing has moved on your Mac.**

### Step 8: Check the folders
- Click any folder card to see what's in it. Press `Esc` to close.
- Don't like the result? Click **Choose another folder** to start over. Nothing changed on your Mac.

### Step 9: Move the files
- Press `Space` again, or click **Apply**.
- Your files now move into folders **inside the same folder** you picked, such as `Food` and `Pets`.

### Step 10: Changed your mind?
- Click **Put everything back**. Every file returns to where it was.
- This works even if you close the app and come back later. Start the app, pick the same folder, and click **Put everything back**.

---

## Keys

| Key | What it does |
|---|---|
| `Space` | Next step: Sort, then Apply |
| `Esc` | Close a folder preview, or leave full screen |
| `F` | Full screen with no buttons, a 3 · 2 · 1 countdown, then the photos fly in (for recording) |
| `G` | Replay the photos flying in |
| ♪ button | Sound on or off |

---

## Recording a video of it

1. Start a screen recorder that captures computer sound, such as **OBS** (free). The Mac's built-in screen recording (`Cmd + Shift + 5`) doesn't record computer sound.
2. Open the app's link, or reload the page. It counts down **3 · 2 · 1**, then the photos fly in from everywhere.
3. Press `Space` to sort. The coins play as each photo lands.
4. For another take, click **Put everything back**, then reload the page.

---

## Privacy and safety

- **Your files are never deleted or overwritten.** Everything stays inside the folder you picked.
- **Only small previews leave your Mac**, sent to Claude through your own account to be sorted. Location and other hidden photo details are removed first. File names and full-size photos never leave your Mac.
- **Claude can only pick a folder name.** It can't open, change or delete anything, and text inside a photo can't instruct it.
- **The app only works on your own Mac.** Other websites and other computers can't reach it.
- A hidden `.organizer-runs` folder inside your picked folder remembers the moves so **Put everything back** works. Delete it once you're happy with the result.

---

## If something goes wrong

| You see | What to do |
|---|---|
| **"Claude is busy and took too long to answer"** | Wait a minute, then click **Try again**. Nothing was moved. |
| **"Claude didn't answer. Check it's signed in"** | In Terminal, run `claude` and sign in again, then click **Try again**. |
| **"couldn't be previewed and will stay where they are"** | Those files are left exactly where they are. Everything else sorts normally. |
| **"The app has stopped. Start it again."** | Run `python3 photo_sorter.py` again. Each start prints a new link. |
| **"Before this can run, this Mac needs…"** | Install what it lists. See steps 1–3. |

---

## Good to know

- Folders are fixed: Selfies and people, Food, Travel and places, Nature, Documents and screenshots, Pets, Other, and Duplicates.
- Supported files: JPG, PNG, HEIC, WebP, GIF, TIFF, MOV, MP4 and M4V.
- Sorting 1,000 photos uses about 40 requests from your Claude plan.
- To remove Photo Sorter, move its folder to the Trash.

---

## For developers

Three files make the app: `engine.py` (scan, thumbnails, Claude, move, undo and the local server), `photo_sorter.py` (the folders and file types) and `page.html` (the screen). Run the tests with:
```bash
python3 -m unittest test_engine
```
Released under the MIT licence. See `LICENSE`.
