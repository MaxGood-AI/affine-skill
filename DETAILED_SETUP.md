# Detailed Setup Guide (for everyone, no technical background needed)

This guide takes you from **nothing** to **your AI agent being able to read, search, and write
your team's AFFiNE knowledge base**. It's written for non-technical users. Follow it top to
bottom — each step tells you exactly what to type and what you should see.

> **Time:** about 15–20 minutes, mostly downloading and installing.
> **Works on:** **Mac (macOS)** only, for now. Windows and Linux support is planned — see the
> repo's [Issues](https://github.com/MaxGood-AI/affine-skill/issues).
> **You'll use:** [Claude Code](https://claude.com/claude-code) as your AI agent (this guide
> assumes Claude Code).

---

## Before you start — get these two things from your team admin

You'll need them during setup. Write them down:

| Thing you need | Example | Yours |
|---|---|---|
| Your team's **AFFiNE server address** | `https://affine.yourteam.com` | ____________________ |
| Your **main workspace name** (exactly as it appears in AFFiNE) | `Team Docs` | ____________________ |
| Your **AFFiNE account** (email + how you sign in) | you@yourteam.com | ____________________ |

If you don't know these, ask whoever invited you to this skill.

---

## A quick note about the "Terminal"

A few steps use the **Terminal** — a plain text window where you type commands. Don't worry, you
only copy-paste and press Return. To open it:

1. Press **⌘ (Command) + Space** to open Spotlight.
2. Type **Terminal** and press **Return**.
3. A window opens. That's it — leave it open; you'll paste commands into it.

To run a command: click the Terminal window, paste the command, press **Return**. Wait for it to
finish (the prompt reappears) before the next one.

---

## Step 1 — Install the AFFiNE desktop app

1. Go to **https://affine.pro/download** and download the **macOS** version.
   It must be **version 0.27.3 or newer** (older versions have a sign-in bug that breaks syncing).
2. Open the downloaded file and drag **AFFiNE** into your **Applications** folder.
3. Open **AFFiNE** (from Applications or Launchpad).
   - If macOS says it "cannot be opened because it is from an unidentified developer," go to
     **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**.
4. **Sign in to your team's server:** in AFFiNE, look for the option to **add a self-hosted
   server** (usually near the workspace list or sign-in screen). Enter your team's **AFFiNE
   server address** from the table above, then sign in with your account (you'll either enter a
   password or get a one-time code by email).
5. Wait until your **documents and workspaces appear** and stop showing a "syncing" indicator.

> **⚠️ Very important — keep AFFiNE running:** After signing in, only ever **close the window**
> (the red dot, top-left). **Do NOT** use **"Quit AFFiNE Completely"** from the little AFFiNE
> icon in the top menu bar. Keeping AFFiNE running in the background is what keeps you signed in
> and syncing. If you fully quit it and it makes you sign in again next time, that's expected —
> just sign back in and keep it running.

---

## Step 2 — Install uv (one-time)

The skill uses a free tool called **uv**. You only install this one thing — uv takes care of
everything else the skill needs behind the scenes, including the right version of Python.

1. Open **Terminal** (see the note above) and run:
   ```
   uv --version
   ```
2. If you see a version number (for example `uv 0.11.32`), you're done — **skip to Step 3**.
3. If you see `command not found`, install uv by running this line in Terminal:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   - When it finishes, **close Terminal and open it again**, then re-run `uv --version` to
     confirm it now shows a version.
   - If you already use Homebrew, `brew install uv` works just as well.

---

## Step 3 — Download the skill

1. Open **https://github.com/MaxGood-AI/affine-skill** in your web browser.
2. Click the green **`< > Code`** button, then **Download ZIP**.
   - If GitHub asks you to sign in, you need access to the repository — ask your admin.
3. Open your **Downloads** folder and **double-click the ZIP** to unzip it. You'll get a folder
   named something like **`affine-skill-main`**.
4. **Rename it to `affine-skill`** and move it to your **Home folder**:
   - In Finder, press **⌘ + Shift + H** to open your Home folder.
   - Drag the `affine-skill-main` folder into it, then rename it to **`affine-skill`**
     (click the name once, wait, click again, type `affine-skill`, press Return).

You should now have a folder at **`~/affine-skill`** (the `~` means your Home folder).

> Prefer the command line? You can instead run `git clone https://github.com/MaxGood-AI/affine-skill.git`
> from your Home folder — same result.

