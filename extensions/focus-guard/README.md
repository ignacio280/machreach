# MachReach Focus Guard

Chrome/Edge extension that blocks distracting sites while you have a focus session active on [machreach.com](https://machreach.com).

## What it blocks

Instagram · TikTok · Twitter/X · Facebook · Reddit · Snapchat · Pinterest · Twitch · 9GAG · Netflix

## What stays allowed

**YouTube.** You might actually be studying.

## How it works

1. A content script on machreach.com reads whether a focus session is running.
2. A background service worker watches navigations and, if you try to open a blocked site while focused, sends you to a block page.
3. A 60-second safety timeout means if you close MachReach without ending the session, blocking releases automatically.

## Install (developer mode)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked**.
4. Select this `extensions/focus-guard/` folder.
5. Pin the extension so you can see its status icon.

Start a focus timer on MachReach → try opening instagram.com → you'll land on the block page. Stop the timer → everything unblocks.
