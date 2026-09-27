from pathlib import Path

manifest = Path("android/app/src/main/AndroidManifest.xml")
text = manifest.read_text()

permissions = """    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
"""

if "android.permission.CAMERA" not in text:
    marker = "<manifest"
    start = text.find(">", text.find(marker))
    if start == -1:
        raise SystemExit("AndroidManifest.xml: manifest tag not found")
    text = text[: start + 1] + "\n" + permissions + text[start + 1 :]

manifest.write_text(text)
print("Android permissions patched.")
