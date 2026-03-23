# GhostPort Android App (TWA)

Build an Android APK using Bubblewrap (Google's TWA tool).

## Prerequisites
- Node.js 18+
- Java JDK 11+
- Android SDK (or use Android Studio)

## Build Steps

```bash
# Install bubblewrap
npm install -g @nicedoc/bubblewrap

# Initialize TWA project
bubblewrap init --manifest="http://192.168.50.1:4200/manifest.json"

# Build the APK
bubblewrap build

# Output: app-release-signed.apk
```

## Alternative: PWA Builder
1. Go to https://www.pwabuilder.com
2. Enter your GhostPort URL
3. Click "Package for stores"
4. Download the Android package

## Notes
- The app requires the user to be on the GhostPort WiFi network
- TWA shows the web UI in a Chrome Custom Tab with no browser chrome
- Digital Asset Links verification is not needed for side-loading
