# Prateek's Photo Sorter and File Sorter

Two tools that tidy a mess while you watch:

- **Photo Sorter** sorts photos and videos into folders such as people, food, travel, nature, documents and screenshots, and pets. Claude looks at each photo and picks its folder. Exact copies go into their own Duplicates folder.
- **File Sorter** sorts every kind of file into folders by type: documents, spreadsheets, slides, images, videos, audio, zip files, installers, code, screenshots, and other. It works entirely on your Mac and never uses Claude.

Both work on **one folder you pick** or on **files all over your computer**. Nothing moves until you say so, and one click puts everything back.

**Mac only for now.** Windows isn't supported yet.

**Setup takes about 10 minutes, once.**

---

## What you need

- **A Mac.**
- **For Photo Sorter only:** a paid Claude plan (Pro or Max) with Claude Code signed in. Sorting uses your plan's normal usage. No API key, and no extra bill. File Sorter doesn't need Claude.
- **Homebrew, Python 3.11 or newer, and ffmpeg.** Steps 1 and 2 install them.

---

## Set up (once)

### Step 1: Install Homebrew
Homebrew installs free Mac tools from Terminal.
1. Open **Terminal**: press `Cmd + Space`, type `Terminal`, then press `Return`.
2. Go to **[brew.sh](https://brew.sh)**, copy the install command at the top, paste it into Terminal, and press `Return`.
3. Type your Mac password when it asks. Nothing shows as you type; that's normal.
4. When it finishes, run the commands it prints under **"Next steps"**.

Already have Homebrew? Skip to step 2.

### Step 2: Install Python and ffmpeg
Paste this into Terminal and press `Return`:
```bash
brew install python ffmpeg
```

### Step 3: Install Claude Code and sign in (Photo Sorter only)
1. Install Claude Code by following **[claude.com/claude-code](https://claude.com/claude-code)**.
2. In Terminal, type `claude` and press `Return`. Sign in with your Claude account when it asks.
3. Once you're signed in, type `/exit` and press `Return`.

### Step 4: Download the tools
1. On this page, click the green **Code** button, then **Download ZIP**.
2. Open your **Downloads** folder and double-click the ZIP to unzip it.
3. Move the unzipped folder somewhere you'll find it again, for example your **Documents** folder.

---

## Use it

### Step 5: Start a tool
1. Open **Terminal**.
2. Type `cd ` with a space after it, drag the unzipped folder into the Terminal window, and press `Return`.
3. Type one of these and press `Return`:
   ```bash
   python3 photo_sorter.py
   ```
   ```bash
   python3 file_sorter.py
   ```
4. Your browser opens the tool. Keep Terminal open while you use it. Closing Terminal stops the tool.

> If the browser doesn't open, copy the link Terminal printed (it starts with `http://127.0.0.1`) and paste it into your browser.

### Step 6: Choose what to sort
Pick one of the two buttons:

- **Pick a folder to tidy:** choose one messy folder. The tool uses the files directly inside it, up to 1,500. Files inside its subfolders are left alone.
- **Find photos (or files) all over my computer:** it collects the loose files at the top of your **Desktop, Downloads, Documents, Pictures, Movies and Music** folders. It never touches your Photos library, apps or system files.

**Where sorted files go:** under **Sorted photos go to**, click **Choose…** to pick a destination folder. If you don't, they go into new folders inside the folder you picked.

If your Mac asks whether Terminal may access a folder or drive, click **Allow**.

**Nothing has moved yet.**

### Step 7: Sort
- Press `Space`, or click **Sort**.
- Files fly into folders on screen, with a coin "ka-ching" for each one. The ♪ button turns sound off.
- With Photo Sorter, about 1,000 photos take 1–3 minutes, depending on how busy Claude is. File Sorter is almost instant.
- **Still nothing has moved on your Mac.**

### Step 8: Check the folders
- Click any folder card to see what's in it. Press `Esc` to close.
- Don't like the result? Click **Choose another folder** to start over. Nothing changed on your Mac.

### Step 9: Move the files
- Press `Space` again, or click **Apply**.
- When files are coming from all over your computer, a card first shows exactly where they'll go. Confirm it to continue.
- Your files now move into the new folders.

### Step 10: Changed your mind?
- Click **Put everything back**. Every file returns to exactly where it was, including files collected from all over your computer.
- This works even if you close the tool and come back later. Start it, pick the folder the files were sorted into, and click **Put everything back**.

---

## Privacy and safety

- **Your files are never deleted or overwritten.** Every move can be undone.
- **Photo Sorter sends only small previews** to Claude through your own account. Location and other hidden photo details are removed first. File names and full-size photos never leave your Mac.
- **File Sorter sends nothing anywhere.** It sorts by file type on your Mac.
- **Claude can only pick a folder name.** It can't open, change or delete anything, and text inside a photo can't instruct it.
- **The tools only work on your own Mac.** Other websites and other computers can't reach them.
- A hidden `.organizer-runs` folder remembers the moves so **Put everything back** works. Delete it once you're happy with the result.

---

## If something goes wrong

| You see | What to do |
|---|---|
| **"Claude is busy and took too long to answer"** | Wait a minute, then click **Try again**. Nothing was moved. |
| **"Claude didn't answer. Check it's signed in"** | In Terminal, run `claude` and sign in again, then click **Try again**. |
| **"couldn't be previewed and will stay where they are"** | Those files are left exactly where they are. Everything else sorts normally. |
| **"The app has stopped. Start it again."** | Run the start command again. Each start prints a new link. |
| **"Before this can run, this Mac needs…"** | Install what it lists. See steps 1–3. |

---

## Good to know

- **Photo Sorter folders:** Selfies and people, Food, Travel and places, Nature, Documents and screenshots, Pets, Other, and Duplicates. It handles JPG, PNG, HEIC, WebP, GIF, TIFF, MOV, MP4 and M4V.
- **File Sorter folders:** Documents, Spreadsheets, Slides, Images, Videos, Audio, Zip files, Installers, Code, Screenshots, and Other.
- Sorting 1,000 photos uses about 40 requests from your Claude plan.
- To remove the tools, move their folder to the Trash.

---

## For developers

The tools share `engine.py` (scan, previews, Claude, move, undo and the local server). `photo_sorter.py` and `file_sorter.py` define each tool, and `page.html` and `file_page.html` are their screens. Run the tests with:
```bash
python3 -m unittest test_engine
```
Released under the MIT licence. See `LICENSE`.
