import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: patch_release.py VERSION_CODE VERSION_NAME")

version_code = int(sys.argv[1])
version_name = sys.argv[2]

gradle = Path("android/app/build.gradle")
text = gradle.read_text()

text = re.sub(
    r'versionCode\s+\d+',
    f'versionCode {version_code}',
    text,
    count=1,
)
text = re.sub(
    r'versionName\s+"[^"]+"',
    f'versionName "{version_name}"',
    text,
    count=1,
)

if "SVOI_KEYSTORE_PATH" not in text:
    android_pos = text.find("android {")
    if android_pos == -1:
        raise SystemExit("android block not found")

    insert_pos = text.find("\n", android_pos) + 1
    signing = r'''
    signingConfigs {
        release {
            storeFile file(System.getenv("SVOI_KEYSTORE_PATH"))
            storePassword System.getenv("SVOI_KEYSTORE_PASSWORD")
            keyAlias System.getenv("SVOI_KEY_ALIAS")
            keyPassword System.getenv("SVOI_KEY_PASSWORD")
        }
    }
'''
    text = text[:insert_pos] + signing + text[insert_pos:]

    release_marker = "release {"
    release_pos = text.find(release_marker)
    if release_pos == -1:
        raise SystemExit("release build type not found")
    brace_end = text.find("\n", release_pos) + 1
    text = (
        text[:brace_end]
        + "            signingConfig signingConfigs.release\n"
        + text[brace_end:]
    )

gradle.write_text(text)
print(f"Release configured: {version_name} ({version_code})")
