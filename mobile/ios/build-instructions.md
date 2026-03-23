# GhostPort iOS App

## Option A: PWA (Recommended)
iOS Safari supports "Add to Home Screen" which creates an app-like experience:
1. Connect to GhostPort WiFi
2. Open Safari → http://192.168.50.1:4200
3. Tap Share → "Add to Home Screen"
4. The app icon appears on the home screen and opens in standalone mode

## Option B: App Store (via PWA Builder or Capacitor)
1. Go to https://www.pwabuilder.com
2. Enter your GhostPort URL
3. Package for iOS
4. Requires Apple Developer account ($99/year)
5. Submit to App Store for review

## Option C: Capacitor
```bash
npm install -g @capacitor/cli
npx cap init GhostPort com.ghostport.commanddeck --web-dir=public
npx cap add ios
npx cap open ios  # Opens Xcode
```

## Notes
- iOS PWA has full standalone mode support
- No push notifications on iOS PWA (Apple limitation)
- For App Store distribution, you need an Apple Developer account
