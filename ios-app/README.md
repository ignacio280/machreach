# MachReach — iOS app (Capacitor wrapper)

This folder turns the MachReach website into a real native iOS app that can be
submitted to the **App Store**. It wraps the live site in a native shell using
[Capacitor](https://capacitorjs.com/).

> ⚠️ **You must finish this on a Mac.** Compiling, signing, and submitting an iOS
> app requires **macOS + Xcode** and a paid **Apple Developer account**
> ($99/year). None of that can be done on Windows — this folder contains
> everything that *can* be prepared ahead of time (config, icons, splash, app
> metadata) so the Mac steps are quick.

---

## What's here

| File | Purpose |
|------|---------|
| `capacitor.config.json` | App id (`com.machreach.app`), name, and the live URL the app loads |
| `package.json` | Capacitor dependencies + helper scripts |
| `www/index.html` | Bundled fallback/launch screen (the app normally loads the live site) |
| `assets/icon.png` | 1024×1024 source app icon (used to generate every iOS icon size) |
| `assets/splash.png` / `splash-dark.png` | 2732×2732 launch screen source |

## How it works

The app loads the **live MachReach site** set in `capacitor.config.json` →
`server.url`. Update that to your production domain before building:

```json
"server": { "url": "https://YOUR-DOMAIN.com" }
```

Because it loads the real site, the app always reflects the deployed backend —
no rebuild needed when you ship web changes. (Internet is required; the bundled
`www/` screen shows briefly on launch and when offline.)

---

## Build & submit (on a Mac)

Prereqs: macOS, [Xcode](https://apps.apple.com/app/xcode/id497799835),
[Node.js](https://nodejs.org) 18+, CocoaPods (`sudo gem install cocoapods`), and
an Apple Developer account.

```bash
cd ios-app

# 1. Install Capacitor
npm install

# 2. Point the app at production (edit capacitor.config.json -> server.url)

# 3. Create the native iOS project
npx cap add ios

# 4. Generate all icon + splash sizes from assets/
npx capacitor-assets generate --ios

# 5. Sync config/assets into the native project
npx cap sync ios

# 6. Open in Xcode
npx cap open ios
```

Then in **Xcode**:
1. Select the **App** target → **Signing & Capabilities**.
2. Choose your **Team** (your Apple Developer account) and confirm the bundle
   identifier `com.machreach.app` (change it if it's taken).
3. Pick a real device or simulator and press **Run** to test.
4. To ship: **Product → Archive**, then **Distribute App → App Store Connect**.
5. Finish the listing (screenshots, description, privacy) in
   [App Store Connect](https://appstoreconnect.apple.com) and submit for review.

---

## Notes & gotchas

- **App Store guideline 4.2** discourages apps that are *only* a website
  wrapper. Lean on native value to pass review — e.g. add
  [`@capacitor/push-notifications`](https://capacitorjs.com/docs/apis/push-notifications)
  for study reminders, [`@capacitor/haptics`](https://capacitorjs.com/docs/apis/haptics),
  and a native splash/status bar. The PWA also exists, so this wrapper should add
  something the browser can't.
- **Focus Guard** is a Chrome extension and does **not** exist on iOS. Hide or
  replace that flow in the app (e.g. use iOS Screen Time / Shortcuts instead).
- **Push notifications** require an Apple Push Notification (APNs) key configured
  in App Store Connect + the push capability enabled in Xcode.
- Keep `appId` in sync with the bundle identifier you register in your Apple
  Developer account.
- After any change to `capacitor.config.json` or `assets/`, re-run
  `npx cap sync ios`.
