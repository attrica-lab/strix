---
name: electron-desktop-apps
description: Security testing for Electron and other web-tech desktop apps covering the renderer-to-native trust boundary, preload/IPC bridge exposure, top-level navigation escape, custom protocol/deep-link handlers, auto-update, and node/context isolation misconfiguration
---

# Electron / Web-Tech Desktop Apps

Use this skill when the target is a desktop app built on Electron (or a similar Chromium+Node/webview stack: NW.js, CEF, Tauri-with-Node, wails). These apps look native but much of the UI is web content, so their security model reduces to one question: **which web content is allowed to talk to the native side, and does that trust survive navigation?**

Electron's model is *positional*: native capability follows the window, and it only holds while that window stays on trusted content. Pair `xss` (to get script into the renderer), `browser_security` (context/navigation state machine), `argument_injection` (native subprocess launches), and `insecure_deserialization`/`rce` when a native handler is the final sink.

## Threat Model

The prize is moving attacker-controlled JavaScript into a **preload-bearing (privileged) renderer**, then using the bridge that renderer already has. Full Node integration is *not* required — inheriting an existing IPC bridge is enough.

```text
untrusted content in first-party UI (display name, notification, note title, avatar)
  -> renders as live link / injected markup in trusted renderer
  -> top-level navigation to attacker origin (bridge NOT dropped)
  -> window.electron / ipcInvoke reachable from attacker page
  -> privileged IPC channels: session tokens, sqlite port, screenshot/webcam, fs
  -> account takeover + local desktop foothold
```

## Recon: Unpack and Map the Native Surface

1. Extract the app bundle: locate and unpack `app.asar` (`npx @electron/asar extract app.asar out/`, or `asar`), or read the plain `resources/app` directory. Grab `package.json` (`main` entry) and the Electron version.
2. Find every `BrowserWindow`/`BrowserView`/`webContents` creation and record its `webPreferences`:
   - `nodeIntegration`, `contextIsolation`, `sandbox`, `nodeIntegrationInSubFrames`, `webSecurity`, `allowRunningInsecureContent`, `preload`.
3. Read each `preload` script: what does it expose via `contextBridge.exposeInMainWorld` / on `window`? Is it a **narrow typed API** or a **generic IPC pass-through** (`ipcInvoke`/`ipcSend`/`ipcOn` with caller-chosen channel names)?
4. Inventory `ipcMain.handle`/`ipcMain.on` channels — this is the *real* capability list. Note sensitive ones: session/token get/set, DB key or port, `shell.openExternal`/`openPath`, fs read/write, screenshot/media, child_process/exec, auto-update triggers.
5. Map navigation guards: `setWindowOpenHandler`, and handlers for `will-navigate`, `will-redirect`, `will-attach-webview`, `web-contents-created`. Note custom protocol registration (`protocol.register*`, `app.setAsDefaultProtocolClient`) and deep-link handling (`open-url`, second-instance argv).

## High-Value Weaknesses

### Top-Level Navigation Escape (the crack)

The most impactful and most common gap: apps guard *new windows* (`setWindowOpenHandler` denies popups / routes to system browser) but leave the **primary window's top-level navigation** unguarded. Because the preload bridge is attached to the window — granted once, not per-URL — navigating that window to `https://attacker.example` carries `window.electron` along.

- Test whether any in-app action, link, or redirect can move a preload-bearing window off the trusted origin (`app://`, `file://`, first-party https).
- The correct fix (use as an oracle): deny-by-default on `will-navigate` **and** `will-redirect`, allowlisting only trusted origins and pushing everything else to `shell.openExternal`.

```javascript
webContents.on('will-navigate', (event, url) => {
  if (!url.startsWith('app://ui/')) {
    event.preventDefault();
    if (url.startsWith('https:') || url.startsWith('mailto:')) shell.openExternal(url);
  }
});
// Same guard required for 'will-redirect'.
```

### Untrusted Content Rendered as Trusted UI

First-party UI chrome is not automatically trusted input. Display names, activity-feed/notification entries, meeting/note titles, avatars, and chat messages are attacker-writable and can carry markup or markdown links that become the navigation trigger inside the privileged renderer.

