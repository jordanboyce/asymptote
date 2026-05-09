# Electron app icons

Platform icons used by `electron-builder` and Electron's `BrowserWindow` /
`Tray` APIs.

| File | Used by | Source |
|------|---------|--------|
| [icon.ico](icon.ico) | Windows installer + taskbar (multi-res 16/32/48/64/128/256) | generated |
| [icon.png](icon.png) | Linux AppImage, macOS tray fallback (512×512) | generated |
| [icon.icns](icon.icns) | macOS dmg + dock (multi-res up to 1024×1024) | generated |

All three are **generated** from [frontend/public/icon_light.png](../../frontend/public/icon_light.png)
(the dark/visible robot — picked for contrast against typical dock and taskbar
backgrounds). Re-run after updating the source PNG:

```bash
python electron/assets/generate_icons.py
```

The script lives at [generate_icons.py](generate_icons.py); change the
`SOURCE` line if you want to feed a different file (e.g. `icon_dark.png` for
the light/gray variant).

## Optional: dedicated macOS tray icon

`tray-icon.png` is *not* checked in. If you want a proper macOS menu-bar tray
icon (template-style, 22×22 or 44×44 monochrome with transparency), drop it
here and `getTrayIcon()` in [../main.js](../main.js) will pick it up
automatically. Without it, the tray falls back to `icon.png`.