---

## Step 4 — Connect the skill to your agent

This tells **Claude Code** where to find the skill. In **Terminal**, paste these two commands
**one at a time** (press Return after each):

```
mkdir -p ~/.claude/skills
```
```
ln -s ~/affine-skill ~/.claude/skills/affine
```

- The first makes the folder where skills live (if it isn't there already).
- The second creates a shortcut named **affine** that points at your downloaded folder.

**Check it worked** by running:
```
ls -l ~/.claude/skills/affine
```
You should see a line ending in `-> /Users/yourname/affine-skill`.

> Already have a skill named `affine`? Ask your admin — you may be re-installing over an old copy.

---

## Step 5 — Tell the skill your main workspace (optional but recommended)

Reading and searching look in **all** your workspaces automatically. This step only sets **where
new documents get created**. If you only have one workspace, you can skip it.

In **Terminal**, run this — replacing **`Team Docs`** with **your main workspace name**
(exactly as it appears in AFFiNE):

```
echo '{"default_workspace": "Team Docs"}' > ~/affine-skill/config.json
```

---

## Step 6 — First run (sets itself up, ~1 minute)

In **Terminal**, run:
```
~/affine-skill/affine workspaces
```

- The **first time only**, uv quietly fetches what the skill needs — this can take up to a
  minute and needs an internet connection. Please wait. Later runs start immediately.
- **Success looks like a list of your workspaces**, for example:
  ```
  cloud  a1b2c3...  Team Docs
  cloud  d4e5f6...  Projects
  local  g7h8i9...  Demo Workspace
  ```

If you see your workspaces, **everything is working!** 🎉 (See Troubleshooting below if not.)

---

## Step 7 — Use it with your agent

Open **Claude Code**. It automatically finds the skill. Now just ask in plain English, for example:

- *"Search our AFFiNE for our onboarding process and summarize it for me."*
- *"List the documents in our AFFiNE knowledge base."*
- *"Read the AFFiNE doc about our brand guidelines."*
- *"Create a new AFFiNE doc titled 'Meeting Notes — Friday' with these bullet points: …"*
- *"In AFFiNE, add a section to the Roadmap doc about Q4 priorities."*

**What to expect when it writes:** when you ask the agent to create, edit, or delete something,
it will briefly **quit and reopen the AFFiNE app** to save your change to the server. The AFFiNE
window may flash to the front for a few seconds — **this is normal and expected.**

---

## Everyday tips

- **Keep AFFiNE running** (close the window, never "Quit Completely"). This keeps you signed in.
- **Reading and searching** work any time and don't disturb the app.
- **Writing** briefly brings AFFiNE to the front to sync — let it finish (a few seconds).
- Your changes appear for the rest of your team once they sync, just like your own edits.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: uv` or `uv: No such file or directory` | Install uv — see **Step 2**. If you just installed it, close Terminal and open it again. |
| **"no AFFiNE workspaces found"** | Make sure AFFiNE is installed, **signed in**, and has finished syncing (Step 1). Then re-run Step 6. |
| The first run seems stuck | uv is downloading what it needs on first use — give it up to a minute with internet on. |
| Your agent doesn't seem to use the skill | Fully **quit and reopen Claude Code**, then try again. Re-check the shortcut with `ls -l ~/.claude/skills/affine` (Step 4). |
| Sync doesn't happen / it keeps asking you to sign in | Update AFFiNE to **v0.27.3 or newer** (Step 1), and remember to only close the window, never "Quit Completely." |
| macOS won't open AFFiNE | **System Settings → Privacy & Security → Open Anyway** (Step 1). |
| Terminal asks for your password during install | That's macOS confirming the install — type your Mac login password and continue. |
| You're on **Windows or Linux** | Not supported yet — follow the repo's [Issues](https://github.com/MaxGood-AI/affine-skill/issues) for progress. |

---

## Keeping the skill up to date

When a new version is released, either:

- **Re-download** the ZIP (Step 3) and replace the `~/affine-skill` folder (keep the same name and
  location so the shortcut from Step 4 still works), **or**
- if you used `git`, run `git pull` inside the `~/affine-skill` folder.

Your `config.json` and the shortcut stay in place.

---

## Getting help

Ask your team admin, or open an issue at
**https://github.com/MaxGood-AI/affine-skill/issues**.