- Enumerate every field another user (or a lower-trust source) can control that renders in a privileged window.
- Test link/markup sanitization on *each* surface separately; a body may be guarded while display names are not.
- Watch for encoding bypasses of URL defanging, e.g. **markdown with an HTML-entity-encoded scheme colon** rendering as a live link after raw `javascript:`/`https:` is stripped.

### Generic IPC Pass-Through Bridge

If the preload exposes `ipcInvoke(channel, ...)` with arbitrary channel names, the effective gate is the `ipcMain` handler list, not any renderer-side allowlist (unknown channels merely return "no handler registered"). A compromised renderer can then call any registered channel.

- Enumerate reachable channels from renderer JS; probe sensitive ones (`get-session`, `get-refreshed-access-token`, `set-tokens`, `get-stored-accounts`, `sqlite:port`, screenshot/system).
- Correct design (oracle): a narrow, explicit, typed API with authorization enforced on the **main-process** side, not a channel-name pass-through.

### Node / Context Isolation Misconfiguration

- `nodeIntegration: true` or `contextIsolation: false` on any window that can render remote/untrusted content = direct RCE; check every window, webview, and child frame, not just the main one.
- `sandbox: false` + a leaky preload can expose Node primitives even with contextIsolation on.
- `webview`/`<webview>` tags and `nodeIntegrationInSubFrames` re-open the boundary inside frames.

### Custom Protocols, Deep Links, and Auto-Update

- Custom scheme / deep-link handlers (`myapp://…`, `open-url`, second-instance `argv`) accept OS-level attacker input; test for path traversal, argument injection into a launched process (load `argument_injection`), and navigation into privileged windows.
- Auto-update: verify update feed is HTTPS + signature-checked; an unauthenticated/naively-parsed feed is native RCE.

### Renderer-Exposed Secrets

- Encryption keys must not live in renderer reach. If the app exports a DB key (e.g. **SQLCipher key** stored in IndexedDB / handed to renderer JS), at-rest encryption does not survive renderer compromise. Check what secrets IndexedDB/localStorage/JS globals hold once you have renderer execution.

## Safe Validation

- Run the packaged app under a local Electron runtime in an isolated VM with synthetic accounts/data. Do not exfiltrate real user data.
- Prove the **chain**, not just a finding: "a bridge exists" ≠ "an attacker can reach it." Show injected content → off-origin navigation with `window.electron` still present → a benign privileged IPC call (e.g. a read-only channel returning a canary), captured on video/trace.
- Use the least sensitive channel that demonstrates authority; avoid pulling real tokens or invoking media capture beyond what proves reachability.
- Record Electron version and exact `webPreferences`; behavior and defaults change across major versions.

## False Positives

- `nodeIntegration:false` + `contextIsolation:true` reported as "safe" without checking whether navigation can carry the bridge off-origin.
- A dangerous IPC channel exists but no untrusted content can reach a preload-bearing renderer (no navigation escape, no injection surface).
- `setWindowOpenHandler` present, cited as full coverage, while `will-navigate`/`will-redirect` are unguarded (or vice versa).
- Remote content loaded in a window that genuinely has no preload and no node access.
- A deep-link handler that only routes to in-app views with no argv/navigation/traversal effect.

## Pro Tips

1. Capability follows the window — always ask whether a privileged window can wander off trusted content.
2. Treat every user-writable string that renders in first-party UI as attacker-controlled.
3. The handler list is the real ACL for a generic bridge; enumerate `ipcMain` channels, not the preload's intent.
4. Check *every* window/webview/subframe's `webPreferences`, not just the main window.
5. Severity comes from the full path; a video of one-click injection → retained bridge → privileged call is worth more than a config screenshot.

## Summary

Electron security is positional trust: native capability rides the window and holds only while the window stays on trusted content. Map `webPreferences`, the preload bridge, and `ipcMain` channels; then hunt for a way to get untrusted JS into a preload-bearing renderer — most often an unguarded top-level navigation triggered by attacker-controlled first-party UI. Prove the whole chain with the least powerful privileged call.
